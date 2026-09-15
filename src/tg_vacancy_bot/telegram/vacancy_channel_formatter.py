"""Presentation of compact vacancy cards for shared Telegram channels."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from tg_vacancy_bot.models import NOT_SPECIFIED, VacancyAnalysis, normalize_stack

MAX_MESSAGE_LENGTH = 3500
MAX_DESCRIPTION_LENGTH = 400
MAX_STACK_ITEMS = 6
MAX_TAGS = 4


@dataclass(frozen=True)
class SourcePresentation:
    """The human-readable source shown at the bottom of a vacancy card."""

    label: str
    url: str | None = None


def _known(value: str | None) -> bool:
    return bool(value and value.strip() and value != NOT_SPECIFIED)


def _truncate(value: str, limit: int, *, word_boundary: bool = False) -> str:
    """Trim source text before escaping, marking only an actual truncation."""
    value = value.strip()
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    shortened = value[: limit - 1]
    if word_boundary:
        boundary = re.search(r"\s+\S*$", shortened)
        if boundary and boundary.start() > 0:
            shortened = shortened[: boundary.start()].rstrip()
    return f"{shortened}…"


def _escape(value: str, limit: int, *, word_boundary: bool = False) -> str:
    return html.escape(_truncate(value, limit, word_boundary=word_boundary), quote=True)


def _grade_label(data: VacancyAnalysis) -> str | None:
    grades = [value for value in (data.grade_from, data.grade_to) if _known(value)]
    if not grades:
        return None
    if len(grades) == 1 or grades[0].casefold() == grades[1].casefold():
        return grades[0]
    return "–".join(grades)


def _location_label(data: VacancyAnalysis) -> str | None:
    values: list[str] = []
    seen: set[str] = set()
    for value in (data.city, data.country, data.hiring_geography):
        if not _known(value):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return ", ".join(values) if values else None


def _number_label(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _salary_label(data: VacancyAnalysis) -> str | None:
    if data.salary_from is None and data.salary_to is None:
        return None
    if data.salary_from is not None and data.salary_to is not None:
        amount = (
            _number_label(data.salary_from)
            if data.salary_from == data.salary_to
            else f"{_number_label(data.salary_from)}–{_number_label(data.salary_to)}"
        )
    elif data.salary_from is not None:
        amount = f"от {_number_label(data.salary_from)}"
    else:
        amount = f"до {_number_label(data.salary_to)}"

    details: list[str] = []
    if _known(data.currency):
        details.append(data.currency)
    if _known(data.salary_period):
        details.append(data.salary_period.lower())
    return f"{amount} {'/'.join(details)}" if details else amount


def _location_line(data: VacancyAnalysis, location: str | None) -> str | None:
    work_format = data.work_format if _known(data.work_format) else None
    if not location and not work_format:
        return None
    if work_format == "Remote":
        parts = ["🌍 Remote"]
        if location:
            parts.append(location)
        return " · ".join(parts)
    parts = ["📍"]
    if location:
        parts.append(location)
    if work_format:
        parts.append(work_format)
    return " ".join(parts[:2]) + (f" · {parts[2]}" if len(parts) == 3 else "")


def _safe_tag(value: str | None) -> str | None:
    if not _known(value):
        return None
    normalized = re.sub(r"[^\w]+", "_", value.casefold(), flags=re.UNICODE)
    normalized = normalized.strip("_")
    if not normalized:
        return None
    return f"#{_truncate(normalized, 48)}"


class VacancyChannelFormatter:
    """Formats an adaptive, HTML-safe detailed vacancy card."""

    def format(self, *, data: VacancyAnalysis, source: SourcePresentation) -> str:
        title = data.title if _known(data.title) else "Go Developer"
        company = data.company if _known(data.company) else None
        header = f"<b>🟢 {_escape(title, 80)}"
        if company:
            header += f" — {_escape(company, 60)}"
        lines = [f"{header}</b>"]

        salary = _salary_label(data)
        if salary:
            lines.append(f"💰 {_escape(salary, 80)}")

        location = _location_label(data)
        location_line = _location_line(data, location)
        if location_line:
            lines.append(f"{location_line[:2]}{_escape(location_line[2:], 70)}")

        grade = _grade_label(data)
        grade_values = [
            value for value in (data.grade_from, data.grade_to) if _known(value)
        ]
        grade_in_title = bool(
            grade_values
            and all(value.casefold() in title.casefold() for value in grade_values)
        )
        employment = data.employment_type if _known(data.employment_type) else None
        details = [
            value for value in (None if grade_in_title else grade, employment) if value
        ]
        if details:
            lines.append(f"🧭 {_escape(' · '.join(details), 80)}")

        stack = normalize_stack(data.required_stack + data.preferred_stack)[
            :MAX_STACK_ITEMS
        ]
        if stack:
            lines.append(f"🛠 {_escape(' · '.join(stack), 160)}")

        summary = data.summary if _known(data.summary) else None
        if summary:
            lines.append(_escape(summary, MAX_DESCRIPTION_LENGTH, word_boundary=True))

        tags = ["#golang"]
        for value in (grade, data.work_format, location):
            tag = _safe_tag(value)
            if tag and tag not in tags:
                tags.append(tag)
            if len(tags) == MAX_TAGS:
                break
        lines.append(" ".join(tags))

        source_label = _escape(source.label, 50)
        if _known(source.url):
            source_url = html.escape(source.url.strip(), quote=True)
            lines.append(f'via <a href="{source_url}">{source_label}</a>')
        else:
            lines.append(f"via {source_label}")

        message = self._fit_message(lines.copy(), summary)
        if len(message) > MAX_MESSAGE_LENGTH and _known(source.url):
            # Do not corrupt a URL merely to fit the message. For an
            # exceptionally long source URL, preserve a valid compact card
            # and the source label instead of emitting a broken hyperlink.
            fallback_lines = lines.copy()
            fallback_lines[-1] = f"via {source_label}"
            message = self._fit_message(fallback_lines, summary)
        return message

    @staticmethod
    def _fit_message(lines: list[str], summary: str | None) -> str:
        """Keep valid markup while reserving the Bot API's message-size margin."""
        message = "\n".join(lines)
        if len(message) <= MAX_MESSAGE_LENGTH or not summary:
            return message

        summary_index = len(lines) - 3
        fixed_length = len(message) - len(lines[summary_index])
        available = MAX_MESSAGE_LENGTH - fixed_length
        if available <= 1:
            del lines[summary_index]
            return "\n".join(lines)

        raw_limit = min(MAX_DESCRIPTION_LENGTH, max(1, available // 6))
        while raw_limit > 1:
            rendered = _escape(summary, raw_limit, word_boundary=True)
            if len(rendered) <= available:
                lines[summary_index] = rendered
                return "\n".join(lines)
            raw_limit -= 1
        del lines[summary_index]
        return "\n".join(lines)
