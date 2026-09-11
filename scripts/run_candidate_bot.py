#!/usr/bin/env python3
"""Starts the isolated Telegram Bot API long-polling worker for beta candidates."""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

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


def write_heartbeat(path: Path, status: str) -> None:
    """Atomically records readiness without exposing Telegram identifiers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'status': status,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix='.candidate-heartbeat.', text=True
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


async def publish_heartbeat(path: Path, shutdown_event: asyncio.Event) -> None:
    """Keeps the candidate-worker readiness signal fresh."""
    while not shutdown_event.is_set():
        write_heartbeat(path, 'running')
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            continue


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
    heartbeat_path = Path(config.DATA_DIR) / 'admin' / 'candidate-heartbeat.json'
    logging.info('Пользовательский Bot API запущен в режиме long polling.')
    task = asyncio.create_task(bot.run(shutdown_event), name='candidate-bot-polling')
    heartbeat_task = asyncio.create_task(
        publish_heartbeat(heartbeat_path, shutdown_event),
        name='candidate-bot-heartbeat',
    )
    shutdown_task = asyncio.create_task(
        shutdown_event.wait(), name='candidate-bot-shutdown'
    )
    try:
        completed, _ = await asyncio.wait(
            {task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if task in completed:
            await task
    finally:
        task.cancel()
        heartbeat_task.cancel()
        shutdown_task.cancel()
        await asyncio.gather(
            task, heartbeat_task, shutdown_task, return_exceptions=True
        )
        write_heartbeat(heartbeat_path, 'stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info('Пользовательский Bot API остановлен.')
