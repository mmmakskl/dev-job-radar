import asyncio

from tg_vacancy_bot.admin.alerts import AlertDispatcher
from tg_vacancy_bot.admin.telemetry import TelemetryStore


def test_alert_dispatcher_cooldown_and_recovery(tmp_path) -> None:
    sent: list[str] = []

    async def send(message: str) -> bool:
        sent.append(message)
        return True

    dispatcher = AlertDispatcher(
        telemetry=TelemetryStore(str(tmp_path)),
        send=send,
        enabled=True,
        cooldown_seconds=3600,
        data_dir=str(tmp_path),
    )

    asyncio.run(dispatcher.observe('queue_pressure', True, 'Очередь высокая.'))
    asyncio.run(dispatcher.observe('queue_pressure', True, 'Очередь высокая.'))
    asyncio.run(dispatcher.observe('queue_pressure', False, 'Очередь нормальна.'))

    assert sent == ['Очередь высокая.', '✅ Восстановление: Очередь нормальна.']


def test_alert_check_detects_service_error_streak(tmp_path) -> None:
    sent: list[str] = []

    async def send(message: str) -> bool:
        sent.append(message)
        return True

    store = TelemetryStore(str(tmp_path))
    for _ in range(3):
        store.record_metric('processing_error', 'llm', 'llm_error')
    dispatcher = AlertDispatcher(
        telemetry=store,
        send=send,
        enabled=True,
        cooldown_seconds=60,
        data_dir=str(tmp_path),
    )

    asyncio.run(
        dispatcher.check(
            monitoring_enabled=True,
            queue_size=0,
            queue_maxsize=100,
            queue_warning_percent=80,
            error_streak_threshold=3,
            error_window_seconds=900,
            no_export_seconds=300,
            heartbeat_stale_seconds=120,
        )
    )

    assert any('Mistral' in message for message in sent)
