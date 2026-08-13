"""Personal candidate workflow driven by Telegram Bot API long polling."""

import asyncio
import html
import logging
from typing import Any, Protocol

from tg_vacancy_bot.telegram.candidate_store import CandidateStore, CandidateVacancy


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


MAIN_KEYBOARD = {
    'keyboard': [['Новые', 'Мои отклики'], ['Сохранённые', 'Скрытые']],
    'resize_keyboard': True,
}
STATUS_ACTIONS = {
    'n': ('new', 'Возвращено в новые'),
    's': ('saved', 'Сохранено'),
    'a': ('applied', 'Отклик отмечен'),
    'p': ('replied', 'Ответ получен'),
    'i': ('interview', 'Этап: интервью'),
    'o': ('offer', 'Этап: оффер'),
    'x': ('rejected', 'Этап: отказ'),
    'h': ('hidden', 'Скрыто'),
}
REPORT_ACTIONS = {
    'sus': 'suspicious',
    'dup': 'duplicate',
    'old': 'outdated',
    'oth': 'other',
}


def personal_keyboard(callback_key: str, post_link: str) -> dict[str, list[list[dict]]]:
    """Shows all personal pipeline stages without changing the channel card."""
    return {
        'inline_keyboard': [
            [
                {'text': 'Открыть', 'url': post_link},
                {'text': 'Сохранить', 'callback_data': f'v:s:{callback_key}'},
                {'text': 'Откликнулся', 'callback_data': f'v:a:{callback_key}'},
            ],
            [
                {'text': 'Ответили', 'callback_data': f'v:p:{callback_key}'},
                {'text': 'Интервью', 'callback_data': f'v:i:{callback_key}'},
                {'text': 'Оффер', 'callback_data': f'v:o:{callback_key}'},
            ],
            [
                {'text': 'Отказ', 'callback_data': f'v:x:{callback_key}'},
                {'text': 'В новые', 'callback_data': f'v:n:{callback_key}'},
                {'text': 'Скрыть', 'callback_data': f'v:h:{callback_key}'},
            ],
            [{'text': 'Пожаловаться', 'callback_data': f'v:r:{callback_key}'}],
        ]
    }


def report_keyboard(callback_key: str) -> dict[str, list[list[dict]]]:
    return {
        'inline_keyboard': [
            [
                {'text': 'Подозрительная', 'callback_data': f'r:{callback_key}:sus'},
                {'text': 'Дубликат', 'callback_data': f'r:{callback_key}:dup'},
            ],
            [
                {'text': 'Неактуальна', 'callback_data': f'r:{callback_key}:old'},
                {'text': 'Другое', 'callback_data': f'r:{callback_key}:oth'},
            ],
        ]
    }


def vacancy_text(vacancy: CandidateVacancy) -> str:
    """Renders a compact personal view without exposing other users' actions."""
    lines = [f'<b>{html.escape(vacancy.title)}</b>']
    if vacancy.company:
        lines.append(f'Компания: {html.escape(vacancy.company)}')
    if vacancy.summary:
        lines.extend(['', html.escape(vacancy.summary[:500])])
    lines.extend(['', f'<b>Статус:</b> {html.escape(vacancy.status)}'])
    return '\n'.join(lines)


