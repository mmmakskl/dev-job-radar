"""Telegram notification entry points backed by the channel-card formatter."""

import logging
from datetime import datetime
from typing import Any

from tg_vacancy_bot.models import VacancyAnalysis
from tg_vacancy_bot.telegram.vacancy_channel_formatter import (
    MAX_DESCRIPTION_LENGTH,
    MAX_MESSAGE_LENGTH as FORMATTER_MAX_MESSAGE_LENGTH,
    SourcePresentation,
    VacancyChannelFormatter,
)

# Kept for import compatibility with callers of the original notifier module.
MAX_MESSAGE_LENGTH = FORMATTER_MAX_MESSAGE_LENGTH
MAX_SUMMARY_LENGTH = MAX_DESCRIPTION_LENGTH


def format_vacancy_notification(
    *,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    published_at: datetime,
) -> str:
    """Format a shared-channel card while preserving the public call signature."""
    del vacancy_id, channel_name, published_at
    return VacancyChannelFormatter().format(
        data=data,
        source=SourcePresentation(label="Telegram", url=post_link),
    )


async def send_vacancy_notification(
    *,
    client: Any,
    target: str | int,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    published_at: datetime,
) -> bool:
    """Send a formatted card without propagating errors to the main pipeline."""
    message = format_vacancy_notification(
        vacancy_id=vacancy_id,
        post_link=post_link,
        channel_name=channel_name,
        data=data,
        published_at=published_at,
    )
    try:
        await client.send_message(
            target,
            message,
            parse_mode="html",
            link_preview=False,
        )
    except Exception:
        logging.exception("Не удалось отправить Telegram-уведомление")
        return False
    return True
