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
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.candidate_delivery_worker import (
    CandidateDeliveryWorker,
    PersonalDeliveryStore,
)
from tg_vacancy_bot.telegram.candidate_search import CandidateSearch
from tg_vacancy_bot.search.store import SearchStore
from tg_vacancy_bot.search.settings import search_database_path
from tg_vacancy_bot.premium_search.settings import enabled as premium_enabled

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
    store = CandidateStore(config.CANDIDATE_BOT_DB_PATH)
    registry = VacancyRegistry(config.CANDIDATE_BOT_DB_PATH)
    profile_users = (
        config.CANDIDATE_PROFILE_ALLOWED_USER_IDS
        & config.CANDIDATE_BOT_ALLOWED_USER_IDS
    )
    search = CandidateSearch(
        api,
        store,
        SearchStore(search_database_path(config.DATA_DIR)),
        enabled=config.CANDIDATE_CATALOG_SEARCH_ENABLED,
        premium_enabled=premium_enabled(),
        allowed_user_ids=profile_users,
    )
    delivery = CandidateDeliveryWorker(
        api,
        registry,
        PersonalDeliveryStore(config.CANDIDATE_BOT_DB_PATH),
        enabled=config.CANDIDATE_PROFILE_DELIVERY_ENABLED,
        allowed_user_ids=profile_users,
    )
    # The personal feed follows CANDIDATE_BOT_ALLOWED_USER_IDS for every track.
    bot = CandidateBot(
        api,
        store,
        config.CANDIDATE_BOT_ALLOWED_USER_IDS,
        registry=registry,
        profile_allowed_user_ids=profile_users,
        search=search,
    )
    heartbeat_path = Path(config.DATA_DIR) / 'admin' / 'candidate-heartbeat.json'
    logging.info('Пользовательский Bot API запущен в режиме long polling.')
    task = asyncio.create_task(bot.run(shutdown_event), name='candidate-bot-polling')
    delivery_task = asyncio.create_task(
        delivery.run(shutdown_event), name='candidate-profile-delivery'
    )
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
        delivery_task.cancel()
        shutdown_task.cancel()
        await asyncio.gather(
            task, heartbeat_task, delivery_task, shutdown_task, return_exceptions=True
        )
        write_heartbeat(heartbeat_path, 'stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info('Пользовательский Bot API остановлен.')
