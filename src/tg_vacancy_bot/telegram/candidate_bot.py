"""Personal vacancy feed, profile wizard and Premium templates over Bot API."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from tg_vacancy_bot.candidate_catalog import CATALOG, validate_profile_path
from tg_vacancy_bot.premium_search.templates import compose_query, list_templates
from tg_vacancy_bot.telegram.candidate_card_formatter import (
    format_card,
    format_legacy_card,
)
from tg_vacancy_bot.telegram.candidate_delivery_worker import personal_card
from tg_vacancy_bot.telegram.candidate_search import CandidateSearch
from tg_vacancy_bot.telegram.candidate_store import (
    CandidateProfile,
    CandidateStore,
    CandidateVacancy,
)


class CandidateBotApi(Protocol):
    async def get_updates(self, offset: int | None, timeout: int) -> list[dict]: ...
    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict: ...
    async def answer_callback_query(
        self, callback_query_id: str, text: str
    ) -> None: ...
    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict: ...


MAIN_KEYBOARD = {
    'keyboard': [['Новые', 'Сохранённые'], ['Профили']],
    'resize_keyboard': True,
}


PROFILE_STEPS = (
    'direction_id',
    'specialization_id',
    'role_id',
    'stacks',
    'seniority',
    'formats',
    'geography',
    'vacancy_languages',
    'required_skills',
    'desired_skills',
    'excluded_skills',
    'timezone',
    'delivery_mode',
    'preview',
)
STEP_LABELS = dict(
    zip(
        PROFILE_STEPS,
        (
            'Направление',
            'Специализация',
            'Роль',
            'Допустимые стеки',
            'Грейд',
            'Формат',
            'География',
            'Язык вакансий',
            'Обязательные навыки',
            'Желаемые навыки',
            'Исключения',
            'Часовой пояс',
            'Режим доставки',
            'Предпросмотр',
        ),
    )
)
STEP_LABELS['name'] = 'Имя профиля'
STEP_LABELS['premium_template_id'] = 'Premium-шаблон'


def personal_keyboard(callback_key: str) -> dict:
    return {
        'inline_keyboard': [
            [
                {'text': 'Сохранить', 'callback_data': f'v:s:{callback_key}'},
            ]
        ]
    }


def vacancy_text(vacancy: CandidateVacancy) -> str:
    return format_legacy_card(vacancy, now=datetime.now(timezone.utc))


class CandidateBot:
    """Routes personal feed, profiles and templates for an allowlisted user."""

    def __init__(
        self,
        api: CandidateBotApi,
        store: CandidateStore,
        allowed_user_ids: set[int],
        *,
        registry=None,
        profile_feed_enabled: bool = False,
        profile_allowed_user_ids: set[int] | None = None,
        search: CandidateSearch | None = None,
    ) -> None:
        self.api = api
        self.store = store
        self.allowed_user_ids = allowed_user_ids
        self.registry = registry
        self.profile_feed_enabled = profile_feed_enabled
        self.profile_allowed_user_ids = profile_allowed_user_ids or set()
        self.search = search

    def _keyboard(self, user_id: int) -> dict:
        if self.profile_feed_enabled and user_id in self.profile_allowed_user_ids:
            return {
                'keyboard': [['Новые', 'Для меня'], ['Сохранённые', 'Профили']],
                'resize_keyboard': True,
            }
        return MAIN_KEYBOARD

    async def _personal_feed(self, user_id: int) -> None:
        await self._show_browser(user_id, 'for_me')

    async def _show_browser(
        self, user_id: int, bucket: str, index: int = 0, message_id: int | None = None
    ) -> bool:
        """Show one current card; navigation edits only the owner's private message."""
        if bucket == 'for_me' and (
            not self.profile_feed_enabled
            or user_id not in self.profile_allowed_user_ids
            or self.registry is None
        ):
            if message_id is None:
                await self.api.send_message(
                    user_id, 'Персональная выдача пока недоступна.'
                )
            return False
        if bucket == 'for_me':
            matches = await asyncio.to_thread(
                self.registry.list_personal_matches, user_id
            )
            cards = [
                (
                    format_card(
                        personal_card(match),
                        now=datetime.now(timezone.utc),
                        timezone=min(
                            match.matched_profiles, key=lambda p: p.profile_id
                        ).preferences.get('timezone', 'Europe/Moscow'),
                    ),
                    match.callback_key,
                )
                for match in matches
            ]
        elif bucket in {'new', 'saved'}:
            cards = [
                (vacancy_text(vacancy), vacancy.callback_key)
                for vacancy in self.store.list_for_user(user_id, bucket)
            ]
        else:
            return False
        if not cards:
            if message_id is not None:
                return False
            await self.api.send_message(
                user_id,
                (
                    'Подтверждённых совпадений активных профилей пока нет.'
                    if bucket == 'for_me'
                    else (
                        'Новых вакансий пока нет.'
                        if bucket == 'new'
                        else 'В сохранённых вакансиях пока ничего нет.'
                    )
                ),
            )
            return True
        if index < 0 or index >= len(cards):
            return False
        content, callback_key = cards[index]
        keyboard = personal_keyboard(callback_key)
        if len(cards) > 1:
            controls = []
            if index > 0:
                controls.append(
                    {'text': 'Назад', 'callback_data': f'b:{bucket}:{index - 1}'}
                )
            if index + 1 < len(cards):
                controls.append(
                    {'text': 'Далее', 'callback_data': f'b:{bucket}:{index + 1}'}
                )
            keyboard['inline_keyboard'].append(controls)
        if message_id is None:
            await self.api.send_message(
                user_id, content, parse_mode='HTML', reply_markup=keyboard
            )
        else:
            await self.api.edit_message_text(
                user_id, message_id, content, parse_mode='HTML', reply_markup=keyboard
            )
        return True

    async def run(self, shutdown_event: asyncio.Event) -> None:
        offset: int | None = None
        while not shutdown_event.is_set():
            try:
                for update in await self.api.get_updates(offset, timeout=25):
                    update_id = update.get('update_id')
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    await self.handle_update(update)
            except Exception:
                logging.exception('Ошибка long polling пользовательского бота')
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    continue

    async def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get('message'), dict):
            await self._handle_message(update['message'])
        elif isinstance(update.get('callback_query'), dict):
            await self._handle_callback(update['callback_query'])

    async def _handle_message(self, message: dict[str, Any]) -> None:
        sender, chat = message.get('from') or {}, message.get('chat') or {}
        user_id, chat_id = sender.get('id'), chat.get('id')
        if (
            not isinstance(user_id, int)
            or not isinstance(chat_id, int)
            or chat.get('type') != 'private'
        ):
            return
        if user_id not in self.allowed_user_ids:
            await self.api.send_message(chat_id, 'Доступ к beta-боту ограничен.')
            return
        text = (message.get('text') or '').strip()
        if text in {'/start', '/help'}:
            await self.api.send_message(
                chat_id,
                'Лента вакансий и архив сохранённых.',
                reply_markup=self._keyboard(user_id),
            )
            return
        if text in {'Для меня', '/forme'}:
            await self._personal_feed(user_id)
            return
        if text in {'Профили', '/profiles'}:
            await self._profiles(user_id)
            return
        if self.store.get_profile_draft(user_id) and text not in {
            'Новые',
            'Сохранённые',
            '/new',
            '/saved',
        }:
            await self._draft_text(user_id, text)
            return
        bucket = {
            'Новые': 'new',
            'Сохранённые': 'saved',
            '/new': 'new',
            '/saved': 'saved',
        }.get(text)
        if bucket is None:
            await self.api.send_message(
                chat_id,
                'Используйте «Новые», «Сохранённые» или «Профили».',
                reply_markup=self._keyboard(user_id),
            )
            return
        await self._show_browser(user_id, bucket)

    async def _can_save(self, user_id: int, key: str) -> bool:
        with self.store._connect() as connection:
            row = connection.execute(
                'SELECT v.vacancy_id FROM vacancies v WHERE v.callback_key=? AND '
                '(v.go_visible=1 OR EXISTS(SELECT 1 FROM user_saved_vacancies s '
                'WHERE s.vacancy_id=v.vacancy_id AND s.telegram_user_id=?))',
                (key, user_id),
            ).fetchone()
        if row:
            return True
        if (
            self.profile_feed_enabled
            and user_id in self.profile_allowed_user_ids
            and self.registry is not None
        ):
            matches = await asyncio.to_thread(
                self.registry.list_personal_matches, user_id
            )
            return any(match.callback_key == key for match in matches)
        return False

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        sender = callback.get('from') or {}
        user_id, callback_id = sender.get('id'), callback.get('id')
        if not isinstance(user_id, int) or not isinstance(callback_id, str):
            return
        if user_id not in self.allowed_user_ids:
            await self.api.answer_callback_query(callback_id, 'Доступ ограничен.')
            return
        data = callback.get('data') or ''
        if not isinstance(data, str):
            return
        parts = data.split(':')
        if len(parts) == 3 and parts[0] == 'b':
            source = callback.get('message') or {}
            chat = source.get('chat') or {}
            try:
                index = int(parts[2])
            except ValueError:
                index = -1
            valid = (
                chat.get('type') == 'private'
                and chat.get('id') == user_id
                and isinstance(source.get('message_id'), int)
                and await self._show_browser(
                    user_id, parts[1], index, source['message_id']
                )
            )
            await self.api.answer_callback_query(
                callback_id, 'Готово.' if valid else 'Кнопка устарела или недоступна.'
            )
            return
        if parts[0] == 'cs' and self.search is not None:
            valid = await self.search.callback(user_id, parts)
            await self.api.answer_callback_query(
                callback_id, 'Готово.' if valid else 'Кнопка устарела или недоступна.'
            )
            return
        if parts[0] in {'p', 'w'}:
            try:
                valid = await self._profile_callback(user_id, parts)
            except (ValueError, KeyError, TypeError):
                valid = False
            await self.api.answer_callback_query(
                callback_id, 'Готово.' if valid else 'Кнопка устарела или недоступна.'
            )
            return
        # Legacy status, search, and complaint callback payloads are deliberately
        # unsupported and can never write to personal data.
        if len(parts) == 3 and parts[0] == 'v' and parts[1] == 's':
            saved = await self._can_save(user_id, parts[2]) and self.store.save_vacancy(
                user_id, parts[2]
            )
            await self.api.answer_callback_query(
                callback_id, 'Сохранено.' if saved else 'Вакансия недоступна.'
            )
            return
        await self.api.answer_callback_query(
            callback_id, 'Это действие больше не поддерживается.'
        )

    def _choices(self, draft: dict) -> list[tuple[str, str]]:
        step, values = draft['step'], draft['values']
        directions = CATALOG['directions']
        if step == 'direction_id':
            items = directions
        elif step == 'specialization_id':
            items = next(d for d in directions if d['id'] == values['direction_id'])[
                'specializations'
            ]
        elif step in {'role_id', 'stacks'}:
            specs = next(d for d in directions if d['id'] == values['direction_id'])[
                'specializations'
            ]
            roles = next(s for s in specs if s['id'] == values['specialization_id'])[
                'roles'
            ]
            if step == 'stacks':
                return [
                    (x, x)
                    for x in next(r for r in roles if r['id'] == values['role_id'])[
                        'stacks'
                    ]
                ]
            items = roles
        else:
            return [
                (x, x)
                for x in {
                    'seniority': ['intern', 'junior', 'middle', 'senior', 'lead'],
                    'formats': ['remote', 'hybrid', 'office'],
                    'vacancy_languages': ['ru', 'en'],
                    'delivery_mode': ['manual', 'immediate', 'hourly'],
                }.get(step, [])
            ]
        return [(x['id'], x['synonyms'][0]) for x in items]

    @staticmethod
    def _profile_text(values: dict) -> str:
        lines = [
            values['name'],
            ' → '.join(values.get(k, '—') for k in PROFILE_STEPS[:3]),
        ]
        lines.extend(
            f'{STEP_LABELS.get(k, k)}: {", ".join(v) if isinstance(v, list) else v}'
            for k, v in values['preferences'].items()
        )
        return '\n'.join(lines)[:3500]

    async def _profiles(self, user_id: int) -> None:
        rows = [
            [
                {
                    'text': f'{"✓" if p.is_active else "○"} {p.name}',
                    'callback_data': f'p:view:{p.profile_id}:{p.version}',
                }
            ]
            for p in self.store.list_profiles(user_id)
        ]
        rows.append([{'text': 'Создать профиль', 'callback_data': 'p:new'}])
        if self.store.get_profile_draft(user_id):
            rows.append([{'text': 'Продолжить черновик', 'callback_data': 'p:resume'}])
        await self.api.send_message(
            user_id, 'Профили', reply_markup={'inline_keyboard': rows}
        )

    async def _show_profile(self, user_id: int, profile: CandidateProfile) -> None:
        rows = [
            [
                {
                    'text': label,
                    'callback_data': f'p:{action}:{profile.profile_id}:{profile.version}',
                }
            ]
            for action, label in [
                ('edit', 'Редактировать'),
                ('toggle', 'Выключить' if profile.is_active else 'Включить'),
                ('templates', 'Premium-шаблоны'),
                ('search', 'Искать'),
            ]
        ]
        await self.api.send_message(
            user_id,
            self._profile_text(vars(profile)),
            reply_markup={'inline_keyboard': rows},
        )

    async def _start_draft(
        self, user_id: int, profile: CandidateProfile | None = None
    ) -> None:
        if not self.store.get_profile_draft(user_id):
            values = (
                {
                    k: getattr(profile, k)
                    for k in (
                        'name',
                        'direction_id',
                        'specialization_id',
                        'role_id',
                        'preferences',
                        'is_active',
                    )
                }
                if profile
                else {
                    'name': '',
                    'preferences': {
                        'delivery_mode': 'manual',
                        'timezone': 'Europe/Moscow',
                    },
                    'is_active': True,
                }
            )
            self.store.put_profile_draft(
                user_id,
                {
                    'token': uuid.uuid4().hex[:12],
                    'revision': 0,
                    'step': PROFILE_STEPS[0],
                    'values': values,
                    'profile_id': profile.profile_id if profile else None,
                    'profile_version': profile.version if profile else None,
                },
            )
        await self._show_draft(user_id)

    async def _show_draft(self, user_id: int) -> None:
        draft = self.store.get_profile_draft(user_id)
        if draft is None:
            await self._profiles(user_id)
            return
        step = draft['step']
        prefix = f'w:{draft["token"]}:{draft["revision"]}:'

        def button(label, action):
            return {'text': label, 'callback_data': prefix + action}

        rows = []
        if step == 'preview':
            text = 'Предпросмотр\n' + self._profile_text(draft['values'])
            rows = [
                [button('Сохранить профиль', 'save')],
                [button('Изменить имя', 'name')],
            ]
        else:
            text = STEP_LABELS[step]
            choices = self._choices(draft)
            selected = draft['values']['preferences'].get(step, [])
            rows = [
                [button(('✓ ' if key in selected else '') + label, f'choose:{i}')]
                for i, (key, label) in enumerate(choices)
            ]
            if step not in PROFILE_STEPS[:3]:
                text += (
                    '\nВыберите значения или введите через запятую. Можно пропустить.'
                )
                if step in {'timezone', 'name'}:
                    text = STEP_LABELS[step] + '\nВведите значение текстом.'
                rows.append([button('Далее', 'next')])
                if step != 'name':
                    rows.append([button('Пропустить', 'skip')])
            if selected:
                text += f'\nСейчас: {selected}'
        if step != PROFILE_STEPS[0]:
            rows.append([button('Назад', 'back')])
        rows.append([button('Отмена', 'cancel')])
        await self.api.send_message(
            user_id, text, reply_markup={'inline_keyboard': rows}
        )

    def _set_draft_value(self, draft: dict, value: str | list[str]) -> None:
        step, values = draft['step'], draft['values']
        if step in PROFILE_STEPS[:3]:
            if values.get(step) != value:
                index = PROFILE_STEPS.index(step)
                for key in PROFILE_STEPS[index + 1 : 3]:
                    values.pop(key, None)
                values['preferences']['stacks'] = []
                values['preferences'].pop('premium_template_id', None)
                values['name'] = ''
            values[step] = value
            if step == 'role_id' and not values['name']:
                values['name'] = dict(self._choices(draft))[value]
        elif step == 'name':
            if not value or not str(value).strip():
                raise ValueError('Имя не может быть пустым.')
            values['name'] = value
        else:
            preferences = {**values['preferences'], step: value}
            if step == 'stacks':
                validate_profile_path(
                    values['direction_id'],
                    values['specialization_id'],
                    values['role_id'],
                    value,
                )
                self.store._clear_invalid_template(values['role_id'], preferences)
            values['preferences'] = self.store._validate_preferences(preferences)

    @staticmethod
    def _advance(draft: dict) -> None:
        step = draft['step']
        draft['step'] = (
            'preview'
            if step == 'name'
            else PROFILE_STEPS[PROFILE_STEPS.index(step) + 1]
        )

    async def _draft_text(self, user_id: int, text: str) -> None:
        draft = self.store.get_profile_draft(user_id)
        step = draft['step']
        try:
            if step in PROFILE_STEPS[:3] or step == 'preview':
                raise ValueError('Используйте кнопки текущего шага.')
            value = (
                text
                if step in {'name', 'timezone', 'delivery_mode'}
                else [x.strip() for x in text.split(',') if x.strip()]
            )
            self._set_draft_value(draft, value)
            self._advance(draft)
            revision = draft['revision']
            draft['revision'] += 1
            self.store.put_profile_draft(user_id, draft, revision)
        except ValueError as error:
            await self.api.send_message(user_id, str(error))
        await self._show_draft(user_id)

    async def _profile_callback(self, user_id: int, parts: list[str]) -> bool:
        if parts == ['p', 'new']:
            await self._start_draft(user_id)
            return True
        if parts == ['p', 'resume']:
            await self._show_draft(user_id)
            return True
        if parts[0] == 'w':
            draft = self.store.get_profile_draft(user_id)
            if (
                len(parts) < 4
                or draft is None
                or parts[1] != draft['token']
                or parts[2] != str(draft['revision'])
            ):
                return False
            action, step = parts[3], draft['step']
            revision = draft['revision']
            if action in {'cancel', 'save'} and len(parts) == 4:
                result = self.store.finish_profile_draft(
                    user_id, parts[1], revision, save=action == 'save'
                )
                if not result:
                    return False
                await self._profiles(user_id)
                return True
            if action == 'choose' and len(parts) == 5:
                choices = self._choices(draft)
                index = int(parts[4])
                if index < 0 or index >= len(choices):
                    return False
                value = choices[index][0]
                if step in PROFILE_STEPS[:3] or step == 'delivery_mode':
                    self._set_draft_value(draft, value)
                    self._advance(draft)
                else:
                    selected = list(draft['values']['preferences'].get(step, []))
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.append(value)
                    self._set_draft_value(draft, selected)
            elif len(parts) != 4:
                return False
            elif action == 'back' and step != PROFILE_STEPS[0]:
                draft['step'] = (
                    'preview'
                    if step == 'name'
                    else PROFILE_STEPS[PROFILE_STEPS.index(step) - 1]
                )
            elif action == 'name' and step == 'preview':
                draft['step'] = 'name'
            elif action in {'skip', 'next'} and step not in (
                *PROFILE_STEPS[:3],
                'preview',
            ):
                if action == 'skip':
                    self._set_draft_value(
                        draft,
                        {'timezone': 'Europe/Moscow', 'delivery_mode': 'manual'}.get(
                            step, []
                        ),
                    )
                self._advance(draft)
            else:
                return False
            draft['revision'] += 1
            if not self.store.put_profile_draft(user_id, draft, revision):
                return False
            await self._show_draft(user_id)
            return True
        if len(parts) not in {4, 5}:
            return False
        profile = self.store.get_profile(user_id, parts[2])
        if profile is None or parts[3] != str(profile.version):
            return False
        action = parts[1]
        if action == 'template' and len(parts) == 5:
            templates = list_templates(profile)
            index = int(parts[4])
            if index < 0 or index >= len(templates):
                return False
            template = templates[index]
            query = compose_query(template.id, profile)
            profile = self.store.update_profile(
                user_id,
                profile.profile_id,
                expected_version=profile.version,
                preferences={**profile.preferences, 'premium_template_id': template.id},
            )
            if profile is None:
                return False
            await self.api.send_message(user_id, f'Шаблон сохранён. Запрос:\n{query}')
            await self._show_profile(user_id, profile)
        elif len(parts) != 4:
            return False
        elif action == 'view':
            await self._show_profile(user_id, profile)
        elif action == 'edit':
            await self._start_draft(user_id, profile)
        elif action == 'toggle':
            profile = self.store.update_profile(
                user_id,
                profile.profile_id,
                expected_version=profile.version,
                is_active=not profile.is_active,
            )
            if profile is None:
                return False
            await self._show_profile(user_id, profile)
        elif action == 'search':
            if self.search is None:
                await self.api.send_message(user_id, 'Premium-поиск недоступен.')
            else:
                await self.search.preview(user_id, profile)
        elif action == 'templates':
            rows = [
                [
                    {
                        'text': t.label,
                        'callback_data': f'p:template:{profile.profile_id}:{profile.version}:{i}',
                    }
                ]
                for i, t in enumerate(list_templates(profile))
            ]
            await self.api.send_message(
                user_id,
                'Выберите Premium-шаблон. Поиск не запускается.',
                reply_markup={'inline_keyboard': rows},
            )
        else:
            return False
        return True
