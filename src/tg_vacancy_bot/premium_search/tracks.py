"""Extensible deterministic track rules, independent of search query aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from tg_vacancy_bot.pipeline.prefilter import candidate_profile_reasons

_GO = re.compile(r'\b(?:golang|go[ -]?lang|go)\b', re.I)
_ROLE = re.compile(
    r'\b(?:golang|go)[ -]*(?:developer|engineer|разработчик\w*)|'
    r'\b(?:разработчик\w*|developer|engineer)\s+(?:на\s+)?(?:go|golang)\b',
    re.I,
)
_HIRING = re.compile(
    r'\b(?:hiring|vacanc\w*|job|we seek|we are looking|join our team|'
    r'ваканси\w*|ищем|нанимаем|требуется|приглашаем|обязанност\w*)\b',
    re.I,
)
_TECH = re.compile(
    r'\b(?:backend|software|programming|code|microservices|grpc|'
    r'разработ\w*|бэкенд|программирован\w*|микросервис\w*|стек|язык\w*)\b',
    re.I,
)
_EXCLUDE = re.compile(
    r'\b(?:ищу работу|ищу проект|open to work|my resume|мо[её] резюме|'
    r'курс\w*|вебинар\w*|конференци\w*|webinar|course|bootcamp|'
    r'подпишитесь|подписывайтесь|subscribe|реклам\w*|розыгрыш\w*)\b',
    re.I,
)
_OPTIONAL = re.compile(
    r'(?:optional|nice.to.have|бонус\w*|плюсом|желательно|необязател\w*)', re.I
)


def go_prefilter(text: str) -> str | None:
    if not text.strip():
        return 'empty_text'
    if candidate_profile_reasons(text) or _EXCLUDE.search(text):
        return 'profile_or_promotion'
    primary = bool(_ROLE.search(text))
    # A role title is enough to request LLM review, even without a hiring keyword.
    if not _HIRING.search(text) and not primary:
        return 'missing_hiring_context'
    for match in _GO.finditer(text):
        if match.group().casefold() == 'go' and re.match(
            r'\s+(?:to|ahead|for|with|on|through|back|out|home|further)\b',
            text[match.end() :],
            re.I,
        ):
            continue
        nearby = text[max(0, match.start() - 65) : match.end() + 65]
        line = text[
            max(text.rfind('\n', 0, match.start()) + 1, match.start() - 80) : min(
                len(text),
                (
                    text.find('\n', match.end())
                    if '\n' in text[match.end() :]
                    else len(text)
                ),
                match.end() + 80,
            )
        ]
        if _OPTIONAL.search(line) and not primary:
            continue
        if primary or match.group().casefold() != 'go' or _TECH.search(nearby):
            return None
    return 'missing_primary_go_context'


@dataclass(frozen=True)
class TrackDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    role_patterns: tuple[str, ...]
    prefilter: Callable[[str], str | None]
    prompt_additions: str
    accepted_strengths: tuple[str, ...] = ('primary', 'significant')
    confidence_threshold: int = 90


TRACKS = {
    'go': TrackDefinition(
        'go',
        'Go / Golang',
        ('go', 'golang', 'go lang'),
        (_ROLE.pattern,),
        go_prefilter,
        'Accept only hiring posts where Go is a primary or significant working language. '
        'Optional/nice-to-have Go, resumes, courses, events and promotion must be rejected.',
    )
}


def normalize_query(value: str) -> str:
    cleaned = ' '.join(value.split())
    if not 3 <= len(cleaned) <= 160:
        raise ValueError('Запрос должен содержать от 3 до 160 символов')
    return cleaned


def get_track(track: str) -> TrackDefinition:
    if track not in TRACKS:
        raise ValueError('Неизвестный трек')
    return TRACKS[track]


def prefilter_reason(text: str, track: str) -> str | None:
    return get_track(track).prefilter(text)
