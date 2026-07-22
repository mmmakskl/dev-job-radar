"""Общая настройка логирования приложения."""

import logging


def configure_logging(log_format: str, date_format: str | None = None) -> None:
    """Настроить INFO-логирование с переданными форматами."""
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
    )
