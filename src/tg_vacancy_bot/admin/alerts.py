"""Cooldown-aware operational alerts delivered through the existing Telegram target."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from tg_vacancy_bot.admin.settings import admin_directory
from tg_vacancy_bot.admin.telemetry import TelemetryStore


SendAlert = Callable[[str], Awaitable[bool]]


class AlertDispatcher:
    """Persist alert state to avoid duplicate notifications across restarts."""

    def __init__(
        self,
        *,
        telemetry: TelemetryStore,
        send: SendAlert | None,
        enabled: bool,
        cooldown_seconds: int,
        data_dir: str | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.send = send
        self.enabled = enabled and send is not None
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.path = admin_directory(data_dir) / 'alerts.json'
        self.started_at = datetime.now(timezone.utc)

    async def observe(self, key: str, unhealthy: bool, message: str) -> None:
        """Send one alert per incident and one recovery after it returns to normal."""
        if not self.enabled:
            return
        state = self._load()
        item = state.get(key, {})
        now = datetime.now(timezone.utc)
        active = bool(item.get('active'))
        last_sent = self._parse_time(item.get('last_sent_at'))
        should_send = unhealthy and (
            not active or last_sent is None or now - last_sent >= self.cooldown
        )
        if not unhealthy and active:
            should_send = True
            message = f'✅ Восстановление: {message}'
        if should_send and self.send is not None:
            try:
                delivered = await self.send(message)
            except Exception:
                delivered = False
            if delivered:
                item['last_sent_at'] = now.isoformat()
                item['active'] = unhealthy
                state[key] = item
                self._save(state)
                self.telemetry.record('alert_sent', alert=key, active=unhealthy)
        elif active != unhealthy:
            # Remember the actual incident state even if delivery is temporarily down.
            item['active'] = unhealthy
            state[key] = item
            self._save(state)

    async def check(
        self,
        *,
        monitoring_enabled: bool,
        queue_size: int,
        queue_maxsize: int,
        queue_warning_percent: int,
        error_streak_threshold: int,
        error_window_seconds: int,
        no_export_seconds: int,
        heartbeat_stale_seconds: int,
    ) -> None:
        """Evaluate queue, dependent-service errors and idle-export conditions."""
        queue_limit = max(1, queue_maxsize * queue_warning_percent / 100)
        await self.observe(
            'queue_pressure',
            queue_size >= queue_limit,
            f'Очередь обработки: {queue_size}/{queue_maxsize}.',
        )
        for component, label in (
            ('llm', 'Mistral'),
            ('google_sheets', 'Google Sheets'),
        ):
            errors = self.telemetry.recent_metric_count(
                'processing_error', component, error_window_seconds
            )
            await self.observe(
                f'{component}_errors',
                errors >= error_streak_threshold,
                f'Серия ошибок {label}: {errors} за последние {error_window_seconds} сек.',
            )

        if monitoring_enabled:
            latest_export = self.telemetry.latest_metric_at('vacancy_saved')
            reference = latest_export or self.started_at
            no_export = reference is not None and (
                datetime.now(timezone.utc) - reference
                >= timedelta(seconds=no_export_seconds)
            )
            await self.observe(
                'no_successful_export',
                no_export,
                'Нет успешного экспорта вакансий дольше настроенного порога.',
            )

        heartbeat = self.telemetry.read_heartbeat()
        updated_at = self._parse_time((heartbeat or {}).get('updated_at'))
        stale = updated_at is not None and (
            datetime.now(timezone.utc) - updated_at
            >= timedelta(seconds=heartbeat_stale_seconds)
        )
        await self.observe(
            'heartbeat_stale',
            stale,
            'Heartbeat бота не обновлялся дольше настроенного порога.',
        )

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, state: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix='.alerts.', text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(state, handle, ensure_ascii=False, separators=(',', ':'))
            os.replace(temporary_path, self.path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
