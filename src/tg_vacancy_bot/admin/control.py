"""Small durable control plane shared by the API and the live process."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tg_vacancy_bot.admin.settings import admin_directory


VALID_ACTIONS = {'restart', 'history', 'sync_channels'}


def _path(data_dir: str | None) -> Path:
    return admin_directory(data_dir) / 'control.json'


def request_action(action: str, data_dir: str | None = None) -> dict[str, str]:
    if action not in VALID_ACTIONS:
        raise ValueError('Недопустимое действие')
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'id': str(uuid.uuid4()),
        'action': action,
        'requested_at': datetime.now(timezone.utc).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix='.control.', text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, separators=(',', ':'))
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return payload


def read_action(data_dir: str | None = None) -> dict[str, str] | None:
    path = _path(data_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    return payload if payload.get('action') in VALID_ACTIONS else None


def acknowledge_action(action_id: str, data_dir: str | None = None) -> None:
    path = _path(data_dir)
    action = read_action(data_dir)
    if action and action.get('id') == action_id:
        path.unlink(missing_ok=True)
