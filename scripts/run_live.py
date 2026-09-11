"""
Основной модуль системы агрегации вакансий из Telegram
Использует Telethon для мониторинга каналов и обработки новых сообщений
"""

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime

from telethon import TelegramClient, events, utils

from tg_vacancy_bot import config
from tg_vacancy_bot.admin.alerts import AlertDispatcher
from tg_vacancy_bot.admin.control import (
    claim_action,
    finish_action,
    recover_running_actions,
)
from tg_vacancy_bot.admin.settings import SettingsStore
from tg_vacancy_bot.channel_sync import fetch_folder_channels
from tg_vacancy_bot.admin.telemetry import TelemetryStore
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
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
from tg_vacancy_bot.telegram.links import (
    get_event_channel_name,
    get_event_message_link,
)
from tg_vacancy_bot.telegram.bot_api import TelegramBotApi
from tg_vacancy_bot.telegram.candidate_notifier import (
    CandidateVacancyNotifier,
)
from tg_vacancy_bot.telegram.candidate_store import CandidateStore

# Настройка логирования
configure_logging(
    log_format='%(asctime)s [%(levelname)s] %(message)s',
    date_format='%Y-%m-%d %H:%M:%S',
    data_dir=config.DATA_DIR,
)

# Инициализация клиента
client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

dedupe_state = JsonlDedupeState(
    path=config.STATE_FILE_PATH,
    ttl_days=config.TEXT_HASH_TTL_DAYS,
)


def build_live_notifier():
    """Creates the sole vacancy publication path: an interactive Bot API card."""
    if config.CANDIDATE_BOT_ENABLED:
        return CandidateVacancyNotifier(
            TelegramBotApi(config.CANDIDATE_BOT_TOKEN),
            CandidateStore(config.CANDIDATE_BOT_DB_PATH),
            config.CANDIDATE_BOT_CHANNEL,
        )
    return None


processor = VacancyProcessor(
    keyword_filter=lambda text: contains_keywords(text, config.KEYWORD_FILTER),
    analyze_text=analyze_text,
    append_to_sheet=append_to_google_sheet,
    dedupe_state=dedupe_state,
    notify_vacancy=build_live_notifier(),
    exclude_keywords=config.EXCLUDE_KEYWORDS,
    event_recorder=TelemetryStore(config.DATA_DIR),
    group_store=VacancyGroupStore(
        config.VACANCY_GROUPS_DB_PATH,
        config.VACANCY_GROUP_WINDOW_DAYS,
    ),
)

message_queue: asyncio.Queue["LiveMessageJob"] = asyncio.Queue(
    maxsize=config.LIVE_QUEUE_MAXSIZE,
)
QUEUE_WARNING_THRESHOLD = max(1, int(config.LIVE_QUEUE_MAXSIZE * 0.8))
SHUTDOWN_TIMEOUT_SECONDS = 30
received_messages = 0
accepting_messages = True
alert_dispatcher: AlertDispatcher | None = None


@dataclass(frozen=True)
class LiveMessageJob:
    """Данные Telegram-сообщения, необходимые общему processor."""

    text: str
    raw_text: str
    post_link: str
    published_at: datetime
    channel_name: str


