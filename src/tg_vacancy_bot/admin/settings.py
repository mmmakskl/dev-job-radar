"""Versioned managed configuration kept outside the repository and `.env`."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = 4


def normalize_public_source(value: str) -> str:
    """Normalize the public Telegram source formats accepted by the UI."""
    candidate = value.strip()
    for prefix in ('https://', 'http://'):
        if candidate.casefold().startswith(prefix):
            candidate = candidate[len(prefix) :]
    if candidate.casefold().startswith('t.me/'):
        candidate = candidate[5:]
    elif candidate.casefold().startswith('telegram.me/'):
        candidate = candidate[12:]
    candidate = candidate.strip('/').lstrip('@')
    if not candidate or not candidate.replace('_', '').isalnum():
        raise ValueError('Источник должен быть публичным username или ссылкой t.me')
    return candidate


_PROMPT_SECRET_RE = re.compile(
    r'(?i)(?:api[_ -]?key|token|password|secret|authorization|cookie|credential)'
    r'\s*[:=]\s*\S+'
)
_PROMPT_TOKEN_RE = re.compile(r'(?i)(?:sk-[a-z0-9_-]{10,}|bearer\s+[a-z0-9._-]{12,})')


def validate_editable_instructions(value: str) -> str:
    """Reject accidental credentials in a user-editable LLM instruction."""
    cleaned = value.strip()
    if len(cleaned) < 20 or len(cleaned) > 12000:
        raise ValueError('Инструкции должны содержать от 20 до 12000 символов')
    if _PROMPT_SECRET_RE.search(cleaned) or _PROMPT_TOKEN_RE.search(cleaned):
        raise ValueError('Инструкции не должны содержать секреты или токены')
    return cleaned


class ManagedSource(BaseModel):
    """One public source created through the administration UI."""

    identifier: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    added_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    verification_status: str = Field(default='verified', pattern='^(verified|invalid)$')
    verified_at: str | None = None

    @field_validator('identifier')
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        return normalize_public_source(value)


class TelegramSettings(BaseModel):
    folder_name: str = Field(default='Вакансии', min_length=1, max_length=80)
    additional_channels: list[str] = Field(default_factory=list, max_length=100)
    managed_sources: list[ManagedSource] = Field(default_factory=list, max_length=100)
    folder_channels: list[str] = Field(default_factory=list, max_length=300)
    disabled_channels: list[str] = Field(default_factory=list, max_length=200)
    monitoring_enabled: bool = True
    history_days: int = Field(default=7, ge=1, le=90)
    notify_enabled: bool = False
    notify_target: str = Field(default='', max_length=128)

    @field_validator('additional_channels')
    @classmethod
    def public_channels_only(cls, values: list[str]) -> list[str]:
        normalised = []
        for value in values:
            channel = normalize_public_source(value)
            if channel.casefold() not in {item.casefold() for item in normalised}:
                normalised.append(channel)
        return normalised

    @model_validator(mode='after')
    def unique_managed_sources(self) -> 'TelegramSettings':
        seen: set[str] = set()
        unique: list[ManagedSource] = []
        for source in self.managed_sources:
            key = source.identifier.casefold()
            if key in seen:
                raise ValueError('Источник уже добавлен')
            seen.add(key)
            unique.append(source)
        self.managed_sources = unique
        return self

    @model_validator(mode='after')
    def require_notify_target(self) -> 'TelegramSettings':
        if self.notify_enabled and not self.notify_target.strip():
            raise ValueError('Укажите канал уведомлений или выключите уведомления')
        return self


class FilterSettings(BaseModel):
    keywords: list[str] = Field(default_factory=lambda: ['go', 'golang'], min_length=1)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=100)
    text_hash_ttl_days: int = Field(default=30, ge=1, le=3650)
    queue_maxsize: int = Field(default=1000, ge=1, le=10000)
    workers: int = Field(default=1, ge=1, le=4)

    @field_validator('keywords', 'exclude_keywords')
    @classmethod
    def normalise_words(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            word = value.strip()
            if not word or len(word) > 64:
                raise ValueError('Ключевое слово должно иметь от 1 до 64 символов')
            if word.casefold() not in {item.casefold() for item in result}:
                result.append(word)
        return result


class MistralSettings(BaseModel):
    model: str = Field(default='ministral-3b-2512', min_length=3, max_length=100)
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_attempts: int = Field(default=2, ge=1, le=5)
    vacancy_instructions: str | None = None

    @field_validator('vacancy_instructions')
    @classmethod
    def valid_instructions(cls, value: str | None) -> str | None:
        return validate_editable_instructions(value) if value is not None else None


class SheetsSettings(BaseModel):
    output_timezone: str = Field(default='Europe/Moscow', min_length=1, max_length=80)
    full_title: str = Field(default='Вакансии — полные', min_length=1, max_length=100)
    short_title: str = Field(default='Вакансии — кратко', min_length=1, max_length=100)

    @field_validator('output_timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError('Укажите корректный часовой пояс IANA') from error
        return value


class RetentionSettings(BaseModel):
    """Retention policy for local observability data only."""

    logs_days: int = Field(default=30, ge=1, le=3650)
    errors_days: int = Field(default=90, ge=1, le=3650)
    operations_days: int = Field(default=90, ge=1, le=3650)
    metrics_days: int = Field(default=90, ge=1, le=3650)


class AlertSettings(BaseModel):
    """Non-secret notification policy for the existing Telegram target."""

    enabled: bool = False
    heartbeat_stale_seconds: int = Field(default=120, ge=30, le=86400)
    queue_warning_percent: int = Field(default=80, ge=50, le=100)
    error_streak_threshold: int = Field(default=3, ge=2, le=100)
    error_window_seconds: int = Field(default=900, ge=60, le=86400)
    no_export_seconds: int = Field(default=21600, ge=300, le=604800)
    cooldown_seconds: int = Field(default=3600, ge=60, le=86400)


class AdminSettings(BaseModel):
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    updated_at: str = ''
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)
    mistral: MistralSettings = Field(default_factory=MistralSettings)
    sheets: SheetsSettings = Field(default_factory=SheetsSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)


def admin_directory(data_dir: str | None) -> Path:
    return Path(data_dir or 'data') / 'admin'


def _env_bool(value: str | None) -> bool:
    return bool(value and value.strip().casefold() in {'1', 'true', 'yes', 'on'})


def defaults_from_environment() -> AdminSettings:
    """Seed the first UI view from existing `.env` without changing runtime."""
    return AdminSettings(
        telegram=TelegramSettings(
            folder_name=os.getenv('TELEGRAM_CHANNELS_FOLDER', 'Вакансии'),
            notify_enabled=_env_bool(os.getenv('TELEGRAM_NOTIFY_ENABLED')),
            notify_target=os.getenv('TELEGRAM_NOTIFY_TARGET', ''),
        ),
        filters=FilterSettings(
            text_hash_ttl_days=int(os.getenv('TEXT_HASH_TTL_DAYS', '30')),
            queue_maxsize=int(os.getenv('LIVE_QUEUE_MAXSIZE', '1000')),
            workers=int(os.getenv('LIVE_WORKERS', '1')),
        ),
        sheets=SheetsSettings(
            output_timezone=os.getenv('OUTPUT_TIMEZONE', 'Europe/Moscow'),
            full_title=os.getenv('GOOGLE_SHEET_FULL_TITLE', 'Вакансии — полные'),
            short_title=os.getenv('GOOGLE_SHEET_SHORT_TITLE', 'Вакансии — кратко'),
        ),
    )


class SettingsStore:
    """SQLite-backed control plane for settings and Telegram sources."""

    def __init__(self, data_dir: str | None = None) -> None:
        self.directory = admin_directory(data_dir)
        self.path = self.directory / 'admin.sqlite3'
        self.legacy_path = self.directory / 'settings.json'
        self.directory.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA busy_timeout = 10000')
        connection.execute('PRAGMA journal_mode = WAL')
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL CHECK (revision >= 0),
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_sources (
                    id TEXT PRIMARY KEY,
                    telegram_id TEXT UNIQUE,
                    username TEXT COLLATE NOCASE UNIQUE,
                    title TEXT,
                    chat_type TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (chat_type IN ('channel','group','unknown')),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
                    verification_status TEXT NOT NULL DEFAULT 'unverified'
                        CHECK (verification_status IN ('verified','invalid','unverified')),
                    administrator_disabled INTEGER NOT NULL DEFAULT 0
                        CHECK (administrator_disabled IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    verified_at TEXT,
                    last_seen_at TEXT,
                    CHECK (telegram_id IS NOT NULL OR username IS NOT NULL)
                );
                CREATE TABLE IF NOT EXISTS source_origins (
                    source_id TEXT NOT NULL REFERENCES telegram_sources(id) ON DELETE CASCADE,
                    origin TEXT NOT NULL CHECK (
                        origin IN ('legacy_env','legacy_settings','admin','folder','discovery')
                    ),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, origin)
                );
                CREATE INDEX IF NOT EXISTS idx_sources_active
                    ON telegram_sources(enabled, administrator_disabled);
                CREATE INDEX IF NOT EXISTS idx_source_origins_origin
                    ON source_origins(origin);
                ''')
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version = ?', (SCHEMA_VERSION,)
            ).fetchone()
            if row:
                return
            self._import_legacy(connection)
            connection.execute(
                'INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)',
                (SCHEMA_VERSION, _now()),
            )

    def _import_legacy(self, connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            'SELECT 1 FROM settings WHERE singleton = 1'
        ).fetchone()
        if existing:
            return
        settings = defaults_from_environment()
        if self.legacy_path.exists():
            settings = AdminSettings.model_validate_json(
                self.legacy_path.read_text(encoding='utf-8')
            )
        now = _now()
        connection.execute(
            'INSERT INTO settings(singleton, revision, payload, updated_at) VALUES (1,?,?,?)',
            (settings.revision, settings.model_dump_json(), settings.updated_at or now),
        )
        for value in (os.getenv('TARGET_CHANNELS') or '').split(','):
            if value.strip():
                self._upsert_source_tx(connection, value.strip(), origin='legacy_env')
        for value in settings.telegram.additional_channels:
            self._upsert_source_tx(connection, value, origin='legacy_settings')
        for value in settings.telegram.folder_channels:
            self._upsert_source_tx(connection, value, origin='folder')
        for item in settings.telegram.managed_sources:
            self._upsert_source_tx(
                connection,
                item.identifier,
                origin='admin',
                enabled=item.enabled,
                verification_status=item.verification_status,
                verified_at=item.verified_at,
            )
        for value in settings.telegram.disabled_channels:
            numeric = value.lstrip('-').isdigit()
            connection.execute(
                f'''UPDATE telegram_sources SET administrator_disabled = 1, enabled = 0
                    WHERE {"telegram_id" if numeric else "username"} = ? COLLATE NOCASE''',
                (value if numeric else value.lstrip('@'),),
            )

    def load(self) -> AdminSettings:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT revision, payload, updated_at FROM settings WHERE singleton = 1'
            ).fetchone()
        if row is None:
            raise RuntimeError('Хранилище настроек не инициализировано')
        payload = json.loads(row['payload'])
        payload.update(revision=row['revision'], updated_at=row['updated_at'])
        return AdminSettings.model_validate(payload)

    def save(
        self, settings: AdminSettings, *, expected_revision: int | None = None
    ) -> AdminSettings:
        updated = settings.model_copy(
            update={
                'schema_version': SCHEMA_VERSION,
                'revision': settings.revision + 1,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
        )
        expected = settings.revision if expected_revision is None else expected_revision
        with self._connect() as connection:
            cursor = connection.execute(
                '''UPDATE settings SET revision = ?, payload = ?, updated_at = ?
                   WHERE singleton = 1 AND revision = ?''',
                (
                    updated.revision,
                    updated.model_dump_json(),
                    updated.updated_at,
                    expected,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleSettingsError('Настройки уже изменены в другой вкладке')
        return updated

    def replace_from_payload(self, payload: dict[str, Any]) -> AdminSettings:
        submitted = AdminSettings.model_validate(payload)
        return self.save(submitted, expected_revision=submitted.revision)

    def replace_with_enabled_sources(
        self, payload: dict[str, Any], enabled_source_ids: set[str]
    ) -> AdminSettings:
        """Commit a settings revision and source toggles in one transaction."""
        if not enabled_source_ids:
            raise ValueError('Должен остаться хотя бы один включённый источник')
        submitted = AdminSettings.model_validate(payload)
        updated = submitted.model_copy(
            update={
                'schema_version': SCHEMA_VERSION,
                'revision': submitted.revision + 1,
                'updated_at': _now(),
            }
        )
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            known_ids = {
                row['id']
                for row in connection.execute('SELECT id FROM telegram_sources')
            }
            if not enabled_source_ids <= known_ids:
                raise ValueError('Передан неизвестный источник')
            if enabled_source_ids:
                placeholders = ','.join('?' for _ in enabled_source_ids)
                blocked = connection.execute(
                    f'''SELECT 1 FROM telegram_sources s
                        WHERE s.id IN ({placeholders})
                        AND (s.verification_status='invalid' OR (
                            s.verification_status='unverified' AND EXISTS (
                                SELECT 1 FROM source_origins o
                                WHERE o.source_id=s.id AND o.origin='admin'
                            )
                        )) LIMIT 1''',
                    tuple(enabled_source_ids),
                ).fetchone()
                if blocked:
                    raise ValueError(
                        'Непроверенный или невалидный источник нельзя включить'
                    )
            cursor = connection.execute(
                '''UPDATE settings SET revision=?, payload=?, updated_at=?
                   WHERE singleton=1 AND revision=?''',
                (
                    updated.revision,
                    updated.model_dump_json(),
                    updated.updated_at,
                    submitted.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleSettingsError('Настройки уже изменены в другой вкладке')
            connection.execute(
                '''UPDATE telegram_sources SET enabled=0, administrator_disabled=1,
                   updated_at=?''',
                (_now(),),
            )
            if enabled_source_ids:
                placeholders = ','.join('?' for _ in enabled_source_ids)
                connection.execute(
                    f'''UPDATE telegram_sources SET enabled=1, administrator_disabled=0,
                        updated_at=? WHERE id IN ({placeholders})''',
                    (_now(), *enabled_source_ids),
                )
        return updated

    def _upsert_source_tx(
        self, connection: sqlite3.Connection, identifier: str | int, **kwargs: Any
    ) -> str:
        return _upsert_source_tx(connection, identifier, **kwargs)

    def upsert_source(self, identifier: str | int, **kwargs: Any) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            source_id = self._upsert_source_tx(connection, identifier, **kwargs)
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> dict[str, Any]:
        items = [item for item in self.list_sources() if item['id'] == source_id]
        if not items:
            raise KeyError(source_id)
        return items[0]

    def list_sources(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = (
            'WHERE s.enabled = 1 AND s.administrator_disabled = 0'
            if active_only
            else ''
        )
        with self._connect() as connection:
            rows = connection.execute(f'''SELECT s.*, GROUP_CONCAT(o.origin) AS origins
                    FROM telegram_sources s
                    LEFT JOIN source_origins o ON o.source_id = s.id
                    {where} GROUP BY s.id ORDER BY s.created_at, s.id''').fetchall()
        return [
            {
                **dict(row),
                'enabled': bool(row['enabled'])
                and not bool(row['administrator_disabled']),
                'origins': sorted((row['origins'] or '').split(',')),
            }
            for row in rows
        ]

    def active_targets(self) -> list[str | int]:
        return [
            (
                int(item['telegram_id'])
                if item['telegram_id'] is not None
                else item['username']
            )
            for item in self.list_sources(active_only=True)
        ]

    def set_source_enabled(self, source_id: str, enabled: bool) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            source = connection.execute(
                '''SELECT s.verification_status,
                          EXISTS(SELECT 1 FROM source_origins o
                                 WHERE o.source_id=s.id AND o.origin='admin') AS is_admin
                   FROM telegram_sources s WHERE s.id=?''',
                (source_id,),
            ).fetchone()
            if source is None:
                raise KeyError(source_id)
            if enabled and (
                source['verification_status'] == 'invalid'
                or (
                    source['verification_status'] == 'unverified'
                    and bool(source['is_admin'])
                )
            ):
                raise ValueError('Сначала успешно проверьте Telegram-источник')
            if not enabled:
                remaining = connection.execute(
                    '''SELECT COUNT(*) FROM telegram_sources
                       WHERE id != ? AND enabled=1 AND administrator_disabled=0''',
                    (source_id,),
                ).fetchone()[0]
                if remaining == 0:
                    raise ValueError('Должен остаться хотя бы один включённый источник')
            cursor = connection.execute(
                '''UPDATE telegram_sources SET enabled = ?, administrator_disabled = ?,
                   updated_at = ? WHERE id = ?''',
                (int(enabled), int(not enabled), _now(), source_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(source_id)
        return self.get_source(source_id)

    def mark_source_verified(
        self,
        source_id: str,
        *,
        telegram_id: int,
        username: str | None,
        title: str | None,
        chat_type: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute(
                '''SELECT s.*, GROUP_CONCAT(o.origin) AS origins
                   FROM telegram_sources s
                   LEFT JOIN source_origins o ON o.source_id=s.id
                   WHERE s.id=? GROUP BY s.id''',
                (source_id,),
            ).fetchone()
            if current is None:
                raise KeyError(source_id)
            origins = sorted((current['origins'] or '').split(','))
            merged_id = self._upsert_source_tx(
                connection,
                telegram_id,
                username=username,
                title=title,
                chat_type=chat_type,
                origin=origins[0],
                verification_status='verified',
                verified_at=_now(),
            )
            if merged_id != source_id:
                for origin in origins:
                    connection.execute(
                        '''INSERT OR IGNORE INTO source_origins(source_id, origin, created_at)
                           VALUES (?, ?, ?)''',
                        (merged_id, origin, _now()),
                    )
                connection.execute(
                    'DELETE FROM telegram_sources WHERE id = ?', (source_id,)
                )
            connection.execute(
                '''UPDATE telegram_sources SET enabled=1
                   WHERE id=? AND administrator_disabled=0''',
                (merged_id,),
            )
        return self.get_source(merged_id)

    def mark_source_invalid(self, source_id: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(
                '''UPDATE telegram_sources SET verification_status='invalid',
                   enabled=0, updated_at=? WHERE id=?''',
                (_now(), source_id),
            )

    def remove_admin_origin(self, source_id: str) -> bool:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.execute(
                "DELETE FROM source_origins WHERE source_id = ? AND origin = 'admin'",
                (source_id,),
            )
            connection.execute(
                '''DELETE FROM telegram_sources WHERE id = ? AND NOT EXISTS
                   (SELECT 1 FROM source_origins WHERE source_id = ?)''',
                (source_id, source_id),
            )
        return cursor.rowcount == 1

    def sync_folder(self, channels: list[Any]) -> dict[str, int]:
        """Atomically replace only folder origins after a complete fetch."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = {
                row['source_id']
                for row in connection.execute(
                    "SELECT source_id FROM source_origins WHERE origin = 'folder'"
                )
            }
            current: set[str] = set()
            for channel in channels:
                current.add(
                    self._upsert_source_tx(
                        connection,
                        channel.id,
                        username=channel.username,
                        title=channel.name,
                        chat_type=getattr(channel, 'chat_type', 'unknown'),
                        origin='folder',
                        verification_status='verified',
                    )
                )
            removed = previous - current
            if removed:
                placeholders = ','.join('?' for _ in removed)
                connection.execute(
                    f"DELETE FROM source_origins WHERE origin='folder' AND source_id IN ({placeholders})",
                    tuple(removed),
                )
                connection.execute(
                    f'''DELETE FROM telegram_sources WHERE id IN ({placeholders})
                        AND NOT EXISTS (SELECT 1 FROM source_origins o WHERE o.source_id=telegram_sources.id)''',
                    tuple(removed),
                )
        return {
            'received': len(channels),
            'created': len(current - previous),
            'updated': len(current & previous),
            'skipped': 0,
            'failed': 0,
        }


