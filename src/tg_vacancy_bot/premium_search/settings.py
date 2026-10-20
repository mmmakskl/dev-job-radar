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

    flag = os.getenv('CANDIDATE_CATALOG_SEARCH_ENABLED', '').casefold() in {
        '1',
        'true',
        'yes',
        'on',
    }
    flag = flag or bool(getattr(config, 'CANDIDATE_CATALOG_SEARCH_ENABLED', False))
    if not enabled() or not flag:
        return False
    if owner.startswith('admin:'):
        try:
            admin_id = int(owner.split(':', 1)[1])
        except ValueError:
            return False
        try:
            configured_admin = int(
                os.getenv('PREMIUM_SEARCH_WEB_ADMIN_TELEGRAM_ID') or 0
            )
        except ValueError:
            configured_admin = 0
        allowed = {
            int(value.strip())
            for value in os.getenv('CANDIDATE_BOT_ALLOWED_USER_IDS', '').split(',')
            if value.strip().isdigit()
        }
        allowed.update(getattr(config, 'CANDIDATE_BOT_ALLOWED_USER_IDS', ()))
        return admin_id == configured_admin and admin_id in allowed
    if not owner.startswith('candidate:'):
        return False
    try:
        user_id = int(owner.split(':', 1)[1])
    except ValueError:
        return False
    allowed = {
        int(value.strip())
        for value in os.getenv('CANDIDATE_BOT_ALLOWED_USER_IDS', '').split(',')
        if value.strip().isdigit()
    }
    allowed.update(getattr(config, 'CANDIDATE_BOT_ALLOWED_USER_IDS', ()))
    return user_id in allowed
