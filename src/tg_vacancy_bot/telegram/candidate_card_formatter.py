"""Pure HTML rendering for confirmed personal matches; no delivery side effects."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tg_vacancy_bot.models import NOT_SPECIFIED, VacancyAnalysis, normalize_stack

MAX_MESSAGE_UNITS = 3500
_UTC = datetime_timezone.utc


@dataclass(frozen=True)
class MatchedProfile:
    """An already confirmed match to an active profile."""

    profile_id: str
    name: str


@dataclass(frozen=True)
class CardInput:
    vacancy_id: str
    analysis: VacancyAnalysis
    published_at: datetime | str | None
    post_link: str | None
    matched_profiles: tuple[MatchedProfile, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "matched_profiles", tuple(self.matched_profiles))


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    vacancy_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "vacancy_ids", tuple(self.vacancy_ids))


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _known(value: str | None) -> str:
    cleaned = (value or "").strip()
    return "" if cleaned.casefold() == NOT_SPECIFIED.casefold() else cleaned


def _plain(value: str, *, budget: int, chars: int | None = None) -> str:
    """Shorten raw text, then escape; budget includes escaped HTML in UTF-16."""
    if (chars is None or len(value) <= chars) and _units(escape(value)) <= budget:
        return escape(value)
    # Reserve the ellipsis before scanning so neither entities nor astral Unicode
    # characters can be split by the message size limit.
    available = budget - 1
    length = 0
    used = 0
    for character in value:
        cost = _units(escape(character))
        if used + cost > available or (chars is not None and length >= chars - 1):
            break
        length += 1
        used += cost
    return escape(value[:length].rstrip() + "…")


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(_UTC)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name}") from exc


def _published(value: datetime | str | None) -> datetime | None:
    if isinstance(value, str):
        # Require an ISO date-time rather than accepting date-only strings.
        if not re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}", value):
            return None
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        return None
    return value.astimezone(_UTC)


def _plural(number: int, forms: tuple[str, str, str]) -> str:
    if 11 <= number % 100 <= 14:
        return forms[2]
    if number % 10 == 1:
        return forms[0]
    if 2 <= number % 10 <= 4:
        return forms[1]
    return forms[2]


def _exact(moment: datetime, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    offset = local.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:5]}" + (
        f":{offset[5:]}" if len(offset) > 5 else ""
    )
    return f"{local:%d.%m.%Y %H:%M} (UTC{offset})"


def _publication(value: datetime | str | None, now: datetime, zone: ZoneInfo) -> str:
    published = _published(value)
    if published is None:
        return "Дата публикации не указана"
    exact = _exact(published, zone)
    seconds = (now - published).total_seconds()
    if seconds < 0:
        return f"Опубликовано {exact}"
    if seconds < 60:
        age = "только что"
    else:
        if seconds < 3600:
            number, forms = int(seconds // 60), ("минуту", "минуты", "минут")
        elif seconds < 86400:
            number, forms = int(seconds // 3600), ("час", "часа", "часов")
        else:
            number, forms = int(seconds // 86400), ("день", "дня", "дней")
        age = f"{number} {_plural(number, forms)} назад"
    return f"Опубликовано {age} · {exact}"


def _profile_names(profiles: Iterable[MatchedProfile]) -> str:
    ids: set[str] = set()
    names: set[str] = set()
    result: list[str] = []
    for profile in profiles:
        if profile.profile_id in ids:
            continue
        ids.add(profile.profile_id)
        name = _known(profile.name)
        if name and name.casefold() not in names:
            names.add(name.casefold())
            result.append(name)
    return ", ".join(result)


def _valid_url(value: str | None) -> str | None:
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    if any(char in value for char in '\\<>"'):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        # Accessing port also validates malformed ports and out-of-range values.
        parsed.port
        host = parsed.hostname
        if ":" not in host:
            labels = host.encode("idna").decode("ascii").split(".")
            if any(
                not re.fullmatch(
                    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label
                )
                for label in labels
            ):
                return None
        if re.search(r"%(?![0-9a-fA-F]{2})", value):
            return None
    except (ValueError, UnicodeError):
        return None
    return value


def _link(card: CardInput, budget: int) -> str:
    for value, label in (
        (card.analysis.apply_link, "Откликнуться ↗"),
        (card.post_link, "Исходная вакансия ↗"),
    ):
        url = _valid_url(value)
        if url:
            html = f'<a href="{escape(url, quote=True)}">{label}</a>'
            if _units(html) <= budget:
                return html
    return ""


def _heading(card: CardInput, title_budget: int, company_budget: int) -> str:
    title = _plain(_known(card.analysis.title) or "Вакансия", budget=title_budget)
    company = _known(card.analysis.company)
    if company:
        title += " — " + _plain(company, budget=company_budget)
    return f"<b>{title}</b>"


def format_card(
    card: CardInput, *, now: datetime, timezone: str = "Europe/Moscow"
) -> str:
    """Render one card, with source publication time and at most 3500 UTF-16 units."""
    current, zone = _aware(now, "now"), _zone(timezone)
    analysis = card.analysis
    lines = [_heading(card, 300, 200)]
    location = list(
        dict.fromkeys(
            value
            for value in (
                _known(analysis.city),
                _known(analysis.country),
                _known(analysis.hiring_geography),
                _known(analysis.work_format),
            )
            if value
        )
    )
    if location:
        lines.append("📍 " + _plain(" · ".join(location), budget=300))
    grades = list(
        dict.fromkeys(
            value
            for value in (_known(analysis.grade_from), _known(analysis.grade_to))
            if value
        )
    )
    if grades:
        lines.append("🎓 " + _plain("–".join(grades), budget=100))
    stack = normalize_stack(
        [
            value
            for value in analysis.required_stack + analysis.preferred_stack
            if _known(value)
        ]
    )[:6]
    if stack:
        lines.append("🛠 " + _plain(" · ".join(stack), budget=360))
    summary = _known(analysis.summary)
    if summary:
        lines.append(_plain(summary, budget=500, chars=240))
    profiles = _profile_names(card.matched_profiles)
    if profiles:
        lines.append("🎯 Профили: " + _plain(profiles, budget=650))
    lines.append("🕒 " + escape(_publication(card.published_at, current, zone)))
    text = "\n".join(lines)
    link = _link(card, min(1000, MAX_MESSAGE_UNITS - _units(text) - 1))
    return text + ("\n" + link if link else "")


def _compact(card: CardInput, now: datetime, zone: ZoneInfo) -> str:
    lines = [_heading(card, 240, 160)]
    profiles = _profile_names(card.matched_profiles)
    if profiles:
        lines.append("🎯 Профили: " + _plain(profiles, budget=350))
    lines.append("🕒 " + escape(_publication(card.published_at, now, zone)))
    link = _link(card, 600)
    if link:
        lines.append(link)
    return "\n".join(lines)


def format_hourly_digest(
    cards: Iterable[CardInput],
    *,
    window_end: datetime,
    now: datetime,
    timezone: str = "Europe/Moscow",
    history: bool = False,
) -> tuple[RenderedMessage, ...]:
    """Pack unique vacancies between whole records; return each page's identities.

    The displayed period is the preceding hour. A delivery batch may also include
    backlog; each record always displays its own source publication timestamp.
    """
    current, end, zone = (
        _aware(now, "now"),
        _aware(window_end, "window_end"),
        _zone(timezone),
    )
    unique: dict[str, CardInput] = {}
    for card in cards:
        if card.vacancy_id not in unique:
            unique[card.vacancy_id] = card
        else:
            first = unique[card.vacancy_id]
            unique[card.vacancy_id] = CardInput(
                first.vacancy_id,
                first.analysis,
                first.published_at,
                first.post_link,
                first.matched_profiles + card.matched_profiles,
            )
    if not unique:
        return ()
    header = (
        f"<b>История вакансий</b>\nУникальных вакансий: {len(unique)}"
        if history
        else (
            "<b>Часовая сводка</b>\n"
            f"Период: {_exact(end - timedelta(hours=1), zone)} — {_exact(end, zone)}\n"
            f"Уникальных вакансий: {len(unique)}"
        )
    )
    pages: list[RenderedMessage] = []
    text, ids = header, []
    for card in unique.values():
        record = _compact(card, current, zone)
        if _units(text + "\n\n" + record) > MAX_MESSAGE_UNITS:
            pages.append(RenderedMessage(text, tuple(ids)))
            text, ids = header, []
        text += "\n\n" + record
        ids.append(card.vacancy_id)
    pages.append(RenderedMessage(text, tuple(ids)))
    return tuple(pages)


def format_legacy_card(
    vacancy, *, now: datetime, timezone: str = 'Europe/Moscow'
) -> str:
    """Render only stored legacy fields, without manufacturing a classification."""
    current, zone = _aware(now, 'now'), _zone(timezone)
    lines = [f'<b>{_plain(vacancy.title or "Вакансия", budget=500)}</b>']
    if _known(vacancy.company):
        lines.append('Компания: ' + _plain(vacancy.company, budget=300))
    if _known(vacancy.summary):
        lines.append(_plain(vacancy.summary, budget=800, chars=500))
    lines.append('🕒 ' + escape(_publication(vacancy.published_at, current, zone)))
    for value in (vacancy.apply_link, vacancy.post_link):
        url = _valid_url(value)
        if url:
            link = f'<a href="{escape(url, quote=True)}">Открыть оригинал ↗</a>'
            if _units(link) <= 1000:
                lines.append(link)
                break
    return '\n'.join(lines)
