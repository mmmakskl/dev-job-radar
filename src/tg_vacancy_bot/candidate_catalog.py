"""Versioned IT role catalog and profile path validation."""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / 'data' / 'it_roles.v1.json'
CATALOG = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
CATALOG_VERSION = CATALOG['version']


def resolve_alias(value: str) -> tuple[str, str] | None:
    """Resolve a Russian or English catalog label/alias to (kind, stable ID)."""
    normalized = value.strip().casefold()
    for direction in CATALOG['directions']:
        for label in [direction['id'], *direction['synonyms']]:
            if normalized == label.casefold():
                return 'direction', direction['id']
        for specialization in direction['specializations']:
            for label in [specialization['id'], *specialization['synonyms']]:
                if normalized == label.casefold():
                    return 'specialization', specialization['id']
            for role in specialization['roles']:
                for label in [role['id'], *role['synonyms']]:
                    if normalized == label.casefold():
                        return 'role', role['id']
    return None


def validate_profile_path(
    direction_id: str,
    specialization_id: str,
    role_id: str,
    stacks: list[str] | None = None,
) -> None:
    """Ensure the selected role belongs to the path and stacks apply to it."""
    direction = next(
        (d for d in CATALOG['directions'] if d['id'] == direction_id), None
    )
    specialization = (
        next(
            (s for s in direction['specializations'] if s['id'] == specialization_id),
            None,
        )
        if direction
        else None
    )
    role = (
        next((r for r in specialization['roles'] if r['id'] == role_id), None)
        if specialization
        else None
    )
    if role is None:
        raise ValueError('Invalid direction, specialization, role path')
    allowed = role['stacks']
    invalid = set(stacks or []) - set(allowed)
    if invalid:
        raise ValueError(f'Stacks do not apply to this role: {sorted(invalid)}')
