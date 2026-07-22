"""
Основной модуль системы агрегации вакансий из Telegram
Использует Telethon для мониторинга каналов и обработки новых сообщений
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from telethon import TelegramClient, events

from tg_vacancy_bot import config
from tg_vacancy_bot.llm.mistral import analyze_text
from tg_vacancy_bot.logging_config import configure_logging
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.prefilter import contains_keywords
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.runtime import (
    install_shutdown_signal_handlers,
    wait_for_disconnect_or_shutdown,
)
from tg_vacancy_bot.storage.sheets import (
    append_to_google_sheet,
    get_existing_links,
)
from tg_vacancy_bot.telegram.links import (
    get_event_channel_name,
    get_event_message_link,
)


# Настройка логирования
configure_logging(
    log_format='%(asctime)s [%(levelname)s] %(message)s',
    date_format='%Y-%m-%d %H:%M:%S',
)

# Инициализация клиента
client = TelegramClient(
    config.SESSION_NAME,
    config.API_ID,
    config.API_HASH
)

dedupe_state = JsonlDedupeState(
    path=config.STATE_FILE_PATH,
    ttl_days=config.TEXT_HASH_TTL_DAYS,
)
processor = VacancyProcessor(
    keyword_filter=lambda text: contains_keywords(text, config.KEYWORD_FILTER),
    analyze_text=analyze_text,
    append_to_sheet=append_to_google_sheet,
    dedupe_state=dedupe_state,
)

message_queue: asyncio.Queue["LiveMessageJob"] = asyncio.Queue(
    maxsize=config.LIVE_QUEUE_MAXSIZE,
)
QUEUE_WARNING_THRESHOLD = max(1, int(config.LIVE_QUEUE_MAXSIZE * 0.8))
SHUTDOWN_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class LiveMessageJob:
    """Данные Telegram-сообщения, необходимые общему processor."""

    text: str
    raw_text: str
    post_link: str
    published_at: datetime
    channel_name: str


@client.on(events.NewMessage(chats=config.TARGET_CHANNELS))
async def handle_new_message(event):
    """Быстро ставит новое Telegram-сообщение в очередь."""
    message = event.message
    text = message.text or ''
    post_link = get_event_message_link(event)
    raw_text = event.raw_text or text
    job = LiveMessageJob(
        text=text,
        raw_text=raw_text,
        post_link=post_link,
        published_at=message.date,
        channel_name=get_event_channel_name(event),
    )

    try:
        message_queue.put_nowait(job)
    except asyncio.QueueFull:
        logging.error(
            "Live-очередь заполнена (%d/%d), сообщение пропущено: %s",
            message_queue.qsize(),
            message_queue.maxsize,
            post_link,
        )
        return

    queue_size = message_queue.qsize()
    logging.info(
        "Сообщение поставлено в live-очередь: %s (размер: %d)",
        post_link,
        queue_size,
    )
    if queue_size >= QUEUE_WARNING_THRESHOLD:
        logging.warning(
            "Live-очередь заполнена на 80%% или более: %d/%d",
            queue_size,
            message_queue.maxsize,
        )


async def live_worker(worker_id: int) -> None:
    """Последовательно обрабатывает сообщения из live-очереди."""
    logging.info("Live worker %d запущен", worker_id)
    while True:
        job = await message_queue.get()
        try:
            logging.info(
                "Live worker %d обрабатывает сообщение: %s",
                worker_id,
                job.post_link,
            )
            await processor.process_message(
                job.text,
                job.raw_text,
                job.post_link,
                job.published_at,
                job.channel_name,
            )
        except Exception:
            logging.exception(
                "Ошибка live worker %d при обработке %s",
                worker_id,
                job.post_link,
            )
        finally:
            message_queue.task_done()


async def stop_workers(worker_tasks: list[asyncio.Task]) -> None:
    """Даёт очереди завершиться и затем корректно отменяет workers."""
    if not worker_tasks:
        return

    logging.info(
        "Ожидаем завершения live-очереди (%d сообщений)...",
        message_queue.qsize(),
    )
    try:
        await asyncio.wait_for(
            message_queue.join(),
            timeout=SHUTDOWN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.warning(
            "Таймаут остановки: в live-очереди осталось %d сообщений",
            message_queue.qsize(),
        )
    finally:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        logging.info("Live workers остановлены")


async def main():
    """Основная функция запуска бота"""
    config.validate_required_settings()
    shutdown_event = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_event)
    if config.LIVE_QUEUE_MAXSIZE <= 0:
        raise RuntimeError("LIVE_QUEUE_MAXSIZE должен быть больше нуля")
    if config.LIVE_WORKERS <= 0:
        raise RuntimeError("LIVE_WORKERS должен быть больше нуля")

    logging.info("=" * 60)
    logging.info("Система агрегации вакансий из Telegram")
    logging.info("=" * 60)
    logging.info(f"Отслеживаемые каналы: {', '.join(str(ch) for ch in config.TARGET_CHANNELS)}")
    logging.info(f"Фильтр ключевых слов: {', '.join(config.KEYWORD_FILTER)}")
    logging.info(
        "Live-очередь: maxsize=%d, workers=%d",
        config.LIVE_QUEUE_MAXSIZE,
        config.LIVE_WORKERS,
    )
    if config.LIVE_WORKERS != 1:
        logging.warning(
            "Для последовательной обработки рекомендуется LIVE_WORKERS=1"
        )
    logging.info("=" * 60)

    worker_tasks: list[asyncio.Task] = []
    try:
        # Google Таблица читается только один раз за время работы процесса.
        dedupe_state.exported_links.update(await get_existing_links())
        worker_tasks = [
            asyncio.create_task(
                live_worker(worker_id),
                name=f"live-worker-{worker_id}",
            )
            for worker_id in range(1, config.LIVE_WORKERS + 1)
        ]

        await client.start()

        # Кэшируем диалоги для корректной работы с приватными каналами.
        await client.get_dialogs()

        me = await client.get_me()
        logging.info(f"Авторизован как: {me.first_name} (@{me.username})")
        logging.info("Бот запущен. Ожидаю новые сообщения...")

        await wait_for_disconnect_or_shutdown(client, shutdown_event)
    finally:
        if client.is_connected():
            await client.disconnect()
        await stop_workers(worker_tasks)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановка бота...")
