#!/usr/bin/env python3
"""
Скрипт для парсинга исторических сообщений за последнюю неделю
из целевых Telegram-каналов с фильтрацией и анализом через Mistral.
"""

import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

from tg_vacancy_bot import config
from tg_vacancy_bot.admin.telemetry import TelemetryStore
from tg_vacancy_bot.admin.control import finish_action
from tg_vacancy_bot.llm.mistral import analyze_text
from tg_vacancy_bot.logging_config import configure_logging
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.prefilter import contains_keywords
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.storage.sheets import (
    append_to_google_sheet,
    get_existing_links,
)
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
from tg_vacancy_bot.telegram.links import (
    get_message_channel_name,
    get_message_link,
)

# Настройка логирования
configure_logging(
    log_format='%(asctime)s - %(levelname)s - %(message)s',
    data_dir=config.DATA_DIR,
)

# Инициализация клиента
client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)


async def parse_history() -> dict[str, int]:
    """Парсит историю сообщений за последнюю неделю."""
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
        keyword_filter=lambda text: contains_keywords(text, config.KEYWORD_FILTER),
        analyze_text=analyze_text,
        append_to_sheet=append_to_google_sheet,
        dedupe_state=dedupe_state,
        notify_vacancy=None,
        exclude_keywords=config.EXCLUDE_KEYWORDS,
        event_recorder=TelemetryStore(config.DATA_DIR),
        group_store=VacancyGroupStore(
            config.VACANCY_GROUPS_DB_PATH,
            config.VACANCY_GROUP_WINDOW_DAYS,
        ),
    )

    logging.info("Telegram notifications for history: disabled")

    # Принудительно кэшируем диалоги для корректной работы с приватными каналами
    await client.get_dialogs()

    await client.get_me()
    logging.info("✅ Telegram-сессия подключена")

    # Вычисляем дату недели назад (с timezone для корректного сравнения)
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)
    logging.info(f"📅 Парсим сообщения с {one_week_ago.strftime('%Y-%m-%d %H:%M:%S')}")

    total_messages = 0
    total_filtered = 0
    total_matched = 0
    total_failed = 0

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

                total_messages += 1
                channel_messages += 1

                post_link = get_message_link(message)
                try:
                    text = message.text or ''
                    await processor.process_message(
                        text,
                        text,
                        post_link,
                        message.date,
                        get_message_channel_name(message),
                    )
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
    return {
        'received': total_messages,
        'created': total_matched,
        'updated': 0,
        'skipped': max(0, total_messages - total_matched - total_failed),
        'failed': total_failed,
    }


async def main() -> None:
    """Запускает history parser и гарантированно закрывает Telegram-клиент."""
    action_id = os.getenv('ADMIN_ACTION_ID')
    try:
        counts = await parse_history()
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
