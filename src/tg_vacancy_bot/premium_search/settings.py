"""Side-effect-free feature gate shared by API and live startup."""

import os
from pathlib import Path


def enabled() -> bool:
    return os.getenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'false').casefold() in {
        '1',
        'true',
        'yes',
        'on',
    }


def database_path(data_dir: str | None = None) -> str:
    return str(
        Path(data_dir or os.getenv('DATA_DIR') or 'data') / 'premium_search.sqlite3'
    )


def publisher_configured() -> bool:
    return (
        os.getenv('CANDIDATE_BOT_ENABLED', '').casefold() in {'1', 'true', 'yes', 'on'}
        and bool(os.getenv('CANDIDATE_BOT_TOKEN'))
        and bool(os.getenv('CANDIDATE_BOT_CHANNEL'))
    )


def catalog_search_allowed(owner: str) -> bool:
    """Fail closed when rollout access is removed, including queued previews."""
    from tg_vacancy_bot import config

    if not owner.startswith('candidate:'):
        return False
    try:
        user_id = int(owner.split(':', 1)[1])
    except ValueError:
        return False
    return (
        enabled()
        and getattr(config, 'CANDIDATE_CATALOG_SEARCH_ENABLED', False)
        and user_id in getattr(config, 'CANDIDATE_PROFILE_ALLOWED_USER_IDS', ())
        and user_id in getattr(config, 'CANDIDATE_BOT_ALLOWED_USER_IDS', ())
    )
