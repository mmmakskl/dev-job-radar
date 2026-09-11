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
