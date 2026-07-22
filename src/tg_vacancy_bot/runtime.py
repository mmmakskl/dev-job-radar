"""Async runtime helpers for graceful process shutdown."""

import asyncio
import logging
import signal
from collections.abc import Iterable


def install_shutdown_signal_handlers(
    shutdown_event: asyncio.Event,
    *,
    signals: Iterable[signal.Signals] = (signal.SIGTERM, signal.SIGINT),
) -> None:
    """Bridges Unix termination signals into an asyncio event."""
    loop = asyncio.get_running_loop()
    for shutdown_signal in signals:
        try:
            loop.add_signal_handler(shutdown_signal, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            logging.warning(
                "Не удалось установить asyncio handler для %s",
                shutdown_signal.name,
            )


async def wait_for_disconnect_or_shutdown(client, shutdown_event: asyncio.Event) -> None:
    """Waits for Telegram disconnect or requests it after SIGTERM/SIGINT."""
    connection_task = asyncio.create_task(
        client.run_until_disconnected(),
        name="telegram-until-disconnected",
    )
    shutdown_task = asyncio.create_task(
        shutdown_event.wait(),
        name="process-shutdown-signal",
    )
    try:
        done, _ = await asyncio.wait(
            {connection_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task in done and shutdown_event.is_set():
            logging.info("Получен сигнал остановки, отключаем Telegram...")
            if client.is_connected():
                await client.disconnect()
        await connection_task
    finally:
        for task in (connection_task, shutdown_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            connection_task,
            shutdown_task,
            return_exceptions=True,
        )
