"""Долговременная дедупликация экспортированных вакансий через JSONL."""

import json
import fcntl
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tg_vacancy_bot.telegram.links import build_vacancy_id


class JsonlDedupeState:
    """Хранит ссылки бессрочно, а текстовые хэши — в пределах TTL."""

    def __init__(self, path: str | Path, ttl_days: int) -> None:
        self.path = Path(path)
        self.ttl = timedelta(days=ttl_days)
        self.exported_links: set[str] = set()
        self.exported_ids: set[str] = set()
        self.recent_text_hashes: set[str] = set()
        self._text_hash_created_at: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self.claims_path = self.path.with_suffix(self.path.suffix + '.claims.sqlite3')

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._restore()
        with self._claims_connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS inflight_claims (
                    claim_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )''')

    def _claims_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.claims_path, timeout=10)
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA journal_mode=WAL')
        return connection

    def claim(
        self, post_link: str, text_hash: str, vacancy_id: str | None = None
    ) -> str | None:
        """Atomically claim a message across workers before external calls."""
        owner = f'{threading.get_ident()}:{datetime.now(timezone.utc).timestamp()}'
        keys = {f'link:{post_link}', f'hash:{text_hash}'}
        if vacancy_id:
            keys.add(f'id:{vacancy_id}')
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        try:
            with self._claims_connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute(
                    'DELETE FROM inflight_claims WHERE created_at < ?', (cutoff,)
                )
                now = datetime.now(timezone.utc).isoformat()
                connection.executemany(
                    'INSERT INTO inflight_claims(claim_key, owner, created_at) VALUES (?,?,?)',
                    [(key, owner, now) for key in keys],
                )
        except sqlite3.IntegrityError:
            return None
        return owner

    def release(self, owner: str) -> None:
        with self._claims_connection() as connection:
            connection.execute('DELETE FROM inflight_claims WHERE owner=?', (owner,))

    def _restore(self) -> None:
        if not self.path.exists():
            return

        cutoff = datetime.now(timezone.utc) - self.ttl
        with self.path.open("r", encoding="utf-8") as state_file:
            for line_number, line in enumerate(state_file, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    self._restore_event(event, cutoff)
                except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                    logging.warning(
                        "Пропуск битой строки JSONL %s:%d: %s",
                        self.path,
                        line_number,
                        exc,
                    )

    def _restore_event(
        self,
        event: Any,
        cutoff: datetime,
    ) -> None:
        if not isinstance(event, dict):
            raise TypeError("JSONL-событие должно быть объектом")
        if event.get("event") != "exported":
            return

        post_link = event["post_link"]
        text_hash = event["text_hash"]
        created_at = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            raise ValueError("created_at должен содержать timezone")
        if not isinstance(post_link, str) or not isinstance(text_hash, str):
            raise TypeError("post_link и text_hash должны быть строками")

        self.exported_links.add(post_link)
        try:
            self.exported_ids.add(build_vacancy_id(post_link))
        except ValueError:
            logging.warning("Не удалось восстановить vacancy ID")
        created_at_utc = created_at.astimezone(timezone.utc)
        if created_at_utc >= cutoff:
            previous = self._text_hash_created_at.get(text_hash)
            if previous is None or created_at_utc > previous:
                self._text_hash_created_at[text_hash] = created_at_utc
            self.recent_text_hashes.add(text_hash)

    def is_duplicate(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> bool:
        """Проверяет вечный дубль ссылки и TTL-дубль текста."""
        with self._lock:
            # Other worker processes may have appended since this instance
            # started. Replaying is idempotent and keeps JSONL as the authority.
            self._restore()
            if post_link in self.exported_links or (
                vacancy_id is not None and vacancy_id in self.exported_ids
            ):
                return True

            created_at = self._text_hash_created_at.get(text_hash)
            if created_at is None:
                return False
            if created_at < datetime.now(timezone.utc) - self.ttl:
                self._text_hash_created_at.pop(text_hash, None)
                self.recent_text_hashes.discard(text_hash)
                return False
            return True

    def duplicate_reason(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> str | None:
        """Return the safe aggregate reason for a duplicate without post data."""
        with self._lock:
            if post_link in self.exported_links or (
                vacancy_id is not None and vacancy_id in self.exported_ids
            ):
                return 'duplicate_link_or_id'
            created_at = self._text_hash_created_at.get(text_hash)
            if (
                created_at is not None
                and created_at >= datetime.now(timezone.utc) - self.ttl
            ):
                return 'duplicate_fingerprint'
        return None

    def mark_exported(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> None:
        """Записывает успешный экспорт одной JSONL-строкой и обновляет sets."""
        created_at = datetime.now().astimezone()
        event = {
            "event": "exported",
            "post_link": post_link,
            "text_hash": text_hash,
            "created_at": created_at.isoformat(),
        }
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            with self.path.open("a", encoding="utf-8") as state_file:
                fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
                try:
                    state_file.write(serialized + "\n")
                    state_file.flush()
                    __import__('os').fsync(state_file.fileno())
                finally:
                    fcntl.flock(state_file.fileno(), fcntl.LOCK_UN)
            self.exported_links.add(post_link)
            if vacancy_id is not None:
                self.exported_ids.add(vacancy_id)
            self.recent_text_hashes.add(text_hash)
            self._text_hash_created_at[text_hash] = created_at.astimezone(timezone.utc)
