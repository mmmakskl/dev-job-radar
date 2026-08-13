#!/usr/bin/env python3
"""Starts the isolated Telegram Bot API long-polling worker for beta candidates."""

import asyncio
import logging

from tg_vacancy_bot import config
from tg_vacancy_bot.logging_config import configure_logging
from tg_vacancy_bot.runtime import install_shutdown_signal_handlers
from tg_vacancy_bot.telegram.bot_api import TelegramBotApi
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


configure_logging(
    log_format='%(asctime)s [%(levelname)s] %(message)s',
    date_format='%Y-%m-%d %H:%M:%S',
    data_dir=config.DATA_DIR,
)


async def main() -> None:
    """Runs long polling until SIGINT or SIGTERM without using a webhook."""
    config.validate_candidate_bot_settings()
    shutdown_event = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_event)
    api = TelegramBotApi(config.CANDIDATE_BOT_TOKEN)
    await api.delete_webhook()
    bot = CandidateBot(
        api,
        CandidateStore(config.CANDIDATE_BOT_DB_PATH),
        config.CANDIDATE_BOT_ALLOWED_USER_IDS,
    )
    logging.info('Пользовательский Bot API запущен в режиме long polling.')
    task = asyncio.create_task(bot.run(shutdown_event), name='candidate-bot-polling')
    try:
        await shutdown_event.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info('Пользовательский Bot API остановлен.')
