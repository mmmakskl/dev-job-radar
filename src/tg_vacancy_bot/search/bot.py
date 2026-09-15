"""Private preview browser for durable unified search runs."""

import asyncio
import html
import logging
from typing import Any
from urllib.parse import urlsplit


class CandidateSearch:
    """Queues personal searches and delivers progress without blocking bot polling."""

    def __init__(self, api: Any, store: Any, sources: list[dict]) -> None:
        self.api = api
        self.store = store
        self.sources = sources
        self.pending: set[tuple[int, int]] = set()
        self.rendered: dict[str, tuple[str, str]] = {}
        self.lock = asyncio.Lock()

    async def handle_message(self, chat_id: int, user_id: int, text: str) -> bool:
        """Handles /search and the next message after the search keyboard button."""
        key = (chat_id, user_id)
        command, _, argument = text.partition(' ')
        if text in {'Новые', 'Сохранённые', 'Мои отклики', 'Скрытые'}:
            self.pending.discard(key)
            return False
        if text == 'Поиск' or command.split('@')[0] == '/search':
            query = argument.strip() if text != 'Поиск' else ''
            if not query:
                self.pending.add(key)
                await self.api.send_message(
                    chat_id, 'Введите запрос, например: Go backend remote.'
                )
                return True
        elif key in self.pending and not text.startswith('/'):
            query = text.strip()
        else:
            self.pending.discard(key)
            return False
        if len(query) < 3 or len(query) > 160:
            await self.api.send_message(chat_id, 'Введите запрос от 3 до 160 символов.')
            return True
        sources = [source['key'] for source in self.sources if source['enabled']]
        if not sources:
            await self.api.send_message(chat_id, 'Источники поиска пока недоступны.')
            return True
        self.pending.discard(key)
        owner = f'candidate:{user_id}'
        try:
            run = await asyncio.to_thread(
                self.store.create_run,
                query=query,
                sources=sources,
                owner=owner,
                mode='preview',
                include_review=False,
            )
        except ValueError:
            await self.api.send_message(
                chat_id,
                'Не удалось запустить поиск. Дождитесь завершения текущего поиска '
                'или остановите его и проверьте запрос.',
            )
            return True
        async with self.lock:
            text, keyboard = await self._view(run)
            sent = await self.api.send_message(
                chat_id, text, parse_mode='HTML', reply_markup=keyboard
            )
            await asyncio.to_thread(
                self.store.save_ui, run['id'], owner, chat_id, sent['message_id']
            )
            self.rendered[run['id']] = (text, repr(keyboard))
        return True

    async def handle_callback(
        self, callback: dict, user_id: int, callback_id: str, data: str
    ) -> None:
        """Validates ownership and the original private message before any action."""
        parts = data.split(':')
        if len(parts) != 3 or parts[2] not in {'prev', 'next', 'refresh', 'cancel'}:
            await self.api.answer_callback_query(callback_id, 'Некорректное действие.')
            return
        _, run_id, operation = parts
        owner = f'candidate:{user_id}'
        async with self.lock:
            run = await asyncio.to_thread(self.store.get_run, run_id, owner=owner)
            message = callback.get('message') or {}
            chat = message.get('chat') or {}
            if (
                run is None
                or chat.get('type') != 'private'
                or run.get('ui_chat_id') != chat.get('id')
                or run.get('ui_message_id') != message.get('message_id')
            ):
                await self.api.answer_callback_query(
                    callback_id, 'Эта карточка недоступна.'
                )
                return
            if operation == 'refresh':
                enabled = {
                    source['key'] for source in self.sources if source['enabled']
                }
                sources = [source for source in run['sources'] if source in enabled]
                if not sources:
                    await self.api.answer_callback_query(
                        callback_id, 'Источники поиска сейчас недоступны.'
                    )
                    return
                try:
                    refreshed = await asyncio.to_thread(
                        self.store.create_run,
                        query=run['query'],
                        sources=sources,
                        owner=owner,
                        track=run.get('track', 'go'),
                        mode='preview',
                        result_limit=run.get('result_limit', 50),
                        period_days=run.get('period_days', 7),
                        include_review=False,
                    )
                except ValueError:
                    await self.api.answer_callback_query(
                        callback_id,
                        'Дождитесь завершения текущего поиска или остановите его.',
                    )
                    return
                await asyncio.to_thread(self.store.clear_ui, run_id, owner)
                await asyncio.to_thread(
                    self.store.save_ui,
                    refreshed['id'],
                    owner,
                    run['ui_chat_id'],
                    run['ui_message_id'],
                )
                refreshed.update(
                    ui_chat_id=run['ui_chat_id'],
                    ui_message_id=run['ui_message_id'],
                    ui_position=0,
                )
                run = refreshed
            elif operation == 'cancel':
                run = await asyncio.to_thread(self.store.cancel, run_id, owner=owner)
            elif operation in {'prev', 'next'}:
                page = await asyncio.to_thread(
                    self.store.list_results,
                    run_id,
                    owner=owner,
                    limit=1,
                    include_review=False,
                )
                position = int(run.get('ui_position') or 0)
                position += -1 if operation == 'prev' else 1
                position = max(0, min(position, page['total'] - 1))
                await asyncio.to_thread(
                    self.store.save_ui,
                    run_id,
                    owner,
                    run['ui_chat_id'],
                    run['ui_message_id'],
                    position=position,
                )
                run['ui_position'] = position
            await self.api.answer_callback_query(callback_id, '')
            if run:
                await self._refresh(run)

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Restores persisted UI deliveries on restart; each search stays private."""
        while not shutdown_event.is_set():
            try:
                runs = await asyncio.to_thread(self.store.list_ui_runs)
                for run in runs:
                    if shutdown_event.is_set():
                        break
                    try:
                        async with self.lock:
                            current = await asyncio.to_thread(
                                self.store.get_run, run['id'], owner=run['owner']
                            )
                            if current:
                                await self._refresh(current)
                    except Exception:
                        logging.warning('Не удалось обновить карточку поиска')
            except Exception:
                logging.warning('Не удалось прочитать очередь карточек поиска')
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

    async def _refresh(self, run: dict) -> None:
        if not str(run.get('owner', '')).startswith('candidate:'):
            return
        if run.get('ui_chat_id') is None or run.get('ui_message_id') is None:
            return
        text, keyboard = await self._view(run)
        signature = (text, repr(keyboard))
        if self.rendered.get(run['id']) == signature:
            return
        try:
            await self.api.edit_message_text(
                run['ui_chat_id'],
                run['ui_message_id'],
                text,
                parse_mode='HTML',
                reply_markup=keyboard,
            )
        except Exception as error:
            if 'message is not modified' not in str(error).lower():
                raise
        self.rendered[run['id']] = signature

    async def _view(self, run: dict) -> tuple[str, dict]:
        position = int(run.get('ui_position') or 0)
        page = await asyncio.to_thread(
            self.store.list_results,
            run['id'],
            owner=run['owner'],
            offset=position,
            limit=1,
            include_review=False,
        )
        statuses = {
            'queued': 'В очереди',
            'running': 'Ищем вакансии',
            'completed': 'Поиск завершён',
            'completed_with_errors': 'Поиск завершён частично',
            'cancelled': 'Поиск остановлен',
            'failed': 'Поиск не выполнен',
        }
        lines = [
            '<b>Личный поиск · предпросмотр</b>',
            html.escape(str(run['query'])[:500]),
            statuses.get(run['status'], 'Обновляем поиск'),
        ]
        labels = {source['key']: source['label'] for source in self.sources}
        states = {
            'queued': 'ожидает',
            'pending': 'ожидает',
            'running': 'ищем',
            'completed': 'готово',
            'failed': 'недоступен',
            'timeout': 'время ожидания истекло',
            'cancelled': 'остановлен',
            'disabled': 'отключён',
        }
        for source, state in (run.get('source_states') or {}).items():
            status = state.get('status', 'queued') if isinstance(state, dict) else state
            lines.append(
                f'{html.escape(labels.get(source, source))}: '
                f'{html.escape(states.get(status, str(status)))}'
            )
        rows = []
        if page['items']:
            item = page['items'][0]
            lines.extend(
                [
                    '',
                    f'<b>{position + 1} из {page["total"]}</b>',
                    f'<b>{html.escape(str(item.get("title") or "Вакансия")[:200])}</b>',
                    html.escape(str(item.get('company') or '')[:150]),
                    html.escape(
                        str(item.get('summary') or item.get('text') or '')[:1000]
                    ),
                    f'Источник: {html.escape(str(item["source"]))}',
                ]
            )
            link = str(item.get('permalink') or '')
            if urlsplit(link).scheme in {'http', 'https'} and urlsplit(link).netloc:
                rows.append([{'text': 'Открыть оригинал', 'url': link}])
            rows.append(
                [
                    {'text': '‹ Назад', 'callback_data': f'q:{run["id"]}:prev'},
                    {'text': 'Вперёд ›', 'callback_data': f'q:{run["id"]}:next'},
                ]
            )
        else:
            lines.extend(['', 'Результатов пока нет.'])
        rows.append([{'text': 'Обновить', 'callback_data': f'q:{run["id"]}:refresh'}])
        if run['status'] in {'queued', 'running'}:
            rows[-1].append(
                {'text': 'Остановить', 'callback_data': f'q:{run["id"]}:cancel'}
            )
        return '\n'.join(lines), {'inline_keyboard': rows}
