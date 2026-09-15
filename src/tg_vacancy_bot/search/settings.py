"""Search configuration without importing the Telegram runtime or opening databases."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str) -> bool:
    return os.getenv(name, '').strip().casefold() in {'1', 'true', 'yes', 'on'}


@dataclass(frozen=True)
class SearchSettings:
    threads_enabled: bool = False
    token: str = field(default='', repr=False)
    search_limit: int = 100
    variants_limit: int = 6
    max_pages: int = 2
    timeout: int = 20
    max_attempts: int = 3
    interval_hours: int = 6
    daily_budget: int = 1000
    configuration_error: str | None = None

    @classmethod
    def from_env(cls) -> 'SearchSettings':
        values = dict(
            threads_enabled=_bool('THREADS_ENABLED'),
            token=os.getenv('THREADS_ACCESS_TOKEN', '').strip(),
        )
        limits = {
            'search_limit': ('THREADS_SEARCH_LIMIT', 100, 1, 1000),
            'variants_limit': ('THREADS_QUERY_VARIANTS_LIMIT', 6, 2, 12),
            'max_pages': ('THREADS_MAX_PAGES_PER_QUERY', 2, 1, 10),
            'timeout': ('THREADS_TIMEOUT_SECONDS', 20, 1, 120),
            'max_attempts': ('THREADS_MAX_ATTEMPTS', 3, 1, 5),
            'interval_hours': ('THREADS_COLLECTION_INTERVAL_HOURS', 6, 0, 168),
            'daily_budget': ('THREADS_MAX_REQUESTS_PER_DAY', 1000, 1, 2200),
        }
        for key, (name, default, minimum, maximum) in limits.items():
            try:
                value = int(os.getenv(name, str(default)))
                if not minimum <= value <= maximum:
                    raise ValueError()
                values[key] = value
            except ValueError:
                values['configuration_error'] = 'invalid_threads_configuration'
        return cls(**values)


def search_database_path(data_dir: str | None = None) -> str:
    return str(
        Path(data_dir or os.getenv('DATA_DIR') or 'data') / 'admin' / 'admin.sqlite3'
    )


def source_capabilities(settings: SearchSettings | None = None) -> list[dict]:
    settings = settings or SearchSettings.from_env()
    reason = (
        'disabled'
        if not settings.threads_enabled
        else settings.configuration_error
        or ('token_not_configured' if not settings.token else None)
    )
    return [
        dict(
            key='telegram', label='Telegram · опубликованные', enabled=True, reason=None
        ),
        dict(
            key='premium',
            label='Telegram Premium',
            enabled=_bool('PREMIUM_GLOBAL_SEARCH_ENABLED'),
            reason=None if _bool('PREMIUM_GLOBAL_SEARCH_ENABLED') else 'disabled',
        ),
        dict(key='threads', label='Threads', enabled=reason is None, reason=reason),
    ]
