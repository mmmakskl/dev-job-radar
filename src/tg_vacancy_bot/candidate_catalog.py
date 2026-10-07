"""Versioned IT role catalog and profile path validation."""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / 'data' / 'it_roles.v1.json'
CATALOG = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
CATALOG_VERSION = CATALOG['version']
PROFILE_CONTRACT = 'catalog-v3'
LEGACY_CATALOG = json.loads(
    (CATALOG_PATH.parent / 'it_roles.v2.json').read_text(encoding='utf-8')
)


def roles_for_direction(direction_id: str) -> list[dict]:
    """Return the public roles of one direction in display order."""
    direction = next(
        (item for item in CATALOG['directions'] if item['id'] == direction_id), None
    )
    if direction is None:
        raise ValueError('Invalid profile direction')
    return [
        role
        for specialization in direction['specializations']
        for role in specialization['roles']
    ]


def role_path(role_id: str) -> tuple[str, str, str] | None:
    """Resolve a current role ID to its direction and internal specialization."""
    for direction in CATALOG['directions']:
        for specialization in direction['specializations']:
            for role in specialization['roles']:
                if role['id'] == role_id:
                    return direction['id'], specialization['id'], role_id
    return None


def migrate_legacy_role(role_id: str) -> tuple[str, str, str] | None:
    """Map only roles with an unchanged meaning to the current catalog."""
    return role_path(role_id)


def resolve_alias(value: str) -> tuple[str, str] | None:
    """Resolve a Russian or English catalog label/alias to (kind, stable ID)."""
    normalized = value.strip().casefold()
    for direction in [*CATALOG['directions'], *LEGACY_CATALOG['directions']]:
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
        legacy_direction = next(
            (d for d in LEGACY_CATALOG['directions'] if d['id'] == direction_id),
            None,
        )
        legacy_specialization = next(
            (
                s
                for s in (legacy_direction or {}).get('specializations', [])
                if s['id'] == specialization_id
            ),
            None,
        )
        role = next(
            (
                r
                for r in (legacy_specialization or {}).get('roles', [])
                if r['id'] == role_id
            ),
            None,
        )
        if role is None:
            raise ValueError('Invalid direction, specialization, role path')
    allowed = role['stacks']
    invalid = set(stacks or []) - set(allowed)
    if invalid:
        raise ValueError(f'Stacks do not apply to this role: {sorted(invalid)}')


def direction_stacks(direction_id: str) -> list[str]:
    """Return every distinct language/technology option catalogued for a direction."""
    direction = next(
        (item for item in CATALOG['directions'] if item['id'] == direction_id), None
    )
    if direction is None:
        raise ValueError('Invalid profile direction')
    return list(
        dict.fromkeys(
            stack
            for specialization in direction['specializations']
            for role in specialization['roles']
            for stack in role['stacks']
        )
    )


def validate_direction_stacks(
    direction_id: str, stacks: list[str] | None = None
) -> None:
    """Validate a role-neutral profile against all catalogued direction options."""
    allowed = set(direction_stacks(direction_id))
    invalid = set(stacks or []) - allowed
    if invalid:
        raise ValueError(f'Stacks do not apply to this direction: {sorted(invalid)}')