class CandidateBot:
    """Routes private commands and callback queries for an explicit beta allowlist."""

    def __init__(
        self,
        api: CandidateBotApi,
        store: CandidateStore,
        allowed_user_ids: set[int],
    ) -> None:
        self.api = api
        self.store = store
        self.allowed_user_ids = allowed_user_ids

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Runs Bot API long polling. A transient API failure retries safely."""
        offset: int | None = None
        while not shutdown_event.is_set():
            try:
                updates = await self.api.get_updates(offset, timeout=25)
                for update in updates:
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
        """Handles one fakeable Bot API update for unit tests and live polling."""
        if isinstance(update.get('message'), dict):
            await self._handle_message(update['message'])
        elif isinstance(update.get('callback_query'), dict):
            await self._handle_callback(update['callback_query'])

    async def _handle_message(self, message: dict[str, Any]) -> None:
        sender = message.get('from') or {}
        user_id = sender.get('id')
        chat_id = (message.get('chat') or {}).get('id')
        if not isinstance(user_id, int) or chat_id is None:
            return
        if (message.get('chat') or {}).get('type') != 'private':
            return
        if user_id not in self.allowed_user_ids:
            await self.api.send_message(chat_id, 'Доступ к beta-боту ограничен.')
            return
        text = (message.get('text') or '').strip()
        if text in {'/start', '/help'}:
            await self.api.send_message(
                chat_id,
                'Выберите подборку вакансий. Статусы видны только вам.',
                reply_markup=MAIN_KEYBOARD,
            )
            return
        buckets = {
            'Новые': 'new',
            'Сохранённые': 'saved',
            'Мои отклики': 'applications',
            'Скрытые': 'hidden',
            '/new': 'new',
            '/saved': 'saved',
            '/applications': 'applications',
            '/hidden': 'hidden',
        }
        if text in buckets:
            await self._show_list(chat_id, user_id, buckets[text])
        else:
            await self.api.send_message(
                chat_id,
                'Используйте кнопки «Новые», «Мои отклики», «Сохранённые» или «Скрытые».',
                reply_markup=MAIN_KEYBOARD,
            )

    async def _show_list(self, chat_id: int | str, user_id: int, bucket: str) -> None:
        vacancies = self.store.list_for_user(user_id, bucket)
        if not vacancies:
            await self.api.send_message(chat_id, 'В этой подборке пока нет вакансий.')
            return
        for vacancy in vacancies:
            await self.api.send_message(
                chat_id,
                vacancy_text(vacancy),
                parse_mode='HTML',
                reply_markup=personal_keyboard(vacancy.callback_key, vacancy.post_link),
            )

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        sender = callback.get('from') or {}
        user_id = sender.get('id')
        callback_id = callback.get('id')
        if not isinstance(user_id, int) or not isinstance(callback_id, str):
            return
        if user_id not in self.allowed_user_ids:
            await self.api.answer_callback_query(callback_id, 'Доступ ограничен.')
            return
        data = callback.get('data') or ''
        if data.startswith('v:'):
            await self._handle_vacancy_callback(callback_id, user_id, data)
        elif data.startswith('r:'):
            await self._handle_report_callback(callback_id, user_id, data)
        else:
            await self.api.answer_callback_query(callback_id, 'Неизвестное действие.')

    async def _handle_vacancy_callback(
        self, callback_id: str, user_id: int, data: str
    ) -> None:
        parts = data.split(':')
        if len(parts) != 3:
            await self.api.answer_callback_query(callback_id, 'Некорректное действие.')
            return
        _, action, callback_key = parts
        if action == 'r':
            vacancy = self.store.get_vacancy(callback_key)
            if vacancy is None:
                await self.api.answer_callback_query(
                    callback_id, 'Вакансия недоступна.'
                )
                return
            await self.api.answer_callback_query(
                callback_id, 'Выберите причину в личном чате.'
            )
            try:
                await self.api.send_message(
                    user_id,
                    'Что не так с этой вакансией? Сигнал увидит только администратор.',
                    reply_markup=report_keyboard(callback_key),
                )
            except Exception:
                logging.info('Нельзя отправить пользователю форму жалобы до /start')
            return
        status_action = STATUS_ACTIONS.get(action)
        if status_action is None:
            await self.api.answer_callback_query(callback_id, 'Некорректное действие.')
            return
        status, confirmation = status_action
        vacancy = self.store.set_status(user_id, callback_key, status)
        await self.api.answer_callback_query(
            callback_id, confirmation if vacancy else 'Вакансия недоступна.'
        )

    async def _handle_report_callback(
        self, callback_id: str, user_id: int, data: str
    ) -> None:
        parts = data.split(':')
        if len(parts) != 3 or parts[2] not in REPORT_ACTIONS:
            await self.api.answer_callback_query(callback_id, 'Некорректная причина.')
            return
        _, callback_key, reason_code = parts
        saved = self.store.add_report(
            user_id, callback_key, REPORT_ACTIONS[reason_code]
        )
        await self.api.answer_callback_query(
            callback_id,
            'Спасибо, сигнал сохранён.' if saved else 'Вакансия недоступна.',
        )
