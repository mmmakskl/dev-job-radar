"""Redacted status and audit data for the local administration UI."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tg_vacancy_bot.admin.settings import admin_directory


_SECRET_KEY_RE = re.compile(r'(token|secret|password|api[_-]?key|credential)', re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ('[redacted]' if _SECRET_KEY_RE.search(key) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'-100\d{6,}', '[telegram-id]', value)
    return value


class TelemetryStore:
    def __init__(self, data_dir: str | None = None) -> None:
        self.directory = admin_directory(data_dir)
        self.heartbeat_path = self.directory / 'heartbeat.json'
        self.operations_path = self.directory / 'operations.jsonl'

    def heartbeat(self, **values: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = _redact({'updated_at': _now(), **values})
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.directory, prefix='.heartbeat.', text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'))
            os.replace(temporary_path, self.heartbeat_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def record(self, event: str, **details: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = _redact({'at': _now(), 'event': event, **details})
        with self.operations_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
            handle.write('\n')

    def read_heartbeat(self) -> dict[str, Any] | None:
        if not self.heartbeat_path.exists():
            return None
        try:
            return json.loads(self.heartbeat_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            return None

    def recent_operations(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.operations_path.exists():
            return []
        lines = self.operations_path.read_text(encoding='utf-8').splitlines()[-limit:]
        result = []
        for line in reversed(lines):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result
