#!/usr/bin/env python3
"""
Скрипт для парсинга исторических сообщений за последнюю неделю
из целевых Telegram-каналов с фильтрацией и анализом через Mistral.
"""

import asyncio
import argparse
from collections import Counter
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telethon import TelegramClient

from tg_vacancy_bot import config
from tg_vacancy_bot.admin.telemetry import TelemetryStore
from tg_vacancy_bot.admin.control import finish_action
from tg_vacancy_bot.llm.universal import analyze_ingestion_text
from tg_vacancy_bot.logging_config import configure_logging
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.prefilter import contains_keywords, universal_prefilter
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.storage.sheets import (
    append_to_google_sheet,
    get_existing_links,
)
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
from tg_vacancy_bot.telegram.links import (
    build_vacancy_id,
    get_message_channel_name,
    get_message_link,
)
from tg_vacancy_bot.telegram.candidate_notifier import build_candidate_notifier

# Настройка логирования
configure_logging(
    log_format='%(asctime)s - %(levelname)s - %(message)s',
    data_dir=config.DATA_DIR,
)

# Инициализация клиента
client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)


async def parse_history(
    since: datetime | None = None, until: datetime | None = None
) -> dict[str, int]:
    """Re-run a bounded history window safely using stable source IDs."""
    config.validate_required_settings(require_sources=True)

    # Сначала подключаем Telegram: при занятой session не обращаемся к другим API.
    await client.start()

    # Google Таблица читается только один раз за время работы процесса.
    dedupe_state = JsonlDedupeState(
        path=config.STATE_FILE_PATH,
        ttl_days=config.TEXT_HASH_TTL_DAYS,
    )
    dedupe_state.exported_links.update(await get_existing_links())
    processor = VacancyProcessor(
        keyword_filter=(
            (lambda text: contains_keywords(text, config.KEYWORD_FILTER))
            if config.LEGACY_GO_ANALYSIS
            else universal_prefilter
        ),
        analyze_text=analyze_ingestion_text,
        registry=VacancyRegistry(config.CANDIDATE_BOT_DB_PATH),
        append_to_sheet=append_to_google_sheet,
        dedupe_state=dedupe_state,
        notify_vacancy=build_candidate_notifier(),
        exclude_keywords=config.EXCLUDE_KEYWORDS,
        event_recorder=TelemetryStore(config.DATA_DIR),
        group_store=VacancyGroupStore(
            config.VACANCY_GROUPS_DB_PATH,
            config.VACANCY_GROUP_WINDOW_DAYS,
        ),
    )

    logging.info(
        "Telegram card publishing for history: %s",
        "enabled" if processor.notify_vacancy is not None else "disabled",
    )

    # Принудительно кэшируем диалоги для корректной работы с приватными каналами
    await client.get_dialogs()

    await client.get_me()
    logging.info("✅ Telegram-сессия подключена")

    # Вычисляем дату недели назад (с timezone для корректного сравнения)
    one_week_ago = since or datetime.now(timezone.utc) - timedelta(
        days=config.HISTORY_DAYS
    )
    logging.info(f"📅 Парсим сообщения с {one_week_ago.strftime('%Y-%m-%d %H:%M:%S')}")

    total_messages = 0
    total_filtered = 0
    total_matched = 0
    total_failed = 0
    analyzed = 0
    confirmed = 0
    registered = 0
    published = 0
    unresolved = 0
    unavailable_reasons: Counter[str] = Counter()

    # Итерируем по каждому каналу
    for channel_number, channel_identifier in enumerate(
        config.TARGET_CHANNELS, start=1
    ):
        try:
            logging.info("Обработка Telegram-источника %d", channel_number)

            channel_messages = 0
            filtered_before = processor.keyword_matches
            matched_before = processor.saved_matches

            # Получаем сообщения за последнюю неделю
            async for message in client.iter_messages(
                channel_identifier, offset_date=datetime.now(), reverse=False
            ):
                # Проверяем дату сообщения
                if message.date < one_week_ago:
                    break
                if until is not None and message.date >= until:
                    continue

                total_messages += 1
                channel_messages += 1

                post_link = get_message_link(message)
                try:
                    text = message.text or ''
                    vacancy_id = build_vacancy_id(post_link)
                    published_before = (
                        processor.registry.candidates.channel_delivery_state(vacancy_id)
                    )
                    before = processor.analysis_calls
                    before_prefilter = processor.keyword_matches
                    await processor.process_message(
                        text,
                        text,
                        post_link,
                        message.date,
                        get_message_channel_name(message),
                    )
                    analyzed += processor.analysis_calls - before
                    entry = (
                        processor.registry.get(vacancy_id)
                        if processor.keyword_matches > before_prefilter
                        else None
                    )
                    if entry is not None:
                        registered += 1
                        if entry.decision is not None and entry.eligible_at is not None:
                            confirmed += 1
                        elif entry.unavailable_reason:
                            unresolved += 1
                            unavailable_reasons[entry.unavailable_reason] += 1
                    if (
                        published_before != 'published'
                        and processor.registry.candidates.channel_delivery_state(
                            vacancy_id
                        )
                        == 'published'
                    ):
                        published += 1
                except Exception as error:
                    total_failed += 1
                    logging.error(
                        "❌ Ошибка при анализе сообщения (%s)",
                        type(error).__name__,
                    )

            channel_filtered = processor.keyword_matches - filtered_before
            channel_matched = processor.saved_matches - matched_before
            total_filtered += channel_filtered
            total_matched += channel_matched

            logging.info(
                f"📊 Telegram-источник {channel_number}:\n"
                f"   Всего сообщений: {channel_messages}\n"
                f"   С ключевыми словами: {channel_filtered}\n"
                f"   Релевантных вакансий: {channel_matched}"
            )

        except Exception as error:
            total_failed += 1
            logging.error(
                "Ошибка при обработке Telegram-источника %d (%s)",
                channel_number,
                type(error).__name__,
            )
            continue

    logging.info(
        f"\n\n🎯 ИТОГОВАЯ СТАТИСТИКА:\n"
        f"   Всего обработано сообщений: {total_messages}\n"
        f"   С ключевыми словами go/golang: {total_filtered}\n"
        f"   Релевантных вакансий найдено: {total_matched}\n"
        f"   Результаты сохранены в Google Таблицу"
    )
    logging.info('History unavailable reasons: %s', dict(unavailable_reasons))
    return {
        'received': total_messages,
        'created': total_matched,
        'updated': 0,
        'skipped': max(0, total_messages - total_matched - total_failed),
        'failed': total_failed,
        'prefilter_passed': total_filtered,
        'analyzed': analyzed,
        'confirmed': confirmed,
        'registered': registered,
        'published': published,
        'unresolved': unresolved + total_failed,
    }


