"""Read-only startup verification; never opens a second Telegram session."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def bot_is_ready(path: Path, started_at: str, *, now: datetime | None = None) -> bool:
    """Require a live heartbeat written after this container started."""
    try:
        heartbeat = json.loads(path.read_text(encoding='utf-8'))
        updated = datetime.fromisoformat(heartbeat['updated_at'].replace('Z', '+00:00'))
        started = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        age = ((now or datetime.now(timezone.utc)) - updated).total_seconds()
        return (
            heartbeat['status'] == 'running' and updated >= started and 0 <= age <= 90
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


if __name__ == '__main__':
    sys.exit(
        0 if bot_is_ready(Path('/app/data/admin/heartbeat.json'), sys.argv[1]) else 1
    )
