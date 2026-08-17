"""Personal candidate workflow driven by Telegram Bot API long polling."""

import asyncio
import html
import logging
from dataclasses import dataclass, replace
from typing import Any, Protocol

from tg_vacancy_bot.telegram.candidate_store import (
    BrowserSession,
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

    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict: ...

    async def answer_callback_query(
        self, callback_query_id: str, text: str
    ) -> None: ...


MAIN_KEYBOARD = {
    'keyboard': [['Новые', 'Мои отклики'], ['Сохранённые', 'Скрытые']],
    'resize_keyboard': True,
}
BUCKET_LABELS = {
    'new': 'Новые',
    'saved': 'Сохранённые',
    'applications': 'Мои отклики',
    'hidden': 'Скрытые',
}
EMPTY_BUCKET_TEXT = {
    'new': 'Новых вакансий пока нет.',
    'saved': 'В сохранённых вакансиях пока ничего нет.',
    'applications': 'В откликах пока ничего нет.',
    'hidden': 'В скрытых вакансиях пока ничего нет.',
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
    's': 'suspicious',
    'd': 'duplicate',
    'o': 'outdated',
    't': 'other',
}


def personal_keyboard(callback_key: str, post_link: str) -> dict[str, list[list[dict]]]:
    """Keeps direct channel actions separate from the private list browser."""
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


def vacancy_text(vacancy: CandidateVacancy, heading: str | None = None) -> str:
    """Renders a compact personal view without exposing other users' actions."""
    lines = [heading] if heading else []
    lines.append(f'<b>{html.escape(vacancy.title)}</b>')
    if vacancy.company:
        lines.append(f'Компания: {html.escape(vacancy.company)}')
    if vacancy.summary:
        lines.extend(['', html.escape(vacancy.summary[:500])])
    lines.extend(['', f'<b>Статус:</b> {html.escape(vacancy.status)}'])
    return '\n'.join(lines)


def direct_report_keyboard(callback_key: str) -> dict[str, list[list[dict]]]:
    """Keeps complaints from a shared channel private to the reporting user."""
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


@dataclass(frozen=True)
class BrowserPage:
    """One current result page resolved from private persisted browser state."""

    session: BrowserSession
    vacancy: CandidateVacancy
    position: int
    total: int


class CandidateVacancyBrowser:
    """Reusable single-card browser for every private vacancy bucket."""

    def __init__(self, api: CandidateBotApi, store: CandidateStore) -> None:
        self.api = api
        self.store = store

    async def open(self, chat_id: int, user_id: int, bucket: str) -> None:
        vacancies = self.store.list_for_user(user_id, bucket)
        if not vacancies:
            await self.api.send_message(chat_id, EMPTY_BUCKET_TEXT[bucket])
            return
        session = self.store.create_browser_session(user_id, bucket, vacancies)
        page = self._page(session, vacancies)
        sent = await self.api.send_message(
            chat_id,
            self._text(page),
            parse_mode='HTML',
            reply_markup=self._keyboard(page),
        )
        message_id = sent.get('message_id') if isinstance(sent, dict) else None
        if isinstance(message_id, int):
            self.store.attach_browser_message(
                session.token, user_id, chat_id, message_id
            )

    async def handle_callback(
        self,
        callback: dict[str, Any],
        user_id: int,
        callback_id: str,
        data: str,
    ) -> bool:
        """Handles opaque b:<token>:<operation> callbacks for one private card."""
        parts = data.split(':')
        if len(parts) != 3:
            await self.api.answer_callback_query(callback_id, 'Некорректное действие.')
            return True
        _, token, operation = parts
        session = self.store.get_browser_session(token, user_id)
        message = callback.get('message') or {}
        if (
            session is None
            or session.chat_id != (message.get('chat') or {}).get('id')
            or session.message_id != message.get('message_id')
        ):
            await self.api.answer_callback_query(
                callback_id, 'Эта карточка больше неактуальна.'
            )
            return True
        if operation in {'prev', 'next'}:
            await self._navigate(session, user_id, callback_id, operation)
            return True
        if operation == 'report':
            await self._edit_report_choice(session, user_id, callback_id)
            return True
        if operation == 'back':
            await self._render_current(session, user_id, callback_id)
            return True
        if operation.startswith('q') and operation[1:] in REPORT_ACTIONS:
            await self._save_report(session, user_id, callback_id, operation[1:])
            return True
        status_action = STATUS_ACTIONS.get(operation)
        if status_action is None:
            await self.api.answer_callback_query(callback_id, 'Некорректное действие.')
            return True
        await self._change_status(
            session, user_id, callback_id, status_action[0], status_action[1]
        )
        return True

    async def _navigate(
        self, session: BrowserSession, user_id: int, callback_id: str, direction: str
    ) -> None:
        vacancies = self._session_vacancies(session, user_id)
        if not vacancies:
            await self._edit_empty(session, callback_id)
            return
        position = min(session.position, len(vacancies) - 1)
        requested = position - 1 if direction == 'prev' else position + 1
        if requested < 0 or requested >= len(vacancies):
            await self.api.answer_callback_query(
                callback_id,
                (
                    'Это первая вакансия.'
                    if direction == 'prev'
                    else 'Это последняя вакансия.'
                ),
            )
            return
        self.store.set_browser_position(session.token, user_id, requested)
        await self._edit_page(
            BrowserPage(session, vacancies[requested], requested, len(vacancies)),
            callback_id,
        )

    async def _change_status(
        self,
        session: BrowserSession,
        user_id: int,
        callback_id: str,
        status: str,
        confirmation: str,
    ) -> None:
        page = self._current_page(session, user_id)
        if page is None:
            await self._edit_empty(session, callback_id)
            return
        updated = self.store.set_status(user_id, page.vacancy.callback_key, status)
        if updated is None:
            await self.api.answer_callback_query(callback_id, 'Вакансия недоступна.')
            return
        await self.api.answer_callback_query(callback_id, confirmation)
        await self._render_current(session, user_id)

    async def _edit_report_choice(
        self, session: BrowserSession, user_id: int, callback_id: str
    ) -> None:
        page = self._current_page(session, user_id)
        if page is None:
            await self._edit_empty(session, callback_id)
            return
        await self.api.answer_callback_query(callback_id, 'Выберите причину.')
        await self.api.edit_message_text(
            session.chat_id or user_id,
            session.message_id or 0,
            f'{self._text(page)}\n\n<b>Что не так с вакансией?</b>',
            parse_mode='HTML',
            reply_markup=self._report_keyboard(session.token),
        )

    async def _save_report(
        self,
        session: BrowserSession,
        user_id: int,
        callback_id: str,
        reason_code: str,
    ) -> None:
        page = self._current_page(session, user_id)
        if page is None:
            await self._edit_empty(session, callback_id)
            return
        saved = self.store.add_report(
            user_id, page.vacancy.callback_key, REPORT_ACTIONS[reason_code]
        )
        await self.api.answer_callback_query(
            callback_id,
            'Спасибо, сигнал сохранён.' if saved else 'Вакансия недоступна.',
        )
        await self._render_current(session, user_id)

    async def _render_current(
        self, session: BrowserSession, user_id: int, callback_id: str | None = None
    ) -> None:
        page = self._current_page(session, user_id)
        if page is None:
            await self._edit_empty(session, callback_id)
            return
        await self._edit_page(page, callback_id)

    async def _edit_page(self, page: BrowserPage, callback_id: str | None) -> None:
        if callback_id:
            await self.api.answer_callback_query(callback_id, '')
        await self.api.edit_message_text(
            page.session.chat_id or page.session.telegram_user_id,
            page.session.message_id or 0,
            self._text(page),
            parse_mode='HTML',
            reply_markup=self._keyboard(page),
        )

    async def _edit_empty(
        self, session: BrowserSession, callback_id: str | None
    ) -> None:
        if callback_id:
            await self.api.answer_callback_query(callback_id, 'Список пуст.')
        await self.api.edit_message_text(
            session.chat_id or session.telegram_user_id,
            session.message_id or 0,
            EMPTY_BUCKET_TEXT[session.bucket],
            reply_markup=None,
        )

    def _current_page(
        self, session: BrowserSession, user_id: int
    ) -> BrowserPage | None:
        vacancies = self._session_vacancies(session, user_id)
        if not vacancies:
            return None
        position = min(session.position, len(vacancies) - 1)
        if position != session.position:
            self.store.set_browser_position(session.token, user_id, position)
        return BrowserPage(
            replace(session, position=position),
            vacancies[position],
            position,
            len(vacancies),
        )

    def _session_vacancies(
        self, session: BrowserSession, user_id: int
    ) -> list[CandidateVacancy]:
        available = {
            vacancy.vacancy_id: vacancy
            for vacancy in self.store.list_for_user(user_id, session.bucket)
        }
        return [
            available[vacancy_id]
            for vacancy_id in session.vacancy_ids
            if vacancy_id in available
        ]

    @staticmethod
    def _page(
        session: BrowserSession, vacancies: list[CandidateVacancy]
    ) -> BrowserPage:
        return BrowserPage(session, vacancies[0], 0, len(vacancies))

    @staticmethod
    def _text(page: BrowserPage) -> str:
        label = BUCKET_LABELS[page.session.bucket]
        return vacancy_text(
            page.vacancy, f'<b>{label} · {page.position + 1} из {page.total}</b>'
        )

    @staticmethod
    def _keyboard(page: BrowserPage) -> dict[str, list[list[dict]]]:
        token = page.session.token
        return {
            'inline_keyboard': [
                [
                    {'text': 'Открыть', 'url': page.vacancy.post_link},
                    {'text': 'Сохранить', 'callback_data': f'b:{token}:s'},
                    {'text': 'Откликнулся', 'callback_data': f'b:{token}:a'},
                ],
                [
                    {'text': 'Ответили', 'callback_data': f'b:{token}:p'},
                    {'text': 'Интервью', 'callback_data': f'b:{token}:i'},
                    {'text': 'Оффер', 'callback_data': f'b:{token}:o'},
                ],
                [
                    {'text': 'Отказ', 'callback_data': f'b:{token}:x'},
                    {'text': 'В новые', 'callback_data': f'b:{token}:n'},
                    {'text': 'Скрыть', 'callback_data': f'b:{token}:h'},
                ],
                [{'text': 'Пожаловаться', 'callback_data': f'b:{token}:report'}],
                [
                    {'text': '‹ Назад', 'callback_data': f'b:{token}:prev'},
                    {
                        'text': f'{BUCKET_LABELS[page.session.bucket]} · {page.position + 1}/{page.total}',
                        'callback_data': f'b:{token}:z',
                    },
                    {'text': 'Вперёд ›', 'callback_data': f'b:{token}:next'},
                ],
            ]
        }

    @staticmethod
    def _report_keyboard(token: str) -> dict[str, list[list[dict]]]:
        return {
            'inline_keyboard': [
                [
                    {'text': 'Подозрительная', 'callback_data': f'b:{token}:qs'},
                    {'text': 'Дубликат', 'callback_data': f'b:{token}:qd'},
                ],
                [
                    {'text': 'Неактуальна', 'callback_data': f'b:{token}:qo'},
                    {'text': 'Другое', 'callback_data': f'b:{token}:qt'},
                ],
                [{'text': 'Назад к вакансии', 'callback_data': f'b:{token}:back'}],
            ]
        }


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
        self.browser = CandidateVacancyBrowser(api, store)

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
        if not isinstance(user_id, int) or not isinstance(chat_id, int):
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
            await self.browser.open(chat_id, user_id, buckets[text])
        else:
            await self.api.send_message(
                chat_id,
                'Используйте кнопки «Новые», «Мои отклики», «Сохранённые» или «Скрытые».',
                reply_markup=MAIN_KEYBOARD,
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
        if data.startswith('b:'):
            await self.browser.handle_callback(callback, user_id, callback_id, data)
        elif data.startswith('v:'):
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
                    reply_markup=direct_report_keyboard(callback_key),
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
        if len(parts) != 3 or parts[2] not in {'sus', 'dup', 'old', 'oth'}:
            await self.api.answer_callback_query(callback_id, 'Некорректная причина.')
            return
        _, callback_key, reason_code = parts
        reason = {
            'sus': 'suspicious',
            'dup': 'duplicate',
            'old': 'outdated',
            'oth': 'other',
        }[reason_code]
        saved = self.store.add_report(user_id, callback_key, reason)
        await self.api.answer_callback_query(
            callback_id,
            'Спасибо, сигнал сохранён.' if saved else 'Вакансия недоступна.',
        )
