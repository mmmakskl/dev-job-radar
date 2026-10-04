"""Owner-scoped template search intents; the existing live worker runs the jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from tg_vacancy_bot.premium_search.templates import compose_query
from tg_vacancy_bot.telegram.candidate_card_formatter import format_legacy_card


class CandidateSearch:
    """Persist preview intents before sending buttons, then enqueue idempotently."""

    def __init__(
        self,
        api,
        candidates,
        searches,
        *,
        enabled: bool = False,
        premium_enabled: bool = False,
        allowed_user_ids: set[int] | None = None,
    ):
        self.api, self.candidates, self.searches = api, candidates, searches
        self.enabled, self.premium_enabled = enabled, premium_enabled
        self.allowed_user_ids = allowed_user_ids or set()
        with self.candidates._connect() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS candidate_search_intents (
                token TEXT NOT NULL UNIQUE, telegram_user_id INTEGER NOT NULL,
                profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
                run_id TEXT, PRIMARY KEY(telegram_user_id, profile_id)
            )''')

    def _available(self, user_id: int) -> bool:
        return (
            self.enabled and self.premium_enabled and user_id in self.allowed_user_ids
        )

    async def preview(self, user_id: int, profile) -> None:
        if not self._available(user_id):
            await self.api.send_message(
                user_id,
                'Premium-поиск недоступен: требуется включённый Premium и доступ к тестовому поиску.',
            )
            return
        template = profile.preferences.get('premium_template_id')
        if not template or self.candidates.get_active_profile(user_id) != profile:
            await self.api.send_message(
                user_id,
                'Выберите этот профиль активным и сначала сохраните Premium-шаблон.',
            )
            return
        query = compose_query(template, profile)
        # Surface an existing owner job after a restart or a profile edit, so
        # replacing the preview cannot leave the owner unable to cancel it.
        with self.searches.connect() as connection:
            active = connection.execute(
                "SELECT id FROM search_runs WHERE owner=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                (f'candidate:{user_id}',),
            ).fetchone()
        token = uuid.uuid4().hex[:16]
        with self.candidates._connect() as connection:
            connection.execute(
                'INSERT INTO candidate_search_intents(token,telegram_user_id,profile_id,profile_version) '
                'VALUES(?,?,?,?) ON CONFLICT(telegram_user_id,profile_id) DO UPDATE SET '
                'token=excluded.token,profile_version=excluded.profile_version,run_id=NULL',
                (token, user_id, profile.profile_id, profile.version),
            )
        if active:
            with self.candidates._connect() as connection:
                connection.execute(
                    'UPDATE candidate_search_intents SET run_id=? WHERE token=?',
                    (active['id'], token),
                )
            await self._status(user_id, {'run_id': active['id'], 'token': token})
            return
        await self.api.send_message(
            user_id,
            f'Предпросмотр запроса:\n{query}\n\nИсточник: Telegram Premium\nПериод: 7 дней · До 50 результатов\n'
            'Сопоставление с текущим снимком профиля. Результаты личные; публикация и рассылка выключены.\n'
            'Другие источники для поиска по профилю недоступны.',
            reply_markup={
                'inline_keyboard': [
                    [{'text': 'Запустить поиск', 'callback_data': f'cs:start:{token}'}]
                ]
            },
        )

    def _intent(self, user_id: int, token: str):
        with self.candidates._connect() as connection:
            row = connection.execute(
                'SELECT * FROM candidate_search_intents WHERE token=? AND telegram_user_id=?',
                (token, user_id),
            ).fetchone()
        if row is None:
            return None
        profile = self.candidates.get_profile(user_id, row['profile_id'])
        if (
            profile is None
            or self.candidates.get_active_profile(user_id) != profile
            or profile.version != row['profile_version']
        ):
            return None
        return dict(row), profile

    async def callback(self, user_id: int, parts: list[str]) -> bool:
        if len(parts) not in {3, 4} or not self._available(user_id):
            return False
        found = self._intent(user_id, parts[2])
        if found is None:
            return False
        intent, profile = found
        owner, action = f'candidate:{user_id}', parts[1]
        if action == 'start' and len(parts) == 3:
            try:
                run = self.searches.create_run(
                    query=compose_query(
                        profile.preferences['premium_template_id'], profile
                    ),
                    sources=['premium'],
                    owner=owner,
                    track='catalog',
                    mode='preview',
                    profile_snapshot=vars(profile),
                    client_request_id=intent['token'],
                    include_review=False,
                )
            except (ValueError, KeyError) as exc:
                await self.api.send_message(user_id, str(exc))
                return False
            with self.candidates._connect() as connection:
                connection.execute(
                    'UPDATE candidate_search_intents SET run_id=? WHERE token=? AND telegram_user_id=?',
                    (run['id'], intent['token'], user_id),
                )
            intent['run_id'] = run['id']
        run = (
            self.searches.get_run(intent['run_id'], owner) if intent['run_id'] else None
        )
        if run is None:
            return False
        snapshot = run.get('profile_snapshot') or {}
        if action in {'results', 'save'} and (
            snapshot.get('profile_id') != profile.profile_id
            or snapshot.get('version') != profile.version
        ):
            return False
        if action == 'cancel' and len(parts) == 3:
            self.searches.cancel(run['id'], owner)
        elif action == 'results' and len(parts) == 4:
            try:
                offset = int(parts[3])
            except ValueError:
                return False
            if not 0 <= offset <= 100:
                return False
            await self._results(user_id, intent, run, offset)
            return True
        elif action == 'save' and len(parts) == 4:
            # Membership and classification are read from this owner's run, never
            # trusted from an item ID supplied in a callback.
            results = self.searches.list_results(run['id'], owner=owner, limit=100)
            item = next((x for x in results['items'] if x['id'] == parts[3]), None)
            if item is None:
                return False
            vacancy_id = item.get('vacancy_id') or f"search-preview:{item['id']}"
            # INSERT/conflict handling is atomic even if ingestion races this save.
            vacancy = self.candidates.register_vacancy(
                vacancy_id=vacancy_id,
                title=item.get('title') or 'Вакансия',
                company=item.get('company'),
                summary=item.get('summary'),
                post_link=item.get('permalink') or '',
                apply_link=None,
                published_at=item.get('timestamp') or None,
                go_visible=False,
                update_existing=False,
            )
            self.candidates.save_vacancy(user_id, vacancy.callback_key)
            await self.api.send_message(user_id, 'Сохранено в личный архив.')
            return True
        elif action not in {'start', 'status'} or len(parts) != 3:
            return False
        await self._status(user_id, intent)
        return True

    async def _status(self, user_id: int, intent: dict) -> None:
        run = self.searches.get_run(intent['run_id'], f'candidate:{user_id}')
        source = run['source_states'].get('premium', {})
        text = f"Поиск: {run['status']}\nTelegram Premium: {source.get('status', 'queued')}"
        if source.get('reason'):
            text += f"\nПричина: {source['reason']}"
        token = intent['token']
        rows = [
            [
                {'text': 'Обновить', 'callback_data': f'cs:status:{token}'},
                {'text': 'Результаты', 'callback_data': f'cs:results:{token}:0'},
            ]
        ]
        if run['status'] in {'queued', 'running'}:
            rows.append([{'text': 'Отменить', 'callback_data': f'cs:cancel:{token}'}])
        await self.api.send_message(
            user_id, text, reply_markup={'inline_keyboard': rows}
        )

    async def _results(
        self, user_id: int, intent: dict, run: dict, offset: int
    ) -> None:
        results = self.searches.list_results(
            run['id'], owner=f'candidate:{user_id}', offset=offset, limit=5
        )
        if not results['items']:
            await self.api.send_message(user_id, 'Подтверждённых результатов пока нет.')
            return
        for item in results['items']:
            vacancy = SimpleNamespace(
                title=item.get('title') or 'Вакансия',
                company=item.get('company'),
                summary=item.get('summary'),
                post_link=item.get('permalink'),
                apply_link=None,
                published_at=item.get('timestamp'),
            )
            await self.api.send_message(
                user_id,
                format_legacy_card(vacancy, now=datetime.now(timezone.utc)),
                parse_mode='HTML',
                reply_markup={
                    'inline_keyboard': [
                        [
                            {
                                'text': 'Сохранить в личный архив',
                                'callback_data': f"cs:save:{intent['token']}:{item['id']}",
                            }
                        ]
                    ]
                },
            )
        if offset + 5 < results['total']:
            await self.api.send_message(
                user_id,
                'Ещё результаты',
                reply_markup={
                    'inline_keyboard': [
                        [
                            {
                                'text': 'Далее',
                                'callback_data': f"cs:results:{intent['token']}:{offset + 5}",
                            }
                        ]
                    ]
                },
            )
