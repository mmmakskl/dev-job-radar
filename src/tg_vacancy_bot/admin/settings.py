"""Versioned managed configuration kept outside the repository and `.env`."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


SCHEMA_VERSION = 2


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
    model: str = Field(default='mistral-small-latest', min_length=3, max_length=100)
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


class AdminSettings(BaseModel):
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    updated_at: str = ''
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    filters: FilterSettings = Field(default_factory=FilterSettings)
    mistral: MistralSettings = Field(default_factory=MistralSettings)
    sheets: SheetsSettings = Field(default_factory=SheetsSettings)


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
    """Reads and atomically writes validated, non-secret admin settings."""

    def __init__(self, data_dir: str | None = None) -> None:
        self.directory = admin_directory(data_dir)
        self.path = self.directory / 'settings.json'

    def load(self) -> AdminSettings:
        if not self.path.exists():
            return defaults_from_environment()
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        return AdminSettings.model_validate(payload)

    def save(self, settings: AdminSettings) -> AdminSettings:
        self.directory.mkdir(parents=True, exist_ok=True)
        updated = settings.model_copy(
            update={
                'schema_version': SCHEMA_VERSION,
                'revision': settings.revision + 1,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.directory,
            prefix='.settings.',
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write(updated.model_dump_json(indent=2))
                handle.write('\n')
            os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary_path, self.path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return updated

    def replace_from_payload(self, payload: dict[str, Any]) -> AdminSettings:
        current = self.load()
        submitted = AdminSettings.model_validate(payload)
        return self.save(submitted.model_copy(update={'revision': current.revision}))


def load_runtime_settings(data_dir: str | None = None) -> AdminSettings | None:
    """Return defaults when the UI has not saved settings yet.

    A malformed operator file must fail a process start loudly instead of silently
    running with unexpected values.
    """
    store = SettingsStore(data_dir)
    return store.load() if store.path.exists() else None
