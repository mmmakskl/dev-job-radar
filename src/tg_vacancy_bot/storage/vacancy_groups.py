"""Conservative, local grouping of independently published vacancy reposts."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tg_vacancy_bot.models import NOT_SPECIFIED, VacancyAnalysis
from tg_vacancy_bot.pipeline.fingerprints import (
    normalize_application_url,
    normalize_group_text,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _group_id(vacancy_id: str) -> str:
    return 'grp_' + hashlib.sha256(vacancy_id.encode('utf-8')).hexdigest()[:20]


def _known(value: str | None) -> str | None:
    return value if value and value != NOT_SPECIFIED else None


@dataclass(frozen=True)
class GroupDecision:
    """Outcome of registering one analyzed publication into local group history."""

    group_id: str
    canonical_vacancy_id: str
    is_canonical: bool
    merge_reason: str | None
    left_separate_candidate: bool = False


class VacancyGroupStore:
    """SQLite group state with strict equality rules and idempotent migrations."""

    def __init__(self, path: str, window_days: int) -> None:
        if window_days <= 0:
            raise ValueError('window_days must be greater than zero')
        self.path = path
        self.window_days = window_days
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
                CREATE TABLE IF NOT EXISTS vacancy_groups (
                    group_id TEXT PRIMARY KEY,
                    canonical_vacancy_id TEXT NOT NULL UNIQUE,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_publications (
                    vacancy_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    post_link TEXT NOT NULL UNIQUE,
                    channel_name TEXT,
                    published_at TEXT NOT NULL,
                    text_hash TEXT,
                    company_key TEXT,
                    title_key TEXT,
                    contact_key TEXT,
                    apply_url_key TEXT,
                    merge_reason TEXT NOT NULL,
                    is_canonical INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (group_id) REFERENCES vacancy_groups(group_id)
                );
                CREATE TABLE IF NOT EXISTS blocked_group_pairs (
                    vacancy_id_a TEXT NOT NULL,
                    vacancy_id_b TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (vacancy_id_a, vacancy_id_b),
                    CHECK (vacancy_id_a < vacancy_id_b)
                );
                CREATE INDEX IF NOT EXISTS idx_group_publications_lookup
                    ON group_publications (published_at, contact_key, apply_url_key,
                    company_key, title_key);
                CREATE INDEX IF NOT EXISTS idx_group_publications_text_hash
                    ON group_publications (text_hash);
                '''
            )

    def register_publication(
        self,
        *,
        vacancy_id: str,
        post_link: str,
        channel_name: str,
        data: VacancyAnalysis,
        published_at: datetime,
        text_hash: str,
    ) -> GroupDecision:
        """Registers one LLM-analyzed post using only strong, exact evidence."""
        with self._connect() as connection:
            decision = self._decision(connection, vacancy_id, data, published_at)
            if self._publication_exists(connection, vacancy_id):
                return decision

            keys = self._keys(data)
            published_value = published_at.astimezone(timezone.utc).isoformat()
            now = _now()
            if decision.is_canonical:
                connection.execute(
                    '''
                    INSERT INTO vacancy_groups (
                        group_id, canonical_vacancy_id, first_seen_at, last_seen_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        decision.group_id,
                        vacancy_id,
                        published_value,
                        published_value,
                        now,
                        now,
                    ),
                )
                self._insert_publication(
                    connection,
                    vacancy_id=vacancy_id,
                    group_id=decision.group_id,
                    post_link=post_link,
                    channel_name=channel_name,
                    published_at=published_value,
                    text_hash=text_hash,
                    keys=keys,
                    merge_reason='canonical',
                    is_canonical=True,
                    now=now,
                )
                return decision

            self._insert_publication(
                connection,
                vacancy_id=vacancy_id,
                group_id=decision.group_id,
                post_link=post_link,
                channel_name=channel_name,
                published_at=published_value,
                text_hash=text_hash,
                keys=keys,
                merge_reason=decision.merge_reason or 'unknown',
                is_canonical=False,
                now=now,
            )
            self._refresh_group_times(connection, decision.group_id, now)
            return decision

    def preview_publication(
        self,
        *,
        vacancy_id: str,
        data: VacancyAnalysis,
        published_at: datetime,
    ) -> GroupDecision:
        """Checks grouping before export without creating a durable group record."""
        with self._connect() as connection:
            return self._decision(connection, vacancy_id, data, published_at)

    def record_exact_repost(
        self,
        *,
        vacancy_id: str,
        post_link: str,
        channel_name: str,
        published_at: datetime,
        text_hash: str,
    ) -> bool:
        """Adds a source for an exact text repost without changing exact dedupe."""
        with self._connect() as connection:
            if connection.execute(
                'SELECT 1 FROM group_publications WHERE vacancy_id = ?', (vacancy_id,)
            ).fetchone():
                return False
            source = connection.execute(
                '''
                SELECT group_id FROM group_publications
                WHERE text_hash = ? ORDER BY published_at DESC LIMIT 1
                ''',
                (text_hash,),
            ).fetchone()
            if source is None:
                return False
            now = _now()
            published_value = published_at.astimezone(timezone.utc).isoformat()
            connection.execute(
                '''
                INSERT INTO group_publications (
                    vacancy_id, group_id, post_link, channel_name, published_at,
                    text_hash, merge_reason, is_canonical, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'exact_text_hash', 0, ?, ?)
                ''',
                (
                    vacancy_id,
                    source['group_id'],
                    post_link,
                    channel_name,
                    published_value,
                    text_hash,
                    now,
                    now,
                ),
            )
            self._refresh_group_times(connection, source['group_id'], now)
            return True

    def list_groups(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                '''
                SELECT g.group_id, g.canonical_vacancy_id, g.first_seen_at,
                       g.last_seen_at, p.company_key, p.title_key,
                       COUNT(member.vacancy_id) AS publication_count
                FROM vacancy_groups g
                JOIN group_publications p
                    ON p.vacancy_id = g.canonical_vacancy_id
                JOIN group_publications member ON member.group_id = g.group_id
                GROUP BY g.group_id
                ORDER BY g.last_seen_at DESC LIMIT ?
                ''',
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_group(self, group_id: str) -> dict | None:
        with self._connect() as connection:
            group = connection.execute(
                'SELECT * FROM vacancy_groups WHERE group_id = ?', (group_id,)
            ).fetchone()
            if group is None:
                return None
            publications = connection.execute(
                '''
                SELECT vacancy_id, post_link, channel_name, published_at,
                       merge_reason, is_canonical
                FROM group_publications WHERE group_id = ?
                ORDER BY published_at ASC, created_at ASC
                ''',
                (group_id,),
            ).fetchall()
        payload = dict(group)
        payload['publications'] = [dict(item) for item in publications]
        return payload

    def unlink_publication(self, group_id: str, vacancy_id: str) -> bool:
        """Moves a linked repost into a new group and blocks its old pairings."""
        with self._connect() as connection:
            source = connection.execute(
                'SELECT * FROM group_publications WHERE vacancy_id = ? AND group_id = ?',
                (vacancy_id, group_id),
            ).fetchone()
            if source is None or source['is_canonical']:
                return False
            related = connection.execute(
                'SELECT vacancy_id FROM group_publications WHERE group_id = ?',
                (group_id,),
            ).fetchall()
            new_group_id = _group_id(vacancy_id)
            now = _now()
            connection.execute(
                '''
                INSERT INTO vacancy_groups (
                    group_id, canonical_vacancy_id, first_seen_at, last_seen_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (
                    new_group_id,
                    vacancy_id,
                    source['published_at'],
                    source['published_at'],
                    now,
                    now,
                ),
            )
            connection.execute(
                '''
                UPDATE group_publications
                SET group_id = ?, is_canonical = 1, merge_reason = 'manual_unlinked',
                    updated_at = ?
                WHERE vacancy_id = ?
                ''',
                (new_group_id, now, vacancy_id),
            )
            for item in related:
                other = item['vacancy_id']
                if other == vacancy_id:
                    continue
                first, second = sorted((vacancy_id, other))
                connection.execute(
                    '''
                    INSERT OR IGNORE INTO blocked_group_pairs
                    (vacancy_id_a, vacancy_id_b, created_at) VALUES (?, ?, ?)
                    ''',
                    (first, second, now),
                )
            self._refresh_group_times(connection, group_id, now)
            return True

    def _candidates(self, connection: sqlite3.Connection, published_at: datetime):
        lower = (published_at - self._window()).astimezone(timezone.utc).isoformat()
        upper = (published_at + self._window()).astimezone(timezone.utc).isoformat()
        return connection.execute(
            '''
            SELECT p.*, g.canonical_vacancy_id FROM group_publications p
            JOIN vacancy_groups g ON g.group_id = p.group_id
            WHERE p.published_at BETWEEN ? AND ?
            ORDER BY p.published_at DESC
            ''',
            (lower, upper),
        ).fetchall()

    def _decision(
        self,
        connection: sqlite3.Connection,
        vacancy_id: str,
        data: VacancyAnalysis,
        published_at: datetime,
    ) -> GroupDecision:
        existing = connection.execute(
            '''
            SELECT g.group_id, g.canonical_vacancy_id, p.is_canonical, p.merge_reason
            FROM group_publications p
            JOIN vacancy_groups g ON g.group_id = p.group_id
            WHERE p.vacancy_id = ?
            ''',
            (vacancy_id,),
        ).fetchone()
        if existing:
            return GroupDecision(
                group_id=existing['group_id'],
                canonical_vacancy_id=existing['canonical_vacancy_id'],
                is_canonical=bool(existing['is_canonical']),
                merge_reason=(
                    None if existing['is_canonical'] else existing['merge_reason']
                ),
            )
        keys = self._keys(data)
        left_separate = False
        for candidate in self._candidates(connection, published_at):
            reason = self._merge_reason(keys, candidate)
            if reason is None:
                if self._weak_candidate(keys, candidate):
                    left_separate = True
                continue
            if self._pair_is_blocked(connection, vacancy_id, candidate['vacancy_id']):
                left_separate = True
                continue
            return GroupDecision(
                candidate['group_id'],
                candidate['canonical_vacancy_id'],
                False,
                reason,
                left_separate,
            )
        return GroupDecision(
            _group_id(vacancy_id), vacancy_id, True, None, left_separate
        )

    @staticmethod
    def _publication_exists(connection: sqlite3.Connection, vacancy_id: str) -> bool:
        return bool(
            connection.execute(
                'SELECT 1 FROM group_publications WHERE vacancy_id = ?', (vacancy_id,)
            ).fetchone()
        )

    def _window(self):
        return timedelta(days=self.window_days)

    @staticmethod
    def _keys(data: VacancyAnalysis) -> dict[str, str | None]:
        return {
            'company_key': normalize_group_text(_known(data.company)),
            'title_key': normalize_group_text(_known(data.title)),
            'contact_key': normalize_group_text(_known(data.contact)),
            'apply_url_key': normalize_application_url(_known(data.apply_link)),
        }

    @staticmethod
    def _merge_reason(
        keys: dict[str, str | None], candidate: sqlite3.Row
    ) -> str | None:
        if (
            keys['apply_url_key']
            and keys['apply_url_key'] == candidate['apply_url_key']
        ):
            return 'same_application_url'
        if keys['contact_key'] and keys['contact_key'] == candidate['contact_key']:
            return 'same_contact'
        if (
            keys['company_key']
            and keys['title_key']
            and keys['company_key'] == candidate['company_key']
            and keys['title_key'] == candidate['title_key']
        ):
            return 'same_company_and_title'
        return None

    @staticmethod
    def _weak_candidate(keys: dict[str, str | None], candidate: sqlite3.Row) -> bool:
        return bool(
            (keys['company_key'] and keys['company_key'] == candidate['company_key'])
            or (keys['title_key'] and keys['title_key'] == candidate['title_key'])
        )

    @staticmethod
    def _pair_is_blocked(
        connection: sqlite3.Connection, first: str, second: str
    ) -> bool:
        first, second = sorted((first, second))
        return bool(
            connection.execute(
                '''
                SELECT 1 FROM blocked_group_pairs
                WHERE vacancy_id_a = ? AND vacancy_id_b = ?
                ''',
                (first, second),
            ).fetchone()
        )

    @staticmethod
    def _insert_publication(
        connection: sqlite3.Connection,
        *,
        vacancy_id: str,
        group_id: str,
        post_link: str,
        channel_name: str,
        published_at: str,
        text_hash: str,
        keys: dict[str, str | None],
        merge_reason: str,
        is_canonical: bool,
        now: str,
    ) -> None:
        connection.execute(
            '''
            INSERT INTO group_publications (
                vacancy_id, group_id, post_link, channel_name, published_at,
                text_hash, company_key, title_key, contact_key, apply_url_key,
                merge_reason, is_canonical, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                vacancy_id,
                group_id,
                post_link,
                channel_name,
                published_at,
                text_hash,
                keys['company_key'],
                keys['title_key'],
                keys['contact_key'],
                keys['apply_url_key'],
                merge_reason,
                int(is_canonical),
                now,
                now,
            ),
        )

    @staticmethod
    def _refresh_group_times(
        connection: sqlite3.Connection, group_id: str, now: str
    ) -> None:
        connection.execute(
            '''
            UPDATE vacancy_groups
            SET first_seen_at = (
                    SELECT MIN(published_at) FROM group_publications WHERE group_id = ?
                ),
                last_seen_at = (
                    SELECT MAX(published_at) FROM group_publications WHERE group_id = ?
                ),
                updated_at = ?
            WHERE group_id = ?
            ''',
            (group_id, group_id, now, group_id),
        )
