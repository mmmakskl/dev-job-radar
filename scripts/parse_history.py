#!/usr/bin/env python3
"""
Скрипт для парсинга исторических сообщений за последнюю неделю
из целевых Telegram-каналов с фильтрацией и анализом через Mistral.
"""
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

from tg_vacancy_bot import config
from tg_vacancy_bot.admin.telemetry import TelemetryStore
from tg_vacancy_bot.llm.mistral import analyze_text
from tg_vacancy_bot.logging_config import configure_logging
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.prefilter import contains_keywords
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.storage.sheets import (
    append_to_google_sheet,
    get_existing_links,
)
from tg_vacancy_bot.telegram.links import (
    get_message_channel_name,
    get_message_link,
)
from tg_vacancy_bot.telegram.notifier import send_vacancy_notification

# Настройка логирования
configure_logging(
    log_format='%(asctime)s - %(levelname)s - %(message)s',
    data_dir=config.DATA_DIR,
)

# Инициализация клиента
client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)


def build_history_notifier():
    """Включает history-уведомления только отдельным явным флагом."""
    if not (config.TELEGRAM_NOTIFY_ENABLED and config.TELEGRAM_NOTIFY_HISTORY):
        return None

    async def notify_vacancy(**kwargs) -> bool:
        return await send_vacancy_notification(
            client=client,
            target=config.TELEGRAM_NOTIFY_TARGET,
            **kwargs,
        )

    return notify_vacancy


async def parse_history():
    """Парсит историю сообщений за последнюю неделю."""
    config.validate_required_settings()

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
        notify_vacancy=build_history_notifier(),
        exclude_keywords=config.EXCLUDE_KEYWORDS,
        event_recorder=TelemetryStore(config.DATA_DIR),
    )

    logging.info(
        "Telegram notifications for history: %s",
        "enabled" if processor.notify_vacancy is not None else "disabled",
    )

    # Принудительно кэшируем диалоги для корректной работы с приватными каналами
    await client.get_dialogs()

    me = await client.get_me()
    logging.info(f"✅ Подключен как: {me.first_name} (@{me.username})")

    # Вычисляем дату недели назад (с timezone для корректного сравнения)
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)
    logging.info(f"📅 Парсим сообщения с {one_week_ago.strftime('%Y-%m-%d %H:%M:%S')}")

    total_messages = 0
    total_filtered = 0
    total_matched = 0

    # Итерируем по каждому каналу
    for channel_identifier in config.TARGET_CHANNELS:
        try:
            logging.info(f"\n🔍 Обработка канала: {channel_identifier}")

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
                except Exception as e:
                    logging.error(f"❌ Ошибка при анализе сообщения {post_link}: {e}")

            channel_filtered = processor.keyword_matches - filtered_before
            channel_matched = processor.saved_matches - matched_before
            total_filtered += channel_filtered
            total_matched += channel_matched

            logging.info(
                f"📊 Канал {channel_identifier}:\n"
                f"   Всего сообщений: {channel_messages}\n"
                f"   С ключевыми словами: {channel_filtered}\n"
                f"   Релевантных вакансий: {channel_matched}"
            )

        except Exception as e:
            logging.error(f"❌ Ошибка при обработке канала {channel_identifier}: {e}")
            continue

    logging.info(
        f"\n\n🎯 ИТОГОВАЯ СТАТИСТИКА:\n"
        f"   Всего обработано сообщений: {total_messages}\n"
        f"   С ключевыми словами go/golang: {total_filtered}\n"
        f"   Релевантных вакансий найдено: {total_matched}\n"
        f"   Результаты сохранены в Google Таблицу"
    )


async def main() -> None:
    """Запускает history parser и гарантированно закрывает Telegram-клиент."""
    try:
        await parse_history()
    finally:
        if client.is_connected():
            await client.disconnect()


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
