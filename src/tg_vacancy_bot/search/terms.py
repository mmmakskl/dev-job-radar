"""Small technology catalog shared by search adapters."""

import re

TECHNOLOGIES = {
    'go': ('Golang', 'Go developer', 'Go engineer', 'Go разработчик'),
    'python': ('Python', 'Python developer', 'Python engineer', 'Python разработчик'),
    'java': ('Java', 'Java developer', 'Java engineer', 'Java разработчик'),
}


def expand_queries(query: str, limit: int = 6, track: str = 'go') -> list[str]:
    """Include original intent and alternate Russian/English hiring terms."""
    if track not in TECHNOLOGIES:
        raise ValueError('unsupported_track')
    if limit <= 0:
        return []
    original = ' '.join(query.split())[:200]
    technology, developer, engineer, russian_role = TECHNOLOGIES[track]
    senior = (
        'Senior ' if re.search(r'\b(senior|сеньор|сениор)\b', original, re.I) else ''
    )
    variants = [
        original if original.casefold() != 'go' else 'Golang developer',
        f'{senior}{technology} вакансия',
        f'{senior}{technology} hiring',
        f'ищем {russian_role}а',
        f'{senior}{engineer} job',
        f'ищу {russian_role}а',
        f'{developer} wanted',
        f'{technology} разработчик в команду',
        f'hiring {engineer}',
        f'вакансия {russian_role}',
        f'{developer} position',
        f'требуется {russian_role}',
    ]
    return list(dict.fromkeys(v for v in variants if v))[:limit]
