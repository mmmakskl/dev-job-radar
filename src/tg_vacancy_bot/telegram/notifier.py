"""Отправка кратких уведомлений о вакансиях в Telegram."""

import html
import logging
from datetime import datetime
from typing import Any

from tg_vacancy_bot.models import NOT_SPECIFIED, VacancyAnalysis


MAX_MESSAGE_LENGTH = 3500
MAX_SUMMARY_LENGTH = 700


def _known(value: str | None) -> bool:
    return bool(value and value != NOT_SPECIFIED)


def _escape(value: str, limit: int = 250) -> str:
    """Экранирует пользовательский текст и ограничивает его длину."""
    escaped: list[str] = []
    length = 0
    for character in value:
        encoded = html.escape(character, quote=True)
        if length + len(encoded) > limit:
            break
        escaped.append(encoded)
        length += len(encoded)
    return "".join(escaped)


def _grade_label(data: VacancyAnalysis) -> str | None:
    grades = [value for value in (data.grade_from, data.grade_to) if _known(value)]
    if not grades:
        return None
    if len(grades) == 1 or grades[0] == grades[1]:
        return grades[0]
    return "–".join(grades)


def _location_label(data: VacancyAnalysis) -> str | None:
    values: list[str] = []
    seen: set[str] = set()
    for value in (data.city, data.country, data.hiring_geography):
        if not _known(value) or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        values.append(value)
    return ", ".join(values) if values else None


def _number_label(value: float | int) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _salary_label(data: VacancyAnalysis) -> str | None:
    if data.salary_from is None and data.salary_to is None:
        return None
    suffix = ""
    if _known(data.currency):
        suffix = data.currency
    if _known(data.salary_period):
        suffix = f"{suffix}/{data.salary_period}" if suffix else data.salary_period
    suffix = f" {suffix}" if suffix else ""
    if data.salary_from is not None and data.salary_to is not None:
        if data.salary_from == data.salary_to:
            return f"{_number_label(data.salary_from)}{suffix}"
        return (
            f"{_number_label(data.salary_from)}–"
            f"{_number_label(data.salary_to)}{suffix}"
        )
    if data.salary_from is not None:
        return f"от {_number_label(data.salary_from)}{suffix}"
    return f"до {_number_label(data.salary_to)}{suffix}"


def format_vacancy_notification(
    *,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    published_at: datetime,
) -> str:
    """Формирует компактное HTML-уведомление без неуказанных полей."""
    del vacancy_id, channel_name, published_at
    title = data.title if _known(data.title) else "Go вакансия"
    lines = [f"<b>🟢 {_escape(title, 180)}</b>"]

    fields = [
        ("Компания", data.company if _known(data.company) else None),
        ("Грейд", _grade_label(data)),
        ("Формат", data.work_format if _known(data.work_format) else None),
        ("Локация", _location_label(data)),
        ("Зарплата", _salary_label(data)),
    ]
    stack = [
        value
        for value in data.required_stack + data.preferred_stack
        if _known(value)
    ]
    if stack:
        fields.append(("Стек", ", ".join(stack[:6])))
    for label, value in fields:
        if value:
            lines.append(f"<b>{label}:</b> {_escape(value)}")

    if _known(data.summary):
        lines.extend(["", "<b>Кратко:</b>", _escape(data.summary, MAX_SUMMARY_LENGTH)])

    apply_link = data.apply_link if _known(data.apply_link) else post_link
    lines.extend(
        [
            "",
            f'<a href="{_escape(apply_link, 300)}">Откликнуться</a> · '
            f'<a href="{_escape(post_link, 300)}">Источник</a>',
        ]
    )
    message = "\n".join(lines)
    return message[:MAX_MESSAGE_LENGTH]


async def send_vacancy_notification(
    *,
    client: Any,
    target: str | int,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    published_at: datetime,
) -> bool:
    """Отправляет уведомление и не пробрасывает ошибки в основной pipeline."""
    message = format_vacancy_notification(
        vacancy_id=vacancy_id,
        post_link=post_link,
        channel_name=channel_name,
        data=data,
        published_at=published_at,
    )
    try:
        await client.send_message(
            target,
            message,
            parse_mode="html",
            link_preview=False,
        )
    except Exception:
        logging.exception("Не удалось отправить Telegram-уведомление: %s", vacancy_id)
        return False
    return True
