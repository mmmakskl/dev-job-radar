"""SQLite persistence for personal candidate actions and neutral reports."""

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STATUSES = (
    'new',
    'saved',
    'applied',
    'replied',
    'interview',
    'offer',
    'rejected',
    'hidden',
)
APPLICATION_STATUSES = ('applied', 'replied', 'interview', 'offer', 'rejected')
REPORT_REASONS = ('suspicious', 'duplicate', 'outdated', 'other')


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
    status: str = 'new'


@dataclass(frozen=True)
class BrowserSession:
    """Private, server-side state for one user's single-message vacancy browser."""

    token: str
    telegram_user_id: int
    bucket: str
    position: int
    chat_id: int | None
    message_id: int | None
    vacancy_ids: tuple[str, ...]


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
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.executescript(
                '''
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
                CREATE TABLE IF NOT EXISTS user_vacancy_actions (
                    telegram_user_id INTEGER NOT NULL,
                    vacancy_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    personal_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_user_id, vacancy_id),
                    FOREIGN KEY (vacancy_id) REFERENCES vacancies(vacancy_id)
                );
                CREATE TABLE IF NOT EXISTS vacancy_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    vacancy_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (vacancy_id) REFERENCES vacancies(vacancy_id)
                );
                CREATE INDEX IF NOT EXISTS idx_actions_user_status
                    ON user_vacancy_actions (telegram_user_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_reports_vacancy
                    ON vacancy_reports (vacancy_id, created_at);
                CREATE TABLE IF NOT EXISTS candidate_browser_sessions (
                    token TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL,
                    bucket TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    chat_id INTEGER,
                    message_id INTEGER,
                    vacancy_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_browser_sessions_user
                    ON candidate_browser_sessions (telegram_user_id, updated_at);
                '''
            )
            self._add_column_if_missing(
                connection,
                'vacancies',
                'delivery_state',
                "TEXT NOT NULL DEFAULT 'pending'",
            )
            self._add_column_if_missing(
                connection, 'vacancies', 'channel_message_id', 'INTEGER'
            )
            self._add_column_if_missing(
                connection,
                'candidate_browser_sessions',
                'vacancy_ids_json',
                "TEXT NOT NULL DEFAULT '[]'",
            )

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
    ) -> CandidateVacancy:
        """Upserts a shared-channel vacancy without touching any personal action."""
        now = utc_now()
        callback_key = callback_key_for(vacancy_id)
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO vacancies (
                    vacancy_id, callback_key, title, company, summary, post_link,
                    apply_link, published_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vacancy_id) DO UPDATE SET
                    title=excluded.title, company=excluded.company,
                    summary=excluded.summary, post_link=excluded.post_link,
                    apply_link=excluded.apply_link, published_at=excluded.published_at,
                    updated_at=excluded.updated_at
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
                ),
            )
        return CandidateVacancy(
            vacancy_id=vacancy_id,
            callback_key=callback_key,
            title=title,
            company=company,
            summary=summary,
            post_link=post_link,
            apply_link=apply_link,
            published_at=published_at,
        )

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

    def set_status(
        self, telegram_user_id: int, callback_key: str, status: str
    ) -> CandidateVacancy | None:
        """Sets one user's status while leaving the shared channel message intact."""
        if status not in STATUSES:
            raise ValueError(f'Unsupported vacancy status: {status}')
        vacancy = self.get_vacancy(callback_key)
        if vacancy is None:
            return None
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO user_vacancy_actions (
                    telegram_user_id, vacancy_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, vacancy_id) DO UPDATE SET
                    status=excluded.status, updated_at=excluded.updated_at
                ''',
                (telegram_user_id, vacancy.vacancy_id, status, now, now),
            )
        return CandidateVacancy(**{**vacancy.__dict__, 'status': status})

    def add_report(self, telegram_user_id: int, callback_key: str, reason: str) -> bool:
        """Stores a private moderation signal; it is never broadcast to candidates."""
        if reason not in REPORT_REASONS:
            raise ValueError(f'Unsupported report reason: {reason}')
        vacancy = self.get_vacancy(callback_key)
        if vacancy is None:
            return False
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO vacancy_reports (
                    telegram_user_id, vacancy_id, reason, created_at
                ) VALUES (?, ?, ?, ?)
                ''',
                (telegram_user_id, vacancy.vacancy_id, reason, utc_now()),
            )
        return True

    def get_vacancy(self, callback_key: str) -> CandidateVacancy | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM vacancies WHERE callback_key = ?', (callback_key,)
            ).fetchone()
        return self._vacancy_from_row(row) if row else None

    def list_for_user(
        self, telegram_user_id: int, bucket: str, limit: int | None = None
    ) -> list[CandidateVacancy]:
        """Lists a beta user's private view, including unclaimed shared vacancies."""
        filters = {
            'new': "(a.status IS NULL OR a.status = 'new')",
            'saved': "a.status = 'saved'",
            'applications': "a.status IN ('applied', 'replied', 'interview', 'offer', 'rejected')",
            'hidden': "a.status = 'hidden'",
        }
        if bucket not in filters:
            raise ValueError(f'Unsupported vacancy bucket: {bucket}')
        order_by = (
            'COALESCE(v.published_at, v.created_at) DESC'
            if bucket == 'new'
            else 'a.updated_at DESC'
        )
        limit_clause = 'LIMIT ?' if limit is not None else ''
        parameters: tuple[int, ...] = (
            (telegram_user_id, limit) if limit is not None else (telegram_user_id,)
        )
        with self._connect() as connection:
            rows = connection.execute(
                f'''
                SELECT v.*, COALESCE(a.status, 'new') AS status
                FROM vacancies v
                LEFT JOIN user_vacancy_actions a
                    ON a.vacancy_id = v.vacancy_id AND a.telegram_user_id = ?
                WHERE {filters[bucket]}
                ORDER BY {order_by}
                {limit_clause}
                ''',
                parameters,
            ).fetchall()
        return [self._vacancy_from_row(row) for row in rows]

    def create_browser_session(
        self,
        telegram_user_id: int,
        bucket: str,
        vacancies: list[CandidateVacancy],
    ) -> BrowserSession:
        """Creates opaque state; callback payloads never expose vacancy/user IDs."""
        if bucket not in {'new', 'saved', 'applications', 'hidden'}:
            raise ValueError(f'Unsupported vacancy bucket: {bucket}')
        token = secrets.token_hex(8)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO candidate_browser_sessions (
                    token, telegram_user_id, bucket, position, created_at, updated_at
                    , vacancy_ids_json
                ) VALUES (?, ?, ?, 0, ?, ?, ?)
                ''',
                (
                    token,
                    telegram_user_id,
                    bucket,
                    now,
                    now,
                    json.dumps([item.vacancy_id for item in vacancies]),
                ),
            )
        return BrowserSession(
            token,
            telegram_user_id,
            bucket,
            0,
            None,
            None,
            tuple(item.vacancy_id for item in vacancies),
        )

    def attach_browser_message(
        self, token: str, telegram_user_id: int, chat_id: int, message_id: int
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                '''
                UPDATE candidate_browser_sessions
                SET chat_id = ?, message_id = ?, updated_at = ?
                WHERE token = ? AND telegram_user_id = ?
                ''',
                (chat_id, message_id, utc_now(), token, telegram_user_id),
            )
        return result.rowcount == 1

    def get_browser_session(
        self, token: str, telegram_user_id: int
    ) -> BrowserSession | None:
        with self._connect() as connection:
            row = connection.execute(
                '''
                SELECT token, telegram_user_id, bucket, position, chat_id, message_id,
                       vacancy_ids_json
                FROM candidate_browser_sessions
                WHERE token = ? AND telegram_user_id = ?
                ''',
                (token, telegram_user_id),
            ).fetchone()
        if row is None:
            return None
        return BrowserSession(
            row['token'],
            row['telegram_user_id'],
            row['bucket'],
            row['position'],
            row['chat_id'],
            row['message_id'],
            tuple(json.loads(row['vacancy_ids_json'])),
        )

    def set_browser_position(
        self, token: str, telegram_user_id: int, position: int
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                '''
                UPDATE candidate_browser_sessions SET position = ?, updated_at = ?
                WHERE token = ? AND telegram_user_id = ?
                ''',
                (max(0, position), utc_now(), token, telegram_user_id),
            )
        return result.rowcount == 1

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
            status=row['status'] if 'status' in row.keys() else 'new',
        )
