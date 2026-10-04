"""Versioned Premium Global Search query templates for candidate profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tg_vacancy_bot.premium_search.tracks import normalize_query

_DATA_PATH = Path(__file__).parent / 'data' / 'query_templates.v1.json'


@dataclass(frozen=True)
class QueryTemplate:
    """Immutable query template associated with a catalog role or stack."""

    id: str
    kind: str
    role_id: str
    label: str
    main: str
    ru: str
    en: str
    synonyms: tuple[str, ...]
    stack_id: str | None = None


def _load_templates() -> tuple[QueryTemplate, ...]:
    data = json.loads(_DATA_PATH.read_text(encoding='utf-8'))
    return tuple(
        QueryTemplate(
            id=item['id'],
            kind=item['kind'],
            role_id=item['role_id'],
            stack_id=item.get('stack_id'),
            label=item['label'],
            main=item['main'],
            ru=item['ru'],
            en=item['en'],
            synonyms=tuple(item['synonyms']),
        )
        for item in data['templates']
    )


_TEMPLATES = _load_templates()
_BY_ID = {template.id: template for template in _TEMPLATES}
_BY_ROLE: dict[str, tuple[QueryTemplate, ...]] = {}
for _template in _TEMPLATES:
    _BY_ROLE.setdefault(_template.role_id, ())
    _BY_ROLE[_template.role_id] += (_template,)


def _profile_value(profile: Any, key: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(key, default)
    return getattr(profile, key, default)


def list_templates(profile: Any) -> tuple[QueryTemplate, ...]:
    """List applicable role/stack query templates for a legacy or direction profile."""
    role_id = _profile_value(profile, 'role_id')
    if not isinstance(role_id, str):
        return ()
    preferences = _profile_value(profile, 'preferences', {}) or {}
    stacks = preferences.get('stacks', ()) if isinstance(preferences, dict) else ()
    selected = set(stacks or ())
    roleless_onec = not role_id and _profile_value(profile, 'direction_id') == 'onec'
    templates = (
        tuple(t for t in _TEMPLATES if t.role_id.startswith('onec_'))
        if roleless_onec
        else _BY_ROLE.get(role_id, ())
    )
    if roleless_onec:
        return tuple(
            template
            for template in templates
            if template.kind == 'role' or template.stack_id in selected
        )
    return tuple(
        template
        for template in templates
        if template.kind == 'role'
        or template.stack_id in selected
        or role_id.startswith('onec_')
    )


def get_template(template_id: str) -> QueryTemplate | None:
    """Return a template by stable ID, or None when it is unknown."""
    return _BY_ID.get(template_id)


def compose_query(
    template_id: str,
    profile: Any,
    *,
    language: str = 'main',
    grade: str | None = None,
    work_format: str | None = None,
    location: str | None = None,
) -> str:
    """Build a normalized Premium query, raising if it exceeds service limits."""
    template = get_template(template_id)
    if template is None:
        raise ValueError('Неизвестный шаблон запроса')
    role_id = _profile_value(profile, 'role_id')
    roleless_onec = (
        not role_id
        and _profile_value(profile, 'direction_id') == 'onec'
        and template.role_id.startswith('onec_')
    )
    if role_id != template.role_id and not roleless_onec:
        raise ValueError('Шаблон не соответствует направлению профиля')
    preferences = _profile_value(profile, 'preferences', {}) or {}
    selected_stacks = (
        preferences.get('stacks', ()) if isinstance(preferences, dict) else ()
    )
    if (
        template.kind == 'stack'
        and template.stack_id not in (selected_stacks or ())
        and not template.role_id.startswith('onec_')
        and not (roleless_onec and not selected_stacks)
    ):
        raise ValueError('Стек шаблона не выбран в профиле')
    if language not in {'main', 'ru', 'en'}:
        raise ValueError('Язык должен быть main, ru или en')
    parts = [getattr(template, language)]
    parts.extend(
        str(value).strip()
        for value in (grade, work_format, location)
        if value is not None and str(value).strip()
    )
    return normalize_query(' '.join(parts))