async def main() -> None:
    """Запускает history parser и гарантированно закрывает Telegram-клиент."""
    action_id = os.getenv('ADMIN_ACTION_ID')
    parser = argparse.ArgumentParser(description='Idempotent Telegram history replay')
    parser.add_argument('--since', help='Inclusive ISO date or UTC timestamp')
    parser.add_argument('--until', help='Exclusive ISO date or UTC timestamp')
    parser.add_argument(
        '--hold-delivery',
        action='store_true',
        help='Keep automatic personal delivery paused for digest reconciliation',
    )
    args = parser.parse_args()

    def parse_boundary(value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )

    since, until = parse_boundary(args.since), parse_boundary(args.until)
    if since and until and since >= until:
        parser.error('--since must precede --until')
    if args.hold_delivery:
        hold = Path(config.DATA_DIR) / 'candidate-delivery.hold'
        hold.parent.mkdir(parents=True, exist_ok=True)
        hold.touch(exist_ok=True)
    try:
        counts = await parse_history(since, until)
        logging.info('History stage report: %s', counts)
        if action_id:
            finish_action(
                action_id,
                succeeded=True,
                data_dir=config.DATA_DIR,
                counts=counts,
            )
    except Exception as error:
        if action_id:
            finish_action(
                action_id,
                succeeded=False,
                data_dir=config.DATA_DIR,
                error='Историческая обработка не завершена; проверьте безопасные логи.',
                counts={'failed': 1},
            )
            logging.error(
                'Историческая обработка не завершена (%s)', type(error).__name__
            )
        else:
            raise
    finally:
        if client.is_connected():
            await client.disconnect()
    if os.getenv('ADMIN_HISTORY_RESTART') == '1':
        os.execv(sys.executable, [sys.executable, 'scripts/run_live.py'])


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except sqlite3.OperationalError as exc:
        if "database is locked" not in str(exc).lower():
            raise
        logging.error(
            "Telegram session %s.session занята другим процессом. "
            "Остановите live listener/другой Telegram-скрипт или задайте "
            "отдельный SESSION_NAME, затем повторите make history.",
            config.SESSION_NAME,
        )
        raise SystemExit(1) from None