class StaleSettingsError(RuntimeError):
    """The submitted revision no longer matches durable state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_source_tx(
    connection: sqlite3.Connection,
    identifier: str | int,
    *,
    origin: str,
    username: str | None = None,
    title: str | None = None,
    chat_type: str = 'unknown',
    enabled: bool = True,
    verification_status: str = 'unverified',
    verified_at: str | None = None,
) -> str:
    raw = str(identifier).strip()
    telegram_id = raw if raw.lstrip('-').isdigit() else None
    normalized_username = (
        (username or (None if telegram_id else raw)).strip().lstrip('@')
        if (username or not telegram_id)
        else None
    )
    now = _now()
    by_id = (
        connection.execute(
            'SELECT id FROM telegram_sources WHERE telegram_id = ?', (telegram_id,)
        ).fetchone()
        if telegram_id
        else None
    )
    by_username = (
        connection.execute(
            'SELECT id FROM telegram_sources WHERE username = ? COLLATE NOCASE',
            (normalized_username,),
        ).fetchone()
        if normalized_username
        else None
    )
    if by_id and by_username and by_id['id'] != by_username['id']:
        target, duplicate = by_id['id'], by_username['id']
        connection.execute(
            'INSERT OR IGNORE INTO source_origins(source_id, origin, created_at) SELECT ?, origin, created_at FROM source_origins WHERE source_id = ?',
            (target, duplicate),
        )
        connection.execute(
            '''UPDATE telegram_sources SET
               administrator_disabled = MAX(administrator_disabled,
                   (SELECT administrator_disabled FROM telegram_sources WHERE id=?)),
               enabled = MIN(enabled,
                   (SELECT enabled FROM telegram_sources WHERE id=?))
               WHERE id=?''',
            (duplicate, duplicate, target),
        )
        connection.execute('DELETE FROM telegram_sources WHERE id = ?', (duplicate,))
        source_id = target
    elif by_id or by_username:
        source_id = (by_id or by_username)['id']
    else:
        source_id = str(uuid.uuid4())
        connection.execute(
            '''INSERT INTO telegram_sources(
                id, telegram_id, username, title, chat_type, enabled,
                verification_status, created_at, updated_at, verified_at, last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (
                source_id,
                telegram_id,
                normalized_username,
                title,
                chat_type,
                int(enabled),
                verification_status,
                now,
                now,
                verified_at,
                now if verification_status == 'verified' else None,
            ),
        )
    connection.execute(
        '''UPDATE telegram_sources SET telegram_id=COALESCE(?,telegram_id),
           username=COALESCE(?,username), title=COALESCE(?,title),
           chat_type=CASE WHEN ?='unknown' THEN chat_type ELSE ? END,
           verification_status=CASE WHEN ?='unverified' THEN verification_status ELSE ? END,
           verified_at=COALESCE(?,verified_at),
           last_seen_at=CASE WHEN ?='verified' THEN ? ELSE last_seen_at END,
           updated_at=? WHERE id=?''',
        (
            telegram_id,
            normalized_username,
            title,
            chat_type,
            chat_type,
            verification_status,
            verification_status,
            verified_at,
            verification_status,
            now,
            now,
            source_id,
        ),
    )
    connection.execute(
        'INSERT OR IGNORE INTO source_origins(source_id, origin, created_at) VALUES (?,?,?)',
        (source_id, origin, now),
    )
    return source_id


def load_runtime_settings(data_dir: str | None = None) -> AdminSettings | None:
    """Return defaults when the UI has not saved settings yet.

    A malformed operator file must fail a process start loudly instead of silently
    running with unexpected values.
    """
    return SettingsStore(data_dir).load()