async def handle_new_message(event):
    """Быстро ставит новое Telegram-сообщение в очередь."""
    global received_messages
    if not accepting_messages:
        logging.info("Не принимаем новое Telegram-сообщение: выполняется остановка")
        return
    received_messages += 1
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
            "Live-очередь заполнена (%d/%d), сообщение пропущено",
            message_queue.qsize(),
            message_queue.maxsize,
        )
        processor._metric('skipped_invalid', 'telegram', 'live_queue_full')
        if alert_dispatcher is not None:
            await alert_dispatcher.check(
                monitoring_enabled=config.MONITORING_ENABLED,
                queue_size=message_queue.qsize(),
                queue_maxsize=message_queue.maxsize,
                queue_warning_percent=config.ALERT_QUEUE_WARNING_PERCENT,
                error_streak_threshold=config.ALERT_ERROR_STREAK_THRESHOLD,
                error_window_seconds=config.ALERT_ERROR_WINDOW_SECONDS,
                no_export_seconds=config.ALERT_NO_EXPORT_SECONDS,
                heartbeat_stale_seconds=config.ALERT_HEARTBEAT_STALE_SECONDS,
            )
        return

    queue_size = message_queue.qsize()
    logging.info(
        "Сообщение поставлено в live-очередь (размер: %d)",
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
                "Live worker %d обрабатывает сообщение",
                worker_id,
            )
            await processor.process_message(
                job.text,
                job.raw_text,
                job.post_link,
                job.published_at,
                job.channel_name,
            )
        except Exception:
            processor._metric('processing_error', 'telegram')
            logging.exception(
                "Ошибка live worker %d при обработке сообщения",
                worker_id,
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


async def monitor_admin_control(
    shutdown_event: asyncio.Event,
    telemetry: TelemetryStore,
    store: SettingsStore,
) -> str | None:
    """Execute Telegram actions through the bot-owned Telethon session."""
    while not shutdown_event.is_set():
        requested = claim_action(config.DATA_DIR)
        if requested:
            action = requested['action']
            try:
                counts: dict[str, int] = {}
                if action == 'history':
                    shutdown_event.set()
                    return f"history:{requested['id']}"
                if action == 'sync_channels':
                    folder = store.load().telegram.folder_name
                    channels = await fetch_folder_channels(client, folder)
                    counts = store.sync_folder(channels)
                elif action == 'verify_source':
                    source = store.get_source(requested['target_id'])
                    target = source['username'] or int(source['telegram_id'])
                    entity = await client.get_entity(target)
                    store.mark_source_verified(
                        source['id'],
                        telegram_id=utils.get_peer_id(entity),
                        username=getattr(entity, 'username', None),
                        title=getattr(entity, 'title', None),
                        chat_type=(
                            'group'
                            if getattr(entity, 'megagroup', False)
                            else 'channel'
                        ),
                    )
                    counts = {'received': 1, 'updated': 1}
                finish_action(
                    requested['id'],
                    succeeded=True,
                    data_dir=config.DATA_DIR,
                    counts=counts,
                )
                telemetry.record('action_succeeded', action=action)
                logging.info('Команда админ-панели завершена: %s', action)
                shutdown_event.set()
                return action
            except Exception:
                if action == 'verify_source' and requested.get('target_id'):
                    store.mark_source_invalid(requested['target_id'])
                finish_action(
                    requested['id'],
                    succeeded=False,
                    data_dir=config.DATA_DIR,
                    error='Операция не выполнена; проверьте подключение Telegram и повторите.',
                    counts={'failed': 1},
                )
                telemetry.record('action_failed', action=action)
                logging.exception('Команда админ-панели не выполнена: %s', action)
        await asyncio.sleep(2)
    return None


async def publish_heartbeat(
    telemetry: TelemetryStore,
    shutdown_event: asyncio.Event,
) -> None:
    """Publish aggregate counters only; Telegram text never reaches the panel."""
    while not shutdown_event.is_set():
        telemetry.heartbeat(
            status='running',
            settings_revision=config.SETTINGS_REVISION,
            channel_count=len(config.TARGET_CHANNELS),
            queue_size=message_queue.qsize(),
            received_messages=received_messages,
            keyword_matches=processor.keyword_matches,
            saved_matches=processor.saved_matches,
        )
        if alert_dispatcher is not None:
            await alert_dispatcher.check(
                monitoring_enabled=config.MONITORING_ENABLED,
                queue_size=message_queue.qsize(),
                queue_maxsize=message_queue.maxsize,
                queue_warning_percent=config.ALERT_QUEUE_WARNING_PERCENT,
                error_streak_threshold=config.ALERT_ERROR_STREAK_THRESHOLD,
                error_window_seconds=config.ALERT_ERROR_WINDOW_SECONDS,
                no_export_seconds=config.ALERT_NO_EXPORT_SECONDS,
                heartbeat_stale_seconds=config.ALERT_HEARTBEAT_STALE_SECONDS,
            )
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            continue


async def main():
    """Основная функция запуска бота"""
    global accepting_messages, alert_dispatcher
    config.validate_required_settings(require_sources=False)
    accepting_messages = True
    shutdown_event = asyncio.Event()
    telemetry = TelemetryStore(config.DATA_DIR)
    settings = SettingsStore(config.DATA_DIR).load()
    store = SettingsStore(config.DATA_DIR)
    recover_running_actions(config.DATA_DIR)
    removed = telemetry.cleanup(
        logs_days=settings.retention.logs_days,
        errors_days=settings.retention.errors_days,
        operations_days=settings.retention.operations_days,
        metrics_days=settings.retention.metrics_days,
    )
    if any(removed.values()):
        telemetry.record('retention_cleanup', **removed)

    async def send_alert(message: str) -> bool:
        if not config.TELEGRAM_NOTIFY_ENABLED:
            return False
        try:
            await client.send_message(
                config.TELEGRAM_NOTIFY_TARGET,
                f'⚠️ Go Radar: {message}',
                link_preview=False,
            )
        except Exception:
            logging.exception('Не удалось отправить эксплуатационный Telegram-алерт')
            return False
        return True

    alert_dispatcher = AlertDispatcher(
        telemetry=telemetry,
        send=send_alert,
        enabled=(config.ALERTS_ENABLED and config.TELEGRAM_NOTIFY_ENABLED),
        cooldown_seconds=config.ALERT_COOLDOWN_SECONDS,
        data_dir=config.DATA_DIR,
    )
    install_shutdown_signal_handlers(shutdown_event)
    if config.LIVE_QUEUE_MAXSIZE <= 0:
        raise RuntimeError("LIVE_QUEUE_MAXSIZE должен быть больше нуля")
    if config.LIVE_WORKERS <= 0:
        raise RuntimeError("LIVE_WORKERS должен быть больше нуля")

    logging.info("=" * 60)
    logging.info("Система агрегации вакансий из Telegram")
    logging.info("=" * 60)
    logging.info("Отслеживаемых каналов: %d", len(store.active_targets()))
    logging.info(f"Фильтр ключевых слов: {', '.join(config.KEYWORD_FILTER)}")
    if config.TELEGRAM_NOTIFY_ENABLED:
        logging.info(
            "Telegram notifications: enabled (target: %s)",
            '[configured]',
        )
    else:
        logging.info("Telegram notifications: disabled")
    logging.info(
        'Candidate Bot API cards: %s',
        'enabled' if config.CANDIDATE_BOT_ENABLED else 'disabled',
    )
    logging.info(
        "Live-очередь: maxsize=%d, workers=%d",
        config.LIVE_QUEUE_MAXSIZE,
        config.LIVE_WORKERS,
    )
    if config.LIVE_WORKERS != 1:
        logging.warning("Для последовательной обработки рекомендуется LIVE_WORKERS=1")
    logging.info("=" * 60)

    worker_tasks: list[asyncio.Task] = []
    control_task: asyncio.Task[str | None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    requested_action: str | None = None
    premium_task: asyncio.Task | None = None
    try:
        await client.start()
        await client.get_dialogs()
        try:
            channels = await asyncio.wait_for(
                fetch_folder_channels(client, settings.telegram.folder_name), timeout=30
            )
            store.sync_folder(channels)
        except Exception:
            logging.warning(
                'Стартовая синхронизация папки не выполнена; используются последние сохранённые источники.'
            )
        active_targets = store.active_targets()
        if not active_targets:
            raise RuntimeError('Нет включённых Telegram-источников')
        client.add_event_handler(
            handle_new_message, events.NewMessage(chats=active_targets)
        )
        control_task = asyncio.create_task(
            monitor_admin_control(shutdown_event, telemetry, store),
            name='admin-control-monitor',
        )
        if not config.MONITORING_ENABLED:
            telemetry.heartbeat(
                status='paused', settings_revision=config.SETTINGS_REVISION
            )
            logging.info('Live-мониторинг приостановлен через админ-панель.')
            await shutdown_event.wait()
            return
        # Google Таблица читается только один раз за время работы процесса.
        dedupe_state.exported_links.update(await get_existing_links())
        worker_tasks = [
            asyncio.create_task(
                live_worker(worker_id),
                name=f"live-worker-{worker_id}",
            )
            for worker_id in range(1, config.LIVE_WORKERS + 1)
        ]

        from tg_vacancy_bot.premium_search.settings import database_path, enabled

        if enabled():
            from tg_vacancy_bot.premium_search.service import PremiumSearchService
            from tg_vacancy_bot.premium_search.store import PremiumSearchStore

            premium = PremiumSearchService(
                client,
                PremiumSearchStore(database_path(config.DATA_DIR)),
                processor,
                telemetry,
                live_queue=message_queue,
                settings_store=store,
            )
            premium_task = asyncio.create_task(
                premium.serve(shutdown_event), name='premium-search'
            )

        await client.get_me()
        logging.info("Telegram-сессия авторизована")
        logging.info("Бот запущен. Ожидаю новые сообщения...")

        await alert_dispatcher.check(
            monitoring_enabled=config.MONITORING_ENABLED,
            queue_size=message_queue.qsize(),
            queue_maxsize=message_queue.maxsize,
            queue_warning_percent=config.ALERT_QUEUE_WARNING_PERCENT,
            error_streak_threshold=config.ALERT_ERROR_STREAK_THRESHOLD,
            error_window_seconds=config.ALERT_ERROR_WINDOW_SECONDS,
            no_export_seconds=config.ALERT_NO_EXPORT_SECONDS,
            heartbeat_stale_seconds=config.ALERT_HEARTBEAT_STALE_SECONDS,
        )

        telemetry.heartbeat(
            status='running',
            settings_revision=config.SETTINGS_REVISION,
            channel_count=len(config.TARGET_CHANNELS),
            queue_size=message_queue.qsize(),
        )
        heartbeat_task = asyncio.create_task(
            publish_heartbeat(telemetry, shutdown_event),
            name='admin-heartbeat',
        )

        shutdown_requested = await wait_for_disconnect_or_shutdown(
            client, shutdown_event
        )
        if shutdown_requested:
            accepting_messages = False
    finally:
        if premium_task:
            premium_task.cancel()
            await asyncio.gather(premium_task, return_exceptions=True)
        await stop_workers(worker_tasks)
        if client.is_connected():
            logging.info("Очередь завершена, отключаем Telegram...")
            await client.disconnect()
        if control_task:
            if not control_task.done():
                control_task.cancel()
            result = await asyncio.gather(control_task, return_exceptions=True)
            if result and isinstance(result[0], str):
                requested_action = result[0]
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        telemetry.heartbeat(
            status='stopped', settings_revision=config.SETTINGS_REVISION
        )
        if requested_action and requested_action.startswith('history:'):
            telemetry.record('history_started')
            os.environ['ADMIN_HISTORY_RESTART'] = '1'
            os.environ['ADMIN_ACTION_ID'] = requested_action.split(':', 1)[1]
            os.execv(sys.executable, [sys.executable, 'scripts/parse_history.py'])
        if requested_action in {'sync_channels', 'verify_source', 'restart'}:
            os.execv(sys.executable, [sys.executable, 'scripts/run_live.py'])


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановка бота...")
