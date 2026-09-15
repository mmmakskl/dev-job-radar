"""Durable search jobs and source cache in the existing control-plane database.

This is a search audit, not a replacement for Sheets, export state or candidate
publication storage. Legacy Premium tables remain owned by PremiumSearchStore.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tg_vacancy_bot.pipeline.fingerprints import build_text_hash

SOURCES = {'telegram', 'premium', 'threads'}
ACTIVE = {'queued', 'running'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class SearchStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS search_runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, query TEXT NOT NULL,
                    sources_json TEXT NOT NULL, track TEXT NOT NULL, mode TEXT NOT NULL,
                    result_limit INTEGER NOT NULL, period_days INTEGER NOT NULL,
                    include_review INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    ui_chat_id INTEGER, ui_message_id INTEGER, ui_position INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS search_tasks (
                    run_id TEXT NOT NULL REFERENCES search_runs(id), source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', reason TEXT, retry_at TEXT,
                    child_id TEXT, PRIMARY KEY(run_id, source)
                );
                CREATE TABLE IF NOT EXISTS search_items (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
                    permalink TEXT, text TEXT NOT NULL, text_hash TEXT NOT NULL,
                    username TEXT, author TEXT, timestamp TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'und', raw_json TEXT NOT NULL DEFAULT '{}',
                    classification TEXT NOT NULL DEFAULT 'review', confidence REAL,
                    reason TEXT, title TEXT, company TEXT, summary TEXT, analysis_json TEXT,
                    vacancy_id TEXT, premium_result_id TEXT,
                    publication_status TEXT NOT NULL DEFAULT 'preview',
                    action_state TEXT NOT NULL DEFAULT 'idle', action TEXT, action_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(source, external_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS search_source_permalink
                    ON search_items(source, permalink) WHERE permalink IS NOT NULL;
                CREATE TABLE IF NOT EXISTS search_hits (
                    run_id TEXT NOT NULL REFERENCES search_runs(id), item_id TEXT NOT NULL REFERENCES search_items(id),
                    queries_json TEXT NOT NULL DEFAULT '[]', score INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(run_id, item_id)
                );
                CREATE TABLE IF NOT EXISTS search_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS search_requests (at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS search_requests_at ON search_requests(at);
                CREATE INDEX IF NOT EXISTS search_tasks_queue ON search_tasks(source,status,retry_at);
            ''')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_run(
        self,
        *,
        query: str,
        sources: list[str],
        owner: str = 'admin',
        track: str = 'go',
        mode: str = 'preview',
        result_limit: int = 50,
        period_days: int = 7,
        include_review: bool = True,
    ) -> dict:
        query = ' '.join(query.split())
        sources = list(dict.fromkeys(sources))
        if (
            not 3 <= len(query) <= 160
            or not sources
            or not set(sources) <= SOURCES
            or track != 'go'
            or mode not in {'preview', 'save', 'save_publish'}
            or not 1 <= result_limit <= 100
            or not 1 <= period_days <= 30
        ):
            raise ValueError('Недопустимые параметры поиска')
        if owner.startswith('candidate:') and mode != 'preview':
            raise ValueError('Кандидатам доступен только предпросмотр')
        run_id = uuid.uuid4().hex
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if (
                owner.startswith('candidate:')
                and c.execute(
                    "SELECT 1 FROM search_runs WHERE owner=? AND status IN ('queued','running')",
                    (owner,),
                ).fetchone()
            ):
                raise ValueError(
                    'Предыдущий поиск ещё выполняется. Отмените его или дождитесь результатов.'
                )
            c.execute(
                '''INSERT INTO search_runs
                (id,owner,query,sources_json,track,mode,result_limit,period_days,include_review,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    run_id,
                    owner,
                    query,
                    _json(sources),
                    track,
                    mode,
                    result_limit,
                    period_days,
                    int(include_review),
                    now(),
                    now(),
                ),
            )
            c.executemany(
                'INSERT INTO search_tasks(run_id,source) VALUES(?,?)',
                [(run_id, source) for source in sources],
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str, owner: str | None = None) -> dict | None:
        with self.connect() as c:
            row = c.execute(
                'SELECT * FROM search_runs WHERE id=?', (run_id,)
            ).fetchone()
            if row is None or (owner is not None and row['owner'] != owner):
                return None
            run = dict(row)
            run['include_review'] = bool(run['include_review'])
            run['sources'] = json.loads(run.pop('sources_json'))
            run['source_states'] = {
                r['source']: dict(r)
                for r in c.execute(
                    'SELECT source,status,reason,retry_at FROM search_tasks WHERE run_id=?',
                    (run_id,),
                )
            }
            return run

    def list_runs(self, limit: int = 30) -> list[dict]:
        with self.connect() as c:
            ids = [
                r[0]
                for r in c.execute(
                    'SELECT id FROM search_runs ORDER BY created_at DESC LIMIT ?',
                    (limit,),
                )
            ]
        return [self.get_run(rid) for rid in ids]

    def save_ui(
        self, run_id: str, owner: str, chat_id: int, message_id: int, position: int = 0
    ) -> None:
        with self.connect() as c:
            c.execute(
                'UPDATE search_runs SET ui_chat_id=?,ui_message_id=?,ui_position=? WHERE id=? AND owner=?',
                (chat_id, message_id, max(0, position), run_id, owner),
            )

    def clear_ui(self, run_id: str, owner: str) -> None:
        with self.connect() as c:
            c.execute(
                'UPDATE search_runs SET ui_chat_id=NULL,ui_message_id=NULL WHERE id=? AND owner=?',
                (run_id, owner),
            )

    def list_ui_runs(self) -> list[dict]:
        with self.connect() as c:
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM search_runs WHERE owner LIKE 'candidate:%' AND ui_message_id IS NOT NULL ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC LIMIT 100"
                )
            ]
        return [self.get_run(rid) for rid in ids]

    def cancel(self, run_id: str, owner: str | None = None) -> dict | None:
        if self.get_run(run_id, owner) is None:
            return None
        with self.connect() as c:
            c.execute(
                "UPDATE search_runs SET status='cancelled',updated_at=? WHERE id=?",
                (now(), run_id),
            )
            c.execute(
                "UPDATE search_tasks SET status='cancelled' WHERE run_id=? AND status IN ('queued','running')",
                (run_id,),
            )
        return self.get_run(run_id, owner)

    def claim_task(self, source: str) -> dict | None:
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute(
                """SELECT t.* FROM search_tasks t JOIN search_runs r ON r.id=t.run_id
                WHERE t.source=? AND t.status='queued' AND (t.retry_at IS NULL OR t.retry_at<=?)
                AND r.status!='cancelled' ORDER BY r.created_at LIMIT 1""",
                (source, now()),
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE search_tasks SET status='running',reason=NULL,retry_at=NULL WHERE run_id=? AND source=?",
                (row['run_id'], source),
            )
            c.execute(
                "UPDATE search_runs SET status='running',updated_at=? WHERE id=?",
                (now(), row['run_id']),
            )
        return dict(row)

    def update_task(
        self,
        run_id: str,
        source: str,
        *,
        status: str,
        reason: str | None = None,
        retry_at: str | None = None,
        child_id: str | None = None,
    ) -> None:
        with self.connect() as c:
            c.execute(
                """UPDATE search_tasks SET status=?,reason=?,retry_at=?,child_id=COALESCE(?,child_id)
                WHERE run_id=? AND source=? AND status!='cancelled'""",
                (status, reason, retry_at, child_id, run_id, source),
            )
            states = [
                r[0]
                for r in c.execute(
                    'SELECT status FROM search_tasks WHERE run_id=?', (run_id,)
                )
            ]
            state = (
                'running'
                if any(s in ACTIVE for s in states)
                else (
                    'completed_with_errors'
                    if any(s != 'completed' for s in states)
                    else 'completed'
                )
            )
            c.execute(
                "UPDATE search_runs SET status=?,updated_at=? WHERE id=? AND status!='cancelled'",
                (state, now(), run_id),
            )

    def active_tasks(self, source: str) -> list[dict]:
        with self.connect() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM search_tasks WHERE source=? AND status='running'",
                    (source,),
                )
            ]

    def recover(self) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE search_tasks SET status='queued' WHERE status='running' AND (source!='premium' OR child_id IS NULL)"
            )
            c.execute(
                "UPDATE search_items SET action_state='queued' WHERE action_state='running' AND source!='premium' AND action!='publish'"
            )
            c.execute(
                "UPDATE search_items SET action_state='failed',action_error='delivery_uncertain' WHERE action_state='running' AND source!='premium' AND action='publish'"
            )

    def get_item(self, item_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute(
                'SELECT * FROM search_items WHERE id=?', (item_id,)
            ).fetchone()
        return self._item(row) if row else None

    def find_item(self, source: str, external_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute(
                'SELECT * FROM search_items WHERE source=? AND external_id=?',
                (source, external_id),
            ).fetchone()
        return self._item(row) if row else None

    @staticmethod
    def _item(row) -> dict:
        item = dict(row)
        item['raw_data'] = json.loads(item.pop('raw_json'))
        item['analysis'] = json.loads(item.pop('analysis_json') or 'null')
        item['can_persist'] = bool(
            item['text']
            and item['permalink']
            and item['timestamp']
            and item['publication_status'] != 'published'
            and item['action_state'] not in {'queued', 'running'}
            and item['action_error'] != 'delivery_uncertain'
        )
        return item

    def retain(
        self,
        run_id: str,
        *,
        source: str,
        external_id: str,
        text: str,
        timestamp: str,
        permalink: str | None = None,
        queries: list[str] | None = None,
        score: int = 0,
        raw_data: dict | None = None,
        **metadata,
    ) -> str:
        allowed = {
            'username',
            'author',
            'language',
            'classification',
            'confidence',
            'reason',
            'title',
            'company',
            'summary',
            'vacancy_id',
            'premium_result_id',
            'publication_status',
        }
        if not metadata.keys() <= allowed:
            raise ValueError('invalid_search_metadata')
        item_id = hashlib.sha256(f'{source}:{external_id}'.encode()).hexdigest()[:32]
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute(
                'SELECT * FROM search_items WHERE source=? AND (external_id=? OR permalink=?)',
                (source, external_id, permalink),
            ).fetchone()
            if old:
                item_id = old['id']
            values = dict(
                source=source,
                external_id=external_id,
                text=text,
                timestamp=timestamp,
                permalink=permalink,
                text_hash=build_text_hash(text),
                raw_json=_json(raw_data or {}),
                **metadata,
            )
            if old:
                # A new search must never undo an administrator's saved/published decision.
                changed = old['text'] != text
                incoming_status = values.pop('publication_status', 'preview')
                if (
                    old['publication_status'] != 'duplicate'
                    and incoming_status == 'published'
                ):
                    values['publication_status'] = incoming_status
                elif (
                    old['publication_status'] == 'preview'
                    and incoming_status == 'saved'
                ):
                    values['publication_status'] = incoming_status
                if changed:
                    values.update(
                        analysis_json=None,
                        title=metadata.get('title'),
                        company=metadata.get('company'),
                        summary=metadata.get('summary'),
                    )
                if old['reason'] == 'administrator_rejected':
                    values['classification'] = 'rejected'
                    values['reason'] = old['reason']
                c.execute(
                    'UPDATE search_items SET '
                    + ','.join(f'{k}=?' for k in values)
                    + ',updated_at=? WHERE id=?',
                    (*values.values(), now(), item_id),
                )
            else:
                values.update(id=item_id, created_at=now(), updated_at=now())
                c.execute(
                    'INSERT INTO search_items('
                    + ','.join(values)
                    + ') VALUES('
                    + ','.join('?' for _ in values)
                    + ')',
                    tuple(values.values()),
                )
            hit = c.execute(
                'SELECT queries_json FROM search_hits WHERE run_id=? AND item_id=?',
                (run_id, item_id),
            ).fetchone()
            combined = list(
                dict.fromkeys((json.loads(hit[0]) if hit else []) + (queries or []))
            )
            c.execute(
                '''INSERT INTO search_hits(run_id,item_id,queries_json,score) VALUES(?,?,?,?)
                ON CONFLICT(run_id,item_id) DO UPDATE SET queries_json=excluded.queries_json,score=MAX(score,excluded.score)''',
                (run_id, item_id, _json(combined), score),
            )
        return item_id

    def list_results(
        self,
        run_id: str,
        *,
        owner: str | None = None,
        offset: int = 0,
        limit: int = 25,
        include_review: bool = False,
    ) -> dict:
        run = self.get_run(run_id, owner)
        if not run:
            return dict(items=[], total=0, offset=offset, limit=limit)
        with self.connect() as c:
            rows = c.execute(
                '''SELECT i.*,h.queries_json FROM search_items i JOIN search_hits h ON h.item_id=i.id
                WHERE h.run_id=? ORDER BY h.score DESC,i.timestamp DESC,i.id''',
                (run_id,),
            ).fetchall()
        items, seen = [], set()
        for row in rows:
            item = self._item(row)
            if item['classification'] not in (
                {'accepted', 'review'} if include_review else {'accepted'}
            ):
                continue
            if item['publication_status'] == 'duplicate':
                continue
            keys = [
                v
                for v in (
                    item['permalink'],
                    item['vacancy_id'],
                    item['text_hash'] if item['text'] else None,
                )
                if v
            ]
            if any(key in seen for key in keys):
                continue
            seen.update(keys)
            item['found_queries'] = json.loads(item.pop('queries_json'))
            for key in (
                'raw_data',
                'analysis',
                'text_hash',
                'premium_result_id',
                'action',
                'reason',
            ):
                item.pop(key, None)
            items.append(item)
        items = items[: run['result_limit']]
        return dict(
            items=items[offset : offset + limit],
            total=len(items),
            offset=offset,
            limit=limit,
        )

    def update_item(self, item_id: str, **values) -> None:
        allowed = {
            'classification',
            'confidence',
            'reason',
            'language',
            'title',
            'company',
            'summary',
            'analysis_json',
            'publication_status',
            'action_state',
            'action_error',
        }
        if not values or not values.keys() <= allowed:
            raise ValueError('invalid_search_update')
        with self.connect() as c:
            c.execute(
                'UPDATE search_items SET '
                + ','.join(f'{k}=?' for k in values)
                + ',updated_at=? WHERE id=?',
                (*values.values(), now(), item_id),
            )

    def request_action(self, item_id: str, action: str) -> None:
        if action not in {'save', 'publish', 'reject', 'mark_duplicate'}:
            raise ValueError('Недопустимое действие')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute(
                'SELECT * FROM search_items WHERE id=?', (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            item = self._item(row)
            if item['action_state'] in {'queued', 'running'}:
                raise ValueError('Действие уже выполняется')
            if action in {'save', 'publish'} and not item['can_persist']:
                raise ValueError('Вакансия недоступна для сохранения')
            c.execute(
                "UPDATE search_items SET action=?,action_state='queued',action_error=NULL WHERE id=?",
                (action, item_id),
            )

    def claim_action(self) -> dict | None:
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute(
                "SELECT * FROM search_items WHERE action_state='queued' ORDER BY updated_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE search_items SET action_state='running' WHERE id=?",
                (row['id'],),
            )
        return self._item(row)

    def state(self, key: str) -> str | None:
        with self.connect() as c:
            row = c.execute(
                'SELECT value FROM search_state WHERE key=?', (key,)
            ).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as c:
            c.execute(
                'INSERT INTO search_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, value),
            )

    def reserve_request(self, budget: int) -> str | None:
        """Conservatively count every attempt, including errors and empty pages."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM search_requests WHERE at<?', (cutoff,))
            count, oldest = c.execute(
                'SELECT COUNT(*),MIN(at) FROM search_requests'
            ).fetchone()
            if count >= budget:
                return (datetime.fromisoformat(oldest) + timedelta(days=1)).isoformat()
            c.execute('INSERT INTO search_requests VALUES(?)', (now(),))
        return None

    def cleanup(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self.connect() as c:
            c.execute(
                """UPDATE search_items SET raw_json='{}',text='',analysis_json=NULL
                WHERE updated_at<? AND action_state NOT IN ('queued','running')""",
                (cutoff,),
            )
            c.execute(
                'DELETE FROM search_requests WHERE at<?',
                ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
            )
