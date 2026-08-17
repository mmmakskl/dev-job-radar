"""Publishing of interactive shared-channel cards through the Telegram Bot API."""

import logging
from datetime import datetime

from tg_vacancy_bot.models import NOT_SPECIFIED, VacancyAnalysis
from tg_vacancy_bot.telegram.bot_api import TelegramBotApi
from tg_vacancy_bot.telegram.candidate_store import CandidateStore
from tg_vacancy_bot.telegram.notifier import format_vacancy_notification


def _known(value: str | None) -> bool:
    return bool(value and value != NOT_SPECIFIED)


def channel_keyboard(callback_key: str, post_link: str) -> dict[str, list[list[dict]]]:
    """Builds compact callback data that contains no secret and stays under 64 bytes."""
    return {
        'inline_keyboard': [
            [
                {'text': 'Открыть', 'url': post_link},
                {'text': 'Сохранить', 'callback_data': f'v:s:{callback_key}'},
            ],
            [
                {'text': 'Откликнулся', 'callback_data': f'v:a:{callback_key}'},
                {'text': 'Не подходит', 'callback_data': f'v:h:{callback_key}'},
            ],
            [{'text': 'Пожаловаться', 'callback_data': f'v:r:{callback_key}'}],
        ]
    }


class CandidateVacancyNotifier:
    """Sends a Bot API card after export and makes it available in personal views."""

    def __init__(
        self, api: TelegramBotApi, store: CandidateStore, target: str | int
    ) -> None:
        self.api = api
        self.store = store
        self.target = target

    async def __call__(
        self,
        *,
        vacancy_id: str,
        post_link: str,
        channel_name: str,
        data: VacancyAnalysis,
        published_at: datetime,
    ) -> bool:
        """Publishes the shared card; failure never interrupts the ingestion pipeline."""
        vacancy = self.store.register_vacancy(
            vacancy_id=vacancy_id,
            title=data.title if _known(data.title) else 'Go вакансия',
            company=data.company if _known(data.company) else None,
            summary=data.summary if _known(data.summary) else None,
            post_link=post_link,
            apply_link=data.apply_link if _known(data.apply_link) else None,
            published_at=published_at.isoformat(),
        )
        if not self.store.claim_channel_delivery(vacancy_id):
            logging.info('Карточка вакансии уже была заявлена: %s', vacancy_id)
            return True
        message = format_vacancy_notification(
            vacancy_id=vacancy_id,
            post_link=post_link,
            channel_name=channel_name,
            data=data,
            published_at=published_at,
        )
        try:
            sent = await self.api.send_message(
                self.target,
                message,
                parse_mode='HTML',
                reply_markup=channel_keyboard(vacancy.callback_key, post_link),
            )
        except Exception:
            logging.exception('Не удалось отправить пользовательскую Telegram-карточку')
            return False
        message_id = sent.get('message_id') if isinstance(sent, dict) else None
        self.store.mark_channel_published(
            vacancy_id, message_id if isinstance(message_id, int) else None
        )
        return True
