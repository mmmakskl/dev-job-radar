"""Transactional actions shared by the API and the bot-owned worker."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from tg_vacancy_bot.admin.settings import SettingsStore
from tg_vacancy_bot.admin.telemetry import sanitize_text

VALID_ACTIONS = {'restart', 'history', 'sync_channels', 'verify_source'}


class ActionConflict(RuntimeError):
    """A different session-mutating action is already active."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS admin_actions (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            target_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed')),
            requested_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            received INTEGER NOT NULL DEFAULT 0 CHECK (received >= 0),
            created INTEGER NOT NULL DEFAULT 0 CHECK (created >= 0),
            updated INTEGER NOT NULL DEFAULT 0 CHECK (updated >= 0),
            skipped INTEGER NOT NULL DEFAULT 0 CHECK (skipped >= 0),
            failed INTEGER NOT NULL DEFAULT 0 CHECK (failed >= 0)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_admin_action
            ON admin_actions((1)) WHERE status IN ('pending','running');
        CREATE INDEX IF NOT EXISTS idx_admin_actions_requested
            ON admin_actions(requested_at DESC);
        ''')


def _connection(data_dir: str | None) -> sqlite3.Connection:
    store = SettingsStore(data_dir)
    connection = store._connect()
    _ensure_schema(connection)
    return connection


def request_action(
    action: str, data_dir: str | None = None, *, target_id: str | None = None
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise ValueError('Недопустимое действие')
    with _connection(data_dir) as connection:
        connection.execute('BEGIN IMMEDIATE')
        active = connection.execute(
            "SELECT * FROM admin_actions WHERE status IN ('pending','running')"
        ).fetchone()
        if active:
            if active['action'] == action and active['target_id'] == target_id:
                result = dict(active)
                result['duplicate'] = True
                return result
            raise ActionConflict('Другая операция уже выполняется')
        action_id = str(uuid.uuid4())
        connection.execute(
            '''INSERT INTO admin_actions(id, action, target_id, status, requested_at)
               VALUES (?, ?, ?, 'pending', ?)''',
            (action_id, action, target_id, _now()),
        )
        row = connection.execute(
            'SELECT * FROM admin_actions WHERE id = ?', (action_id,)
        ).fetchone()
    return dict(row)


def claim_action(data_dir: str | None = None) -> dict[str, Any] | None:
    """Atomically claim the oldest pending action."""
    with _connection(data_dir) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute(
            "SELECT * FROM admin_actions WHERE status = 'pending' ORDER BY requested_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cursor = connection.execute(
            "UPDATE admin_actions SET status='running', started_at=? WHERE id=? AND status='pending'",
            (_now(), row['id']),
        )
        if cursor.rowcount != 1:
            return None
        claimed = connection.execute(
            'SELECT * FROM admin_actions WHERE id=?', (row['id'],)
        ).fetchone()
    return dict(claimed)


def finish_action(
    action_id: str,
    *,
    succeeded: bool,
    data_dir: str | None = None,
    error: str | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    counts = counts or {}
    safe_error = sanitize_text(error, limit=500) if error else None
    with _connection(data_dir) as connection:
        connection.execute(
            '''UPDATE admin_actions SET status=?, finished_at=?, error=?, received=?,
               created=?, updated=?, skipped=?, failed=? WHERE id=? AND status='running' ''',
            (
                'succeeded' if succeeded else 'failed',
                _now(),
                safe_error,
                max(0, counts.get('received', 0)),
                max(0, counts.get('created', 0)),
                max(0, counts.get('updated', 0)),
                max(0, counts.get('skipped', 0)),
                max(0, counts.get('failed', 0)),
                action_id,
            ),
        )
        row = connection.execute(
            'SELECT * FROM admin_actions WHERE id=?', (action_id,)
        ).fetchone()
    if row is None:
        raise KeyError(action_id)
    return dict(row)


def get_action(action_id: str, data_dir: str | None = None) -> dict[str, Any] | None:
    with _connection(data_dir) as connection:
        row = connection.execute(
            'SELECT * FROM admin_actions WHERE id=?', (action_id,)
        ).fetchone()
    return dict(row) if row else None


def active_action(data_dir: str | None = None) -> dict[str, Any] | None:
    with _connection(data_dir) as connection:
        row = connection.execute(
            "SELECT * FROM admin_actions WHERE status IN ('pending','running') ORDER BY requested_at LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def recover_running_actions(data_dir: str | None = None) -> int:
    """Return actions abandoned by a previous bot instance to the queue."""
    with _connection(data_dir) as connection:
        cursor = connection.execute(
            "UPDATE admin_actions SET status='pending', started_at=NULL WHERE status='running'"
        )
    return cursor.rowcount


read_action = claim_action


def acknowledge_action(action_id: str, data_dir: str | None = None) -> None:
    finish_action(action_id, succeeded=True, data_dir=data_dir)
