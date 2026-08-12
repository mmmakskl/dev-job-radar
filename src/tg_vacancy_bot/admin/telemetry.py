"""Redacted status and audit data for the local administration UI."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tg_vacancy_bot.admin.settings import admin_directory


_SECRET_KEY_RE = re.compile(r'(token|secret|password|api[_-]?key|credential)', re.I)
_SECRET_VALUE_RE = re.compile(
    r'(?i)\b(api[_ -]?key|token|password|secret|authorization|cookie|credential)'
    r'\s*[:=]\s*[^\s,;]+'
)
_BEARER_RE = re.compile(r'(?i)bearer\s+[a-z0-9._-]+')
_URL_SECRET_RE = re.compile(r'([?&](?:token|key|password|secret)=[^&\s]+)', re.I)
_TELEGRAM_ID_RE = re.compile(r'-100\d{6,}')
_COMPONENTS = {
    'telegram',
    'llm',
    'google_sheets',
    'configuration',
    'source',
    'pipeline',
    'other',
}
_METRIC_EVENTS = {
    'post_processed',
    'vacancy_saved',
    'skipped_duplicate',
    'skipped_not_relevant',
    'skipped_invalid',
    'processing_error',
}


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
        return sanitize_text(value)
    return value


def sanitize_text(value: str, limit: int = 500) -> str:
    """Remove credential-shaped values before anything is persisted or returned."""
    sanitized = _BEARER_RE.sub('Bearer [redacted]', value)
    sanitized = _SECRET_VALUE_RE.sub(
        lambda match: f'{match.group(1)}=[redacted]', sanitized
    )
    sanitized = _URL_SECRET_RE.sub('[redacted-query]', sanitized)
    sanitized = _TELEGRAM_ID_RE.sub('[telegram-id]', sanitized)
    sanitized = ' '.join(sanitized.split())
    return sanitized[:limit]


def classify_component(name: str, message: str = '') -> str:
    """Map technical logger names to a short administrator-facing component."""
    value = f'{name} {message}'.casefold()
    if any(item in value for item in ('mistral', 'llm')):
        return 'llm'
    if any(item in value for item in ('sheet', 'gspread', 'google')):
        return 'google_sheets'
    if any(item in value for item in ('telethon', 'telegram', 'live-очередь')):
        return 'telegram'
    if any(item in value for item in ('config', 'settings', 'env')):
        return 'configuration'
    if 'канал' in value or 'source' in value or 'источник' in value:
        return 'source'
    if 'pipeline' in value or 'ваканси' in value:
        return 'pipeline'
    return 'other'


class SafeLogHandler:
    """A logging handler that writes a bounded, redacted app log journal."""

    def __init__(self, store: 'TelemetryStore') -> None:
        import logging

        self._handler = logging.Handler(level=logging.INFO)
        self.store = store
        self._handler.emit = self.emit  # type: ignore[method-assign]

    @property
    def handler(self):
        return self._handler

    def emit(self, record: Any) -> None:
        try:
            component = classify_component(record.name, record.getMessage())
            self.store.record_log(
                level=record.levelname,
                component=component,
                message=record.getMessage(),
            )
            if record.levelname.upper() == 'ERROR':
                self.store.record_error(
                    component, 'Ошибка приложения', record.getMessage()
                )
        except Exception:
            # The dashboard must never break the bot logging path.
            return


class TelemetryStore:
    def __init__(self, data_dir: str | None = None) -> None:
        self.directory = admin_directory(data_dir)
        self.heartbeat_path = self.directory / 'heartbeat.json'
        self.operations_path = self.directory / 'operations.jsonl'
        self.metrics_path = self.directory / 'metrics.jsonl'
        self.errors_path = self.directory / 'errors.json'
        self.logs_path = self.directory / 'app-logs.jsonl'

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

    def record_metric(self, event: str, component: str = 'pipeline') -> None:
        """Persist an aggregate-only processing event, never source post content."""
        if event not in _METRIC_EVENTS:
            raise ValueError(f'Unsupported metric event: {event}')
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            'at': _now(),
            'event': event,
            'component': self._component(component),
        }
        with self.metrics_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
            handle.write('\n')

    def today_metrics(self, timezone_name: str = 'Europe/Moscow') -> dict[str, Any]:
        """Count durable events for the current local calendar day."""
        try:
            project_timezone = ZoneInfo(timezone_name)
        except Exception:
            project_timezone = timezone.utc
        today = datetime.now(project_timezone).date()
        counts = {
            'posts_processed': 0,
            'vacancies_added': 0,
            'skipped': 0,
            'errors': 0,
        }
        mapping = {
            'post_processed': 'posts_processed',
            'vacancy_saved': 'vacancies_added',
            'skipped_duplicate': 'skipped',
            'skipped_not_relevant': 'skipped',
            'skipped_invalid': 'skipped',
            'processing_error': 'errors',
        }
        for item in self._read_jsonl(self.metrics_path):
            try:
                occurred = datetime.fromisoformat(
                    str(item['at']).replace('Z', '+00:00')
                )
            except (KeyError, TypeError, ValueError):
                continue
            key = mapping.get(item.get('event'))
            if key and occurred.astimezone(project_timezone).date() == today:
                counts[key] += 1
        return {
            'date': today.isoformat(),
            'timezone': str(project_timezone),
            'counts': counts,
            'description': (
                'Счётчики формируются из служебных событий pipeline за текущий '
                'календарный день; тексты сообщений в них не сохраняются.'
            ),
        }

    def record_error(
        self, component: str, summary: str, details: str = ''
    ) -> dict[str, Any]:
        """Merge repeated safe failures and preserve an explicit resolution state."""
        safe_component = self._component(component)
        safe_summary = sanitize_text(summary, 280)
        safe_details = sanitize_text(details, 800)
        fingerprint = hashlib.sha256(
            f'{safe_component}:{safe_summary}'.encode('utf-8')
        ).hexdigest()[:16]
        entries = self._read_errors()
        now = _now()
        for entry in entries:
            if (
                entry.get('fingerprint') == fingerprint
                and entry.get('status') != 'resolved'
            ):
                entry['status'] = 'repeating'
                entry['count'] = int(entry.get('count', 1)) + 1
                entry['last_seen_at'] = now
                if safe_details:
                    entry['details'] = safe_details
                self._save_errors(entries)
                return entry
        entry = {
            'id': fingerprint,
            'fingerprint': fingerprint,
            'component': safe_component,
            'summary': safe_summary,
            'details': safe_details or 'Технические детали безопасно недоступны.',
            'status': 'new',
            'count': 1,
            'first_seen_at': now,
            'last_seen_at': now,
        }
        entries.append(entry)
        self._save_errors(entries)
        return entry

    def attention_errors(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        entries = []
        for entry in self._read_errors():
            if entry.get('status') == 'resolved':
                continue
            try:
                seen = datetime.fromisoformat(
                    str(entry['last_seen_at']).replace('Z', '+00:00')
                )
            except (KeyError, TypeError, ValueError):
                continue
            if seen >= cutoff:
                entries.append(_redact(entry))
        return sorted(
            entries, key=lambda item: str(item.get('last_seen_at', '')), reverse=True
        )

    def resolve_error(self, error_id: str) -> dict[str, Any] | None:
        entries = self._read_errors()
        for entry in entries:
            if hmac.compare_digest(str(entry.get('id', '')), error_id):
                entry['status'] = 'resolved'
                entry['resolved_at'] = _now()
                self._save_errors(entries)
                return _redact(entry)
        return None

    def record_log(self, level: str, component: str, message: str) -> None:
        level = level.upper()
        if level not in {'INFO', 'WARNING', 'ERROR'}:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            'at': _now(),
            'level': level,
            'component': self._component(component),
            'message': sanitize_text(message),
        }
        with self.logs_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
            handle.write('\n')

    def read_logs(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        period: str = '7d',
        search: str = '',
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return only a limited, filtered reverse-chronological safe log slice."""
        now = datetime.now(timezone.utc)
        cutoffs = {
            'today': now.replace(hour=0, minute=0, second=0, microsecond=0),
            '7d': now - timedelta(days=7),
        }
        cutoff = cutoffs.get(period)
        wanted_level = level.upper() if level else None
        wanted_component = self._component(component) if component else None
        needle = sanitize_text(search, 120).casefold()
        result: list[dict[str, Any]] = []
        for entry in reversed(self._read_jsonl(self.logs_path, max_lines=3000)):
            if wanted_level and entry.get('level') != wanted_level:
                continue
            if wanted_component and entry.get('component') != wanted_component:
                continue
            try:
                occurred = datetime.fromisoformat(
                    str(entry['at']).replace('Z', '+00:00')
                )
            except (KeyError, TypeError, ValueError):
                continue
            if cutoff and occurred < cutoff:
                continue
            safe_entry = _redact(entry)
            if needle and needle not in str(safe_entry.get('message', '')).casefold():
                continue
            result.append(safe_entry)
        bounded = max(1, min(limit, 100))
        return {
            'items': result[offset : offset + bounded],
            'total': len(result),
            'offset': offset,
            'limit': bounded,
        }

    def read_heartbeat(self) -> dict[str, Any] | None:
        if not self.heartbeat_path.exists():
            return None
        try:
            return json.loads(self.heartbeat_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            return None

    def recent_operations(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._read_jsonl(self.operations_path, max_lines=limit)))

    def _component(self, value: str | None) -> str:
        candidate = (value or 'other').casefold()
        return candidate if candidate in _COMPONENTS else 'other'

    def _read_jsonl(
        self, path: Path, max_lines: int | None = None
    ) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return []
        if max_lines is not None:
            lines = lines[-max_lines:]
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    def _read_errors(self) -> list[dict[str, Any]]:
        if not self.errors_path.exists():
            return []
        try:
            payload = json.loads(self.errors_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def _save_errors(self, entries: list[dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.directory, prefix='.errors.', text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(
                    _redact(entries), handle, ensure_ascii=False, separators=(',', ':')
                )
            os.replace(temporary_path, self.errors_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
