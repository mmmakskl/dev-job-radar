"""SQLite persistence for candidate profiles, feed cards and saved vacancies."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tg_vacancy_bot.candidate_catalog import (
    CATALOG_VERSION,
    validate_direction_stacks,
    validate_profile_path,
)


@dataclass(frozen=True)
class CandidateProfile:
    profile_id: str
    telegram_user_id: int
    name: str
    direction_id: str
    specialization_id: str
    role_id: str
    preferences: dict
    is_active: bool
    version: int


@dataclass(frozen=True)
class CandidateVacancy:
    """The minimum vacancy data required to render a personal Bot API card."""

    vacancy_id: str
    callback_key: str
    title: str
    company: str | None
    summary: str | None
    post_link: str
    apply_link: str | None
    published_at: str | None


def callback_key_for(vacancy_id: str) -> str:
    """Creates a short opaque callback lookup key, safely below Telegram's limit."""
    return hashlib.sha256(vacancy_id.encode('utf-8')).hexdigest()[:20]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class CandidateStore:
    """Idempotently migrates and accesses the local candidate SQLite database."""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 10000')
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS vacancies (
                    vacancy_id TEXT PRIMARY KEY,
                    callback_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    company TEXT,
                    summary TEXT,
                    post_link TEXT NOT NULL,
                    apply_link TEXT,
                    published_at TEXT,
                    delivery_state TEXT NOT NULL DEFAULT 'pending',
                    channel_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                ''')
            self._add_column_if_missing(
                connection,
                'vacancies',
                'delivery_state',
                "TEXT NOT NULL DEFAULT 'pending'",
            )
            self._add_column_if_missing(
                connection, 'vacancies', 'channel_message_id', 'INTEGER'
            )
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS user_saved_vacancies (
                    telegram_user_id INTEGER NOT NULL,
                    vacancy_id TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_user_id, vacancy_id),
                    FOREIGN KEY (vacancy_id) REFERENCES vacancies(vacancy_id)
                );
                CREATE INDEX IF NOT EXISTS idx_saved_user_time
                    ON user_saved_vacancies (telegram_user_id, saved_at DESC);
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    profile_id TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    direction_id TEXT NOT NULL,
                    specialization_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    preferences_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    current_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profiles_user
                    ON candidate_profiles (telegram_user_id, created_at);
                CREATE TABLE IF NOT EXISTS candidate_profile_versions (
                    profile_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, version),
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(profile_id)
                        ON DELETE CASCADE
                );
            ''')
            connection.execute("""CREATE TABLE IF NOT EXISTS candidate_profile_drafts (
                telegram_user_id INTEGER PRIMARY KEY,
                draft_json TEXT NOT NULL
            )""")
            connection.execute(
                '''CREATE TABLE IF NOT EXISTS candidate_feed_preferences (
                telegram_user_id INTEGER PRIMARY KEY,
                older_days INTEGER NOT NULL DEFAULT 7,
                awaiting_custom_days INTEGER NOT NULL DEFAULT 0,
                CHECK (older_days BETWEEN 2 AND 3650)
            )'''
            )
            self._add_column_if_missing(
                connection, 'vacancies', 'go_visible', 'INTEGER NOT NULL DEFAULT 1'
            )
            # Additive migration: archive saved actions and preserve legacy tables.
            tables = {
                row['name']
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if 'user_vacancy_actions' in tables:
                connection.execute('''
                    INSERT OR IGNORE INTO user_saved_vacancies
                        (telegram_user_id, vacancy_id, saved_at)
                    SELECT a.telegram_user_id, a.vacancy_id, a.updated_at
                    FROM user_vacancy_actions a
                    JOIN vacancies v ON v.vacancy_id = a.vacancy_id
                    WHERE a.status='saved'
                ''')
            # Version 4 changes only the profile contract. Legacy role columns and
            # rows are intentionally retained unchanged; role-neutral profiles use
            # empty legacy fields and are validated by direction.
            connection.execute('PRAGMA user_version = 4')

    def _profile_from_row(self, row: sqlite3.Row) -> CandidateProfile:
        return CandidateProfile(
            row['profile_id'],
            row['telegram_user_id'],
            row['name'],
            row['direction_id'],
            row['specialization_id'],
            row['role_id'],
            {'delivery_mode': 'manual', **json.loads(row['preferences_json'])},
            bool(row['is_active']),
            row['current_version'],
        )

    @staticmethod
    def _validate_preferences(preferences: dict | None) -> dict:
        values = dict(preferences or {})
        list_fields = {
            'stacks',
            'additional_languages',
            'seniority',
            'formats',
            'geography',
            'vacancy_languages',
            'required_skills',
            'desired_skills',
            'excluded_skills',
        }
        unknown = (
            set(values)
            - list_fields
            - {'timezone', 'delivery_mode', 'premium_template_id', 'primary_language'}
        )
        if unknown:
            raise ValueError(f'Unsupported profile fields: {sorted(unknown)}')
        for field in list_fields:
            value = values.get(field, [])
            if not isinstance(value, list) or any(
                not isinstance(x, str) or not x.strip() for x in value
            ):
                raise ValueError(f'{field} must be a list of non-empty strings')
            values[field] = list(dict.fromkeys(x.strip() for x in value))
        primary_language = values.get('primary_language')
        allowed_languages = {
            'go',
            'python',
            'java',
            'javascript',
            'cpp',
            'csharp',
            'rust',
            'php',
            'kotlin',
            'swift',
        }
        if primary_language is not None and primary_language not in allowed_languages:
            raise ValueError('Unsupported primary programming language')
        timezone_name = values.get('timezone', 'Europe/Moscow')
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError('timezone must be a valid IANA timezone') from None
        mode = values.get('delivery_mode', 'manual')
        if mode not in {'manual', 'immediate', 'hourly'}:
            raise ValueError('Invalid delivery mode')
        values['delivery_mode'] = mode
        template = values.get('premium_template_id')
        if template is not None and not isinstance(template, str):
            raise ValueError('Invalid Premium template ID')
        values['timezone'] = timezone_name
        return values

    def create_profile(
        self,
        telegram_user_id: int,
        *,
        name: str,
        direction_id: str,
        specialization_id: str,
        role_id: str,
        preferences: dict | None = None,
        is_active: bool = True,
        _connection: sqlite3.Connection | None = None,
    ) -> CandidateProfile:
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Profile name must not be empty')
        if not isinstance(is_active, bool):
            raise ValueError('is_active must be a boolean')
        preferences = self._validate_preferences(preferences)
        self._validate_profile_path(
            direction_id, specialization_id, role_id, preferences['stacks']
        )
        self._clear_invalid_template(role_id, preferences)
        name = name.strip()
        profile_id, now = uuid.uuid4().hex, utc_now()
        snapshot = {
            'name': name,
            'direction_id': direction_id,
            'specialization_id': specialization_id,
            'role_id': role_id,
            'preferences': preferences,
            'is_active': bool(is_active),
            'catalog_version': CATALOG_VERSION,
        }
        with (
            nullcontext(_connection) if _connection is not None else self._connect()
        ) as connection:
            if _connection is None:
                connection.execute('BEGIN IMMEDIATE')
            if is_active:
                self._deactivate_others(connection, telegram_user_id, profile_id)
            connection.execute(
                '''INSERT INTO candidate_profiles VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                (
                    profile_id,
                    telegram_user_id,
                    name,
                    direction_id,
                    specialization_id,
                    role_id,
                    json.dumps(preferences, ensure_ascii=False),
                    int(is_active),
                    now,
                    now,
                ),
            )
            connection.execute(
                'INSERT INTO candidate_profile_versions VALUES (?, 1, ?, ?)',
                (profile_id, json.dumps(snapshot, ensure_ascii=False), now),
            )
            row = connection.execute(
                'SELECT * FROM candidate_profiles WHERE profile_id=?', (profile_id,)
            ).fetchone()
        return self._profile_from_row(row)

    def update_profile(
        self,
        telegram_user_id: int,
        profile_id: str,
        *,
        expected_version: int | None = None,
        _connection: sqlite3.Connection | None = None,
        **changes,
    ) -> CandidateProfile | None:
        allowed = {
            'name',
            'direction_id',
            'specialization_id',
            'role_id',
            'preferences',
            'is_active',
        }
        if set(changes) - allowed:
            raise ValueError(
                f'Unsupported profile fields: {sorted(set(changes) - allowed)}'
            )
        with (
            nullcontext(_connection) if _connection is not None else self._connect()
        ) as connection:
            if _connection is None:
                connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT * FROM candidate_profiles WHERE profile_id=? AND telegram_user_id=?',
                (profile_id, telegram_user_id),
            ).fetchone()
            if row is None:
                return None
            current = self._profile_from_row(row)
            if expected_version is not None and current.version != expected_version:
                return None
            values = {
                'name': current.name,
                'direction_id': current.direction_id,
                'specialization_id': current.specialization_id,
                'role_id': current.role_id,
                'preferences': current.preferences,
                'is_active': current.is_active,
            }
            values.update(changes)
            if not isinstance(values['name'], str) or not values['name'].strip():
                raise ValueError('Profile name must not be empty')
            if not isinstance(values['is_active'], bool):
                raise ValueError('is_active must be a boolean')
            values['name'] = values['name'].strip()
            values['preferences'] = self._validate_preferences(values['preferences'])
            self._validate_profile_path(
                values['direction_id'],
                values['specialization_id'],
                values['role_id'],
                values['preferences'].get('stacks', []),
            )
            self._clear_invalid_template(values['role_id'], values['preferences'])
            if values['is_active'] and not current.is_active:
                self._deactivate_others(connection, telegram_user_id, profile_id)
            version, now = current.version + 1, utc_now()
            snapshot = {**values, 'catalog_version': CATALOG_VERSION}
            connection.execute(
                '''UPDATE candidate_profiles SET name=?, direction_id=?,
                specialization_id=?, role_id=?, preferences_json=?, is_active=?,
                current_version=?, updated_at=? WHERE profile_id=? AND telegram_user_id=?''',
                (
                    values['name'],
                    values['direction_id'],
                    values['specialization_id'],
                    values['role_id'],
                    json.dumps(values['preferences'], ensure_ascii=False),
                    int(values['is_active']),
                    version,
                    now,
                    profile_id,
                    telegram_user_id,
                ),
            )
            connection.execute(
                'INSERT INTO candidate_profile_versions VALUES (?, ?, ?, ?)',
                (profile_id, version, json.dumps(snapshot, ensure_ascii=False), now),
            )
            return self._profile_from_row(
                connection.execute(
                    'SELECT * FROM candidate_profiles WHERE profile_id=?', (profile_id,)
                ).fetchone()
            )

    @staticmethod
    def _clear_invalid_template(role_id: str, preferences: dict) -> None:
        from tg_vacancy_bot.premium_search.templates import list_templates

        if preferences.get('premium_template_id') not in {
            t.id
            for t in list_templates({'role_id': role_id, 'preferences': preferences})
        }:
            preferences.pop('premium_template_id', None)

    def get_profile(
        self, telegram_user_id: int, profile_id: str
    ) -> CandidateProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM candidate_profiles WHERE telegram_user_id=? AND profile_id=?',
                (telegram_user_id, profile_id),
            ).fetchone()
        return self._profile_from_row(row) if row else None

    def get_profile_draft(self, telegram_user_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT draft_json FROM candidate_profile_drafts WHERE telegram_user_id=?',
                (telegram_user_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_profile_draft(
        self, telegram_user_id: int, draft: dict, expected_revision: int | None = None
    ) -> bool:
        """Persist a draft with compare-and-swap protection against repeated actions."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT draft_json FROM candidate_profile_drafts WHERE telegram_user_id=?',
                (telegram_user_id,),
            ).fetchone()
            if expected_revision is not None and (
                row is None
                or json.loads(row[0])['revision'] != expected_revision
                or json.loads(row[0])['token'] != draft['token']
            ):
                return False
            if expected_revision is None and row is not None:
                return False
            connection.execute(
                'INSERT OR REPLACE INTO candidate_profile_drafts VALUES (?, ?)',
                (telegram_user_id, json.dumps(draft, ensure_ascii=False)),
            )
        return True

    def finish_profile_draft(
        self, telegram_user_id: int, token: str, revision: int, *, save: bool
    ) -> CandidateProfile | bool | None:
        """Save one version and remove its draft in the same transaction, or cancel."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT draft_json FROM candidate_profile_drafts WHERE telegram_user_id=?',
                (telegram_user_id,),
            ).fetchone()
            if row is None:
                return None
            draft = json.loads(row[0])
            if draft['token'] != token or draft['revision'] != revision:
                return None
            result = True
            if save:
                if draft['step'] != 'preview':
                    return None
                values = draft['values']
                if draft.get('profile_id'):
                    result = self.update_profile(
                        telegram_user_id,
                        draft['profile_id'],
                        expected_version=draft['profile_version'],
                        _connection=connection,
                        **values,
                    )
                    if result is None:
                        return None
                else:
                    result = self.create_profile(
                        telegram_user_id, _connection=connection, **values
                    )
            connection.execute(
                'DELETE FROM candidate_profile_drafts WHERE telegram_user_id=?',
                (telegram_user_id,),
            )
        return result

    def list_profiles(self, telegram_user_id: int) -> list[CandidateProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT * FROM candidate_profiles WHERE telegram_user_id=? ORDER BY created_at',
                (telegram_user_id,),
            ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def get_active_profile_state(self, telegram_user_id: int) -> str:
        """Report legacy ambiguity without silently changing stored active flags."""
        active = [p for p in self.list_profiles(telegram_user_id) if p.is_active]
        return 'none' if not active else 'single' if len(active) == 1 else 'ambiguous'

    def get_active_profile(self, telegram_user_id: int) -> CandidateProfile | None:
        """Return the selected profile only when the owner has exactly one active."""
        active = [p for p in self.list_profiles(telegram_user_id) if p.is_active]
        return active[0] if len(active) == 1 else None

    def activate_profile(
        self, telegram_user_id: int, profile_id: str
    ) -> CandidateProfile | None:
        """Atomically select one owned profile, versioning every changed profile."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT 1 FROM candidate_profiles WHERE telegram_user_id=? AND profile_id=?',
                (telegram_user_id, profile_id),
            ).fetchone()
            if row is None:
                return None
            self._deactivate_others(connection, telegram_user_id, profile_id)
            current = self.get_profile(telegram_user_id, profile_id)
            if current is not None and not current.is_active:
                now = utc_now()
                version = current.version + 1
                connection.execute(
                    'UPDATE candidate_profiles SET is_active=1,current_version=?,updated_at=? '
                    'WHERE telegram_user_id=? AND profile_id=?',
                    (version, now, telegram_user_id, profile_id),
                )
                snapshot = {
                    'name': current.name,
                    'direction_id': current.direction_id,
                    'specialization_id': current.specialization_id,
                    'role_id': current.role_id,
                    'preferences': current.preferences,
                    'is_active': True,
                    'catalog_version': CATALOG_VERSION,
                }
                connection.execute(
                    'INSERT INTO candidate_profile_versions VALUES(?,?,?,?)',
                    (
                        profile_id,
                        version,
                        json.dumps(snapshot, ensure_ascii=False),
                        now,
                    ),
                )
        return self.get_profile(telegram_user_id, profile_id)

    @staticmethod
    def _deactivate_others(
        connection: sqlite3.Connection, telegram_user_id: int, selected_id: str
    ) -> None:
        rows = connection.execute(
            'SELECT * FROM candidate_profiles WHERE telegram_user_id=? '
            'AND profile_id!=? AND is_active=1',
            (telegram_user_id, selected_id),
        ).fetchall()
        now = utc_now()
        for row in rows:
            version = int(row['current_version']) + 1
            snapshot = {
                'name': row['name'],
                'direction_id': row['direction_id'],
                'specialization_id': row['specialization_id'],
                'role_id': row['role_id'],
                'preferences': json.loads(row['preferences_json']),
                'is_active': False,
                'catalog_version': CATALOG_VERSION,
            }
            connection.execute(
                'UPDATE candidate_profiles SET is_active=0,current_version=?,updated_at=? '
                'WHERE profile_id=?',
                (version, now, row['profile_id']),
            )
            connection.execute(
                'INSERT INTO candidate_profile_versions VALUES(?,?,?,?)',
                (
                    row['profile_id'],
                    version,
                    json.dumps(snapshot, ensure_ascii=False),
                    now,
                ),
            )

    @staticmethod
    def _validate_profile_path(
        direction_id: str,
        specialization_id: str,
        role_id: str,
        stacks: list[str],
    ) -> None:
        if not specialization_id and not role_id:
            validate_direction_stacks(direction_id, stacks)
        elif not specialization_id or not role_id:
            raise ValueError('Legacy specialization and role must both be present')
        else:
            validate_profile_path(direction_id, specialization_id, role_id, stacks)

    def get_profile_versions(
        self, telegram_user_id: int, profile_id: str
    ) -> list[dict]:
        with self._connect() as connection:
            owned = connection.execute(
                'SELECT 1 FROM candidate_profiles WHERE profile_id=? AND telegram_user_id=?',
                (profile_id, telegram_user_id),
            ).fetchone()
            if not owned:
                return []
            rows = connection.execute(
                'SELECT version, snapshot_json, created_at FROM candidate_profile_versions WHERE profile_id=? ORDER BY version',
                (profile_id,),
            ).fetchall()
        return [
            {
                'version': row['version'],
                'snapshot': json.loads(row['snapshot_json']),
                'created_at': row['created_at'],
            }
            for row in rows
        ]

    def delete_profile(self, telegram_user_id: int, profile_id: str) -> bool:
        with self._connect() as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            result = connection.execute(
                'DELETE FROM candidate_profiles WHERE profile_id=? AND telegram_user_id=?',
                (profile_id, telegram_user_id),
            )
        return result.rowcount == 1

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row['name'] for row in connection.execute(f'PRAGMA table_info({table})')
        }
        if column not in columns:
            connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def register_vacancy(
        self,
        *,
        vacancy_id: str,
        title: str,
        company: str | None,
        summary: str | None,
        post_link: str,
        apply_link: str | None,
        published_at: str | None,
        go_visible: bool = True,
        update_existing: bool = True,
    ) -> CandidateVacancy:
        """Register a card; private archives can atomically preserve existing fields."""
        now = utc_now()
        callback_key = callback_key_for(vacancy_id)
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO vacancies (
                    vacancy_id, callback_key, title, company, summary, post_link,
                    apply_link, published_at, created_at, updated_at, go_visible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vacancy_id) DO UPDATE SET
                    title=excluded.title, company=excluded.company,
                    summary=excluded.summary, post_link=excluded.post_link,
                    apply_link=excluded.apply_link,
                    published_at=COALESCE(excluded.published_at,vacancies.published_at),
                    go_visible=MAX(vacancies.go_visible,excluded.go_visible),
                    updated_at=excluded.updated_at
                WHERE ?
                ''',
                (
                    vacancy_id,
                    callback_key,
                    title,
                    company,
                    summary,
                    post_link,
                    apply_link,
                    published_at,
                    now,
                    now,
                    int(go_visible),
                    int(update_existing),
                ),
            )
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM vacancies WHERE vacancy_id=?', (vacancy_id,)
            ).fetchone()
        return self._vacancy_from_row(row)

    def channel_delivery_state(self, vacancy_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT delivery_state FROM vacancies WHERE vacancy_id=?', (vacancy_id,)
            ).fetchone()
        return row[0] if row else None

    def claim_channel_delivery(self, vacancy_id: str) -> bool:
        """Claims a card once; an interrupted send stays claimed to prevent duplicates."""
        with self._connect() as connection:
            result = connection.execute(
                '''
                UPDATE vacancies SET delivery_state = 'sending', updated_at = ?
                WHERE vacancy_id = ? AND delivery_state = 'pending'
                ''',
                (utc_now(), vacancy_id),
            )
        return result.rowcount == 1

    def mark_channel_published(self, vacancy_id: str, message_id: int | None) -> None:
        """Records the Bot API message after its first successful delivery."""
        with self._connect() as connection:
            connection.execute(
                '''
                UPDATE vacancies
                SET delivery_state = 'published', channel_message_id = ?, updated_at = ?
                WHERE vacancy_id = ?
                ''',
                (message_id, utc_now(), vacancy_id),
            )

    def release_channel_delivery(self, vacancy_id: str) -> None:
        """Return a failed delivery claim to pending so it can be retried."""
        with self._connect() as connection:
            connection.execute(
                '''
                UPDATE vacancies
                SET delivery_state = 'pending', updated_at = ?
                WHERE vacancy_id = ? AND delivery_state = 'sending'
                ''',
                (utc_now(), vacancy_id),
            )

    def get_vacancy(self, callback_key: str) -> CandidateVacancy | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM vacancies WHERE callback_key = ?', (callback_key,)
            ).fetchone()
        return self._vacancy_from_row(row) if row else None

    def save_vacancy(self, telegram_user_id: int, callback_key: str) -> bool:
        vacancy = self.get_vacancy(callback_key)
        if vacancy is None:
            return False
        with self._connect() as connection:
            connection.execute(
                '''INSERT OR IGNORE INTO user_saved_vacancies
                (telegram_user_id, vacancy_id, saved_at) VALUES (?, ?, ?)''',
                (telegram_user_id, vacancy.vacancy_id, utc_now()),
            )
        return True

    def list_for_user(
        self,
        telegram_user_id: int,
        bucket: str,
        *,
        now: datetime | None = None,
        older_days: int = 7,
    ) -> list[CandidateVacancy]:
        if bucket not in {'new', 'older', 'undated', 'saved'}:
            raise ValueError(f'Unsupported vacancy bucket: {bucket}')
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        if not 2 <= older_days <= 3650:
            raise ValueError('older_days must be between 2 and 3650')
        cutoff_24h = now - timedelta(hours=24)
        cutoff_n_days = now - timedelta(days=older_days)
        if bucket == 'saved':
            query = '''SELECT v.* FROM vacancies v
                JOIN user_saved_vacancies s ON s.vacancy_id=v.vacancy_id
                WHERE s.telegram_user_id=? ORDER BY s.saved_at DESC'''
            parameters = (telegram_user_id,)
            with self._connect() as connection:
                rows = connection.execute(query, parameters).fetchall()
            return [self._vacancy_from_row(row) for row in rows]
        with self._connect() as connection:
            rows = connection.execute(
                '''SELECT v.* FROM vacancies v WHERE v.go_visible=1
                AND NOT EXISTS (SELECT 1 FROM user_saved_vacancies s
                    WHERE s.vacancy_id=v.vacancy_id AND s.telegram_user_id=?)''',
                (telegram_user_id,),
            ).fetchall()
        classified = []
        for row in rows:
            published = self._trusted_published_datetime(row['published_at'])
            if bucket == 'undated':
                include = published is None
            elif published is None:
                include = False
            elif bucket == 'new':
                include = cutoff_24h <= published <= now
            else:
                include = cutoff_n_days <= published < cutoff_24h
            if include:
                classified.append((published, row))
        if bucket == 'undated':
            classified.sort(key=lambda pair: pair[1]['created_at'], reverse=True)
        else:
            classified.sort(key=lambda pair: pair[0], reverse=True)
        return [self._vacancy_from_row(row) for _, row in classified]

    @staticmethod
    def _trusted_published_datetime(value: str | None) -> datetime | None:
        """Parse a source timestamp only when it carries an explicit UTC offset."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    def get_older_days(self, telegram_user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT older_days FROM candidate_feed_preferences WHERE telegram_user_id=?',
                (telegram_user_id,),
            ).fetchone()
        return int(row['older_days']) if row else 7

    def max_older_days(self) -> int:
        """Allow custom windows through the oldest dated item actually stored."""
        with self._connect() as connection:
            values = [
                row[0]
                for row in connection.execute(
                    'SELECT published_at FROM vacancies WHERE go_visible=1 '
                    'AND published_at IS NOT NULL'
                )
            ]
        dates = [
            parsed
            for value in values
            if (parsed := self._trusted_published_datetime(value)) is not None
        ]
        age = (
            max((datetime.now(timezone.utc) - value).days for value in dates)
            if dates
            else 0
        )
        return max(30, min(3650, age))

    def set_older_days(self, telegram_user_id: int, days: int) -> bool:
        if (
            not isinstance(days, int)
            or isinstance(days, bool)
            or not 2 <= days <= self.max_older_days()
        ):
            return False
        with self._connect() as connection:
            connection.execute(
                '''INSERT INTO candidate_feed_preferences
                (telegram_user_id,older_days,awaiting_custom_days) VALUES(?,?,0)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                older_days=excluded.older_days,awaiting_custom_days=0''',
                (telegram_user_id, days),
            )
        return True

    def request_custom_days(self, telegram_user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                '''INSERT INTO candidate_feed_preferences
                (telegram_user_id,older_days,awaiting_custom_days) VALUES(?,7,1)
                ON CONFLICT(telegram_user_id) DO UPDATE SET awaiting_custom_days=1''',
                (telegram_user_id,),
            )

    def take_custom_days_request(self, telegram_user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT awaiting_custom_days FROM candidate_feed_preferences WHERE telegram_user_id=?',
                (telegram_user_id,),
            ).fetchone()
            if not row or not row['awaiting_custom_days']:
                return False
            connection.execute(
                'UPDATE candidate_feed_preferences SET awaiting_custom_days=0 WHERE telegram_user_id=?',
                (telegram_user_id,),
            )
            return True

    @staticmethod
    def _vacancy_from_row(row: sqlite3.Row) -> CandidateVacancy:
        return CandidateVacancy(
            vacancy_id=row['vacancy_id'],
            callback_key=row['callback_key'],
            title=row['title'],
            company=row['company'],
            summary=row['summary'],
            post_link=row['post_link'],
            apply_link=row['apply_link'],
            published_at=row['published_at'],
        )
