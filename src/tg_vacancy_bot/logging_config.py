"""Общая настройка логирования приложения."""

import logging

from tg_vacancy_bot.admin.telemetry import SafeLogHandler, TelemetryStore


def configure_logging(
    log_format: str,
    date_format: str | None = None,
    data_dir: str | None = None,
) -> None:
    """Configure console logs plus a redacted, read-only admin log journal."""
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
    )
    root = logging.getLogger()
    if not any(
        getattr(handler, '_go_radar_safe_log', False) for handler in root.handlers
    ):
        handler = SafeLogHandler(TelemetryStore(data_dir)).handler
        handler._go_radar_safe_log = True  # type: ignore[attr-defined]
        root.addHandler(handler)
