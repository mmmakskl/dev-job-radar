"""Общая настройка логирования приложения."""

import logging

from tg_vacancy_bot.admin.telemetry import SafeLogHandler, TelemetryStore, sanitize_text


class RedactingConsoleFilter(logging.Filter):
    """Sanitize formatted records before console/container handlers emit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = sanitize_text(record.getMessage())
        if record.exc_info:
            message = f'{message} ({record.exc_info[0].__name__})'
            record.exc_info = None
            record.exc_text = None
        record.msg = message
        record.args = ()
        return True


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
    for existing_handler in root.handlers:
        if not getattr(existing_handler, '_go_radar_redacting', False):
            existing_handler.addFilter(RedactingConsoleFilter())
            existing_handler._go_radar_redacting = True  # type: ignore[attr-defined]
    if not any(
        getattr(handler, '_go_radar_safe_log', False) for handler in root.handlers
    ):
        handler = SafeLogHandler(TelemetryStore(data_dir)).handler
        handler._go_radar_safe_log = True  # type: ignore[attr-defined]
        root.addHandler(handler)
