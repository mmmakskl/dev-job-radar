"""Durable, transactional Premium queue shared by API and session owner."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tg_vacancy_bot.premium_search.tracks import get_track, normalize_query

RUN_MODES = {'preview', 'save', 'save_publish'}
RUN_STATUSES = {
    'queued',
    'running',
    'completed',
    'completed_with_errors',
    'failed',
    'cancelled',
}
RESULT_STATUSES = {
    'pending',
    'accepted',
    'review',
    'rejected',
    'duplicate',
    'saved',
    'published',
    'error',
    'cancelled',
}
METRICS = (
    'found',
    'invalid',
    'old',
    'prefilter_rejected',
    'duplicates',
    'llm_analyzed',
    'accepted',
    'rejected',
    'review',
    'saved',
    'published',
    'errors',
    'rate_limit_waits',
    'duration_seconds',
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ActiveRunError(ValueError):
    def __init__(self, run_id: str):
        super().__init__('Поиск уже выполняется')
        self.run_id = run_id


class PremiumSearchStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def _migrate(self) -> None:
        with self._connect() as c:
            for attempt in range(5):
                try:
                    c.execute('PRAGMA journal_mode=WAL')
                    break
                except sqlite3.OperationalError as error:
                    if 'locked' not in str(error) or attempt == 4:
                        raise
                    time.sleep(0.02 * (2**attempt))
            c.executescript('''
                CREATE TABLE IF NOT EXISTS premium_search_runs (
                    search_run_id TEXT PRIMARY KEY, query TEXT NOT NULL,
                    normalized_query TEXT NOT NULL, track TEXT NOT NULL, mode TEXT NOT NULL,
                    result_limit INTEGER NOT NULL CHECK(result_limit BETWEEN 1 AND 100),
                    period_days INTEGER NOT NULL CHECK(period_days BETWEEN 1 AND 30),
                    status TEXT NOT NULL, metrics_json TEXT NOT NULL DEFAULT '{}',
                    error_reason TEXT, error_details TEXT, started_at TEXT, finished_at TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS premium_search_results (
                    result_id TEXT PRIMARY KEY, search_run_id TEXT NOT NULL,
                    post_link TEXT, telegram_chat_id TEXT, telegram_message_id INTEGER,
                    channel_username TEXT, channel_name TEXT, published_at TEXT,
                    found_at TEXT NOT NULL, raw_text TEXT, text_hash TEXT,
                    title TEXT, company TEXT, summary TEXT, analysis_json TEXT,
                    status TEXT NOT NULL, decision_reason TEXT NOT NULL,
                    vacancy_id TEXT, group_id TEXT, requested_action TEXT, action_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(search_run_id) REFERENCES premium_search_runs(search_run_id),
                    UNIQUE(search_run_id, post_link)
                );
            ''')
            c.execute('BEGIN IMMEDIATE')
            additions = {
                'premium_search_runs': {
                    'phase': "TEXT NOT NULL DEFAULT 'queued'",
                    'include_review': 'INTEGER NOT NULL DEFAULT 1',
                    'cancel_requested': 'INTEGER NOT NULL DEFAULT 0',
                    'retry_at': 'TEXT',
                    'quota_json': "TEXT NOT NULL DEFAULT '{}'",
                    'llm_calls': 'INTEGER NOT NULL DEFAULT 0',
                    'rate_limit_waits': 'INTEGER NOT NULL DEFAULT 0',
                },
                'premium_search_results': {
                    'apply_urls_json': "TEXT NOT NULL DEFAULT '[]'",
                    'confidence': 'INTEGER',
                    'decision_status': 'TEXT',
                    'action_state': "TEXT NOT NULL DEFAULT 'idle'",
                    'action_origin': "TEXT NOT NULL DEFAULT 'manual'",
                    'delivery_state': "TEXT NOT NULL DEFAULT 'pending'",
                },
            }
            for table, fields in additions.items():
                existing = {
                    row['name'] for row in c.execute(f'PRAGMA table_info({table})')
                }
                for name, definition in fields.items():
                    if name not in existing:
                        c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            # Upgrade old prototype vocabulary without deleting its audit rows.
            for old, new in [
                ('suitable', 'accepted'),
                ('needs_review', 'review'),
                ('invalid', 'rejected'),
                ('saving', 'accepted'),
            ]:
                c.execute(
                    'UPDATE premium_search_results SET status=? WHERE status=?',
                    (new, old),
                )
            # Normalize prototype queries before installing the concurrency constraint.
            active = set()
            for row in c.execute(
                'SELECT * FROM premium_search_runs ORDER BY created_at'
            ).fetchall():
                query = ' '.join(row['query'].split()).casefold()
                c.execute(
                    'UPDATE premium_search_runs SET normalized_query=? WHERE search_run_id=?',
                    (query, row['search_run_id']),
                )
                if row['status'] in {'queued', 'running'}:
                    key = (query, row['track'])
                    if key in active:
                        c.execute(
                            "UPDATE premium_search_runs SET status='cancelled', finished_at=? WHERE search_run_id=?",
                            (now(), row['search_run_id']),
                        )
                    active.add(key)
            c.executescript('''
                CREATE UNIQUE INDEX IF NOT EXISTS premium_active_query
                    ON premium_search_runs(normalized_query, track) WHERE status IN ('queued','running');
                CREATE INDEX IF NOT EXISTS premium_run_queue ON premium_search_runs(status, retry_at, created_at);
                CREATE INDEX IF NOT EXISTS premium_results_run ON premium_search_results(search_run_id, status, created_at);
                CREATE INDEX IF NOT EXISTS premium_results_hash ON premium_search_results(text_hash);
                CREATE INDEX IF NOT EXISTS premium_results_pair ON premium_search_results(telegram_chat_id, telegram_message_id);
                CREATE INDEX IF NOT EXISTS premium_results_action ON premium_search_results(action_state, updated_at);
            ''')
            for table, statuses in [
                ('premium_search_runs', RUN_STATUSES),
                ('premium_search_results', RESULT_STATUSES),
            ]:
                valid = ','.join("'" + value + "'" for value in sorted(statuses))
                for op in ('INSERT', 'UPDATE'):
                    c.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_{op}_status
                        BEFORE {op} ON {table} WHEN NEW.status NOT IN ({valid})
                        BEGIN SELECT RAISE(ABORT, 'invalid status'); END''')

            for operation in ('INSERT', 'UPDATE'):
                c.execute(f'DROP TRIGGER IF EXISTS premium_run_parameters_{operation}')
                c.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS premium_run_parameters_{operation}
                    BEFORE {operation} ON premium_search_runs
                    WHEN NEW.mode NOT IN ('preview','save','save_publish')
                        OR NEW.result_limit NOT BETWEEN 1 AND 100
                        OR NEW.period_days NOT BETWEEN 1 AND 30
                        OR NEW.llm_calls NOT BETWEEN 0 AND 3000
                        OR NEW.include_review NOT IN (0,1) OR NEW.cancel_requested NOT IN (0,1)
                    BEGIN SELECT RAISE(ABORT, 'invalid run parameters'); END"""
                )
                c.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS premium_result_parameters_{operation}
                    BEFORE {operation} ON premium_search_results
                    WHEN NEW.confidence NOT BETWEEN 0 AND 100
                        OR NEW.action_state NOT IN ('idle','queued','running','failed')
                        OR NEW.delivery_state NOT IN ('pending','sending','published')
                    BEGIN SELECT RAISE(ABORT, 'invalid result parameters'); END"""
                )

    def create_run(
        self,
        *,
        query: str,
        track: str = 'go',
        mode: str = 'preview',
        result_limit: int = 50,
        period_days: int = 7,
        include_review: bool = True,
    ) -> dict:
        query = normalize_query(query)
        get_track(track)
        if (
            mode not in RUN_MODES
            or not 1 <= result_limit <= 100
            or not 1 <= period_days <= 30
        ):
            raise ValueError('Недопустимые параметры поиска')
        run_id = str(uuid.uuid4())
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute(
                "SELECT search_run_id FROM premium_search_runs WHERE normalized_query=? AND track=? AND status IN ('queued','running')",
                (query.casefold(), track),
            ).fetchone()
            if existing:
                raise ActiveRunError(existing[0])
            c.execute(
                '''INSERT INTO premium_search_runs(search_run_id,query,normalized_query,track,mode,result_limit,period_days,status,include_review,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,'queued',?,?,?)''',
                (
                    run_id,
                    query,
                    query.casefold(),
                    track,
                    mode,
                    result_limit,
                    period_days,
                    int(include_review),
                    now(),
                    now(),
                ),
            )
            self._metrics(c, run_id)
        return self.get_run(run_id)

    def update_run(self, run_id: str, **values: Any) -> None:
        allowed = {
            'status',
            'phase',
            'error_reason',
            'error_details',
            'retry_at',
            'quota_json',
            'finished_at',
            'cancel_requested',
        }
        assert set(values) <= allowed
        with self._connect() as c:
            c.execute(
                'UPDATE premium_search_runs SET '
                + ','.join(f'{k}=?' for k in values)
                + ',updated_at=? WHERE search_run_id=?',
                (*values.values(), now(), run_id),
            )
            self._metrics(c, run_id)

    def claim_next_run(self) -> dict | None:
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute(
                "SELECT * FROM premium_search_runs WHERE status='queued' AND (retry_at IS NULL OR retry_at<=?) ORDER BY created_at LIMIT 1",
                (now(),),
            ).fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE premium_search_runs SET status='running',started_at=COALESCE(started_at,?),updated_at=? WHERE search_run_id=?",
                (now(), now(), row['search_run_id']),
            )
        return self.get_run(row['search_run_id'])

    def cancel(self, run_id: str) -> None:
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute(
                "UPDATE premium_search_runs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END, updated_at=? WHERE search_run_id=? AND status IN ('queued','running')",
                (now(), now(), run_id),
            )
            c.execute(
                "UPDATE premium_search_results SET status='cancelled' WHERE search_run_id=? AND status='pending'",
                (run_id,),
            )
            c.execute(
                "UPDATE premium_search_results SET action_state='idle',requested_action=NULL WHERE search_run_id=? AND action_origin='run' AND action_state='queued'",
                (run_id,),
            )
            self._metrics(c, run_id)

    def consume_llm_call(self, run_id: str, budget: int) -> bool:
        with self._connect() as c:
            changed = c.execute(
                'UPDATE premium_search_runs SET llm_calls=llm_calls+1 WHERE search_run_id=? AND llm_calls<? AND cancel_requested=0',
                (run_id, budget),
            ).rowcount
            self._metrics(c, run_id)
            return bool(changed)

    def defer(self, run_id: str, seconds: float, phase: str) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE premium_search_runs SET status='queued', phase=?, retry_at=?, rate_limit_waits=rate_limit_waits+1 WHERE search_run_id=? AND cancel_requested=0",
                (
                    phase,
                    (
                        datetime.now(timezone.utc) + timedelta(seconds=seconds)
                    ).isoformat(),
                    run_id,
                ),
            )
            c.execute(
                "UPDATE premium_search_runs SET status='cancelled',finished_at=? WHERE search_run_id=? AND cancel_requested=1 AND status IN ('queued','running')",
                (now(), run_id),
            )
            self._metrics(c, run_id)

    def recover(self) -> None:
        """Resume retained candidates; never replay an interrupted outbound search."""
        with self._connect() as c:
            c.execute(
                "UPDATE premium_search_runs SET status='failed',error_reason='search_interrupted',finished_at=? WHERE status='running' AND phase='searching'",
                (now(),),
            )
            c.execute(
                "UPDATE premium_search_runs SET status='queued' WHERE status='running' AND cancel_requested=0"
            )
            c.execute(
                "UPDATE premium_search_runs SET status='cancelled',finished_at=? WHERE cancel_requested=1 AND status IN ('running','queued')",
                (now(),),
            )
            c.execute(
                "UPDATE premium_search_results SET action_state='failed',action_error='delivery_uncertain' WHERE action_state='running' AND delivery_state='sending'"
            )
            c.execute(
                "UPDATE premium_search_results SET action_state='queued' WHERE action_state='running' AND delivery_state!='sending'"
            )
            c.execute(
                "UPDATE premium_search_results SET action_state='idle',requested_action=NULL WHERE action_origin='run' AND action_state='queued' AND search_run_id IN (SELECT search_run_id FROM premium_search_runs WHERE cancel_requested=1)"
            )
            for row in c.execute(
                'SELECT search_run_id FROM premium_search_runs'
            ).fetchall():
                self._metrics(c, row[0])

    def add_result(self, run_id: str, **values: Any) -> str:
        with self._connect() as c:
            return self._insert_result(c, run_id, values)

    def retain_search(
        self, run_id: str, results: list[dict], *, phase: str = 'analyzing'
    ) -> None:
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            cancelled = c.execute(
                'SELECT cancel_requested FROM premium_search_runs WHERE search_run_id=?',
                (run_id,),
            ).fetchone()[0]
            for values in results:
                if cancelled and values['status'] == 'pending':
                    values = dict(
                        values, status='cancelled', decision_reason='run_cancelled'
                    )
                self._insert_result(c, run_id, values)
            c.execute(
                "UPDATE premium_search_runs SET phase=?,retry_at=NULL WHERE search_run_id=?",
                (phase, run_id),
            )
            self._metrics(c, run_id)

    def _insert_result(self, c: sqlite3.Connection, run_id: str, values: dict) -> str:
        result_id = str(uuid.uuid4())
        values = dict(
            values,
            result_id=result_id,
            search_run_id=run_id,
            found_at=now(),
            created_at=now(),
            updated_at=now(),
        )
        allowed = {
            'result_id',
            'search_run_id',
            'found_at',
            'created_at',
            'updated_at',
            'post_link',
            'telegram_chat_id',
            'telegram_message_id',
            'channel_username',
            'channel_name',
            'published_at',
            'raw_text',
            'text_hash',
            'apply_urls_json',
            'status',
            'decision_reason',
            'vacancy_id',
        }
        assert set(values) <= allowed
        c.execute(
            'INSERT OR IGNORE INTO premium_search_results ('
            + ','.join(values)
            + ') VALUES ('
            + ','.join('?' for _ in values)
            + ')',
            tuple(values.values()),
        )
        row = c.execute(
            'SELECT result_id FROM premium_search_results WHERE search_run_id=? AND (result_id=? OR post_link=?)',
            (run_id, result_id, values.get('post_link')),
        ).fetchone()
        self._metrics(c, run_id)
        return row[0]

    def update_result(self, result_id: str, **values: Any) -> None:
        allowed = {
            'status',
            'decision_reason',
            'analysis_json',
            'confidence',
            'decision_status',
            'title',
            'company',
            'summary',
            'vacancy_id',
            'group_id',
            'requested_action',
            'action_state',
            'action_error',
            'delivery_state',
        }
        assert set(values) <= allowed
        with self._connect() as c:
            c.execute(
                'UPDATE premium_search_results SET '
                + ','.join(f'{k}=?' for k in values)
                + ',updated_at=? WHERE result_id=?',
                (*values.values(), now(), result_id),
            )
            row = c.execute(
                'SELECT search_run_id FROM premium_search_results WHERE result_id=?',
                (result_id,),
            ).fetchone()
            if row:
                self._metrics(c, row[0])

    def duplicate(self, result: dict) -> dict | None:
        urls = set(result['apply_urls'])
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM premium_search_results WHERE result_id!=? AND (analysis_json IS NOT NULL OR status IN ('accepted','duplicate','saved','published') OR (search_run_id=? AND status='error')) ORDER BY created_at",
                (result['result_id'], result['search_run_id']),
            ).fetchall()
        for row in rows:
            if (
                row['post_link'] == result['post_link']
                or row['text_hash'] == result['text_hash']
                or (row['telegram_chat_id'], row['telegram_message_id'])
                == (result['telegram_chat_id'], result['telegram_message_id'])
                or urls.intersection(json.loads(row['apply_urls_json']))
            ):
                return self._result(row, True)
        return None

    def analyzed_results(self, exclude_id: str) -> list[dict]:
        with self._connect() as c:
            return [
                self._result(row, True)
                for row in c.execute(
                    "SELECT * FROM premium_search_results WHERE result_id!=? AND analysis_json IS NOT NULL AND status IN ('accepted','review','saved','published')",
                    (exclude_id,),
                )
            ]

    def request_action(
        self, result_id: str, action: str, *, origin: str = 'manual'
    ) -> dict:
        if action not in {
            'save',
            'publish',
            'reject',
            'mark_duplicate',
            'add_public_source',
        }:
            raise ValueError('Недопустимое действие')
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute(
                'SELECT * FROM premium_search_results WHERE result_id=?', (result_id,)
            ).fetchone()
            if not row:
                raise KeyError(result_id)
            if (
                origin == 'run'
                and c.execute(
                    'SELECT cancel_requested FROM premium_search_runs WHERE search_run_id=?',
                    (row['search_run_id'],),
                ).fetchone()[0]
            ):
                raise ValueError('Поиск отменён')
            if row['action_state'] in {'queued', 'running'}:
                raise ValueError('Действие уже выполняется')
            if action in {'save', 'publish'}:
                if not all(
                    row[key]
                    for key in ('raw_text', 'post_link', 'published_at', 'channel_name')
                ):
                    raise ValueError('Нет доступного анализа для сохранения')
                if origin == 'run' and (
                    row['status'] not in {'accepted', 'review', 'saved', 'published'}
                    or not row['analysis_json']
                ):
                    raise ValueError('Нет доступного анализа для сохранения')
                if row['delivery_state'] == 'sending':
                    raise ValueError('Доставка не определена; повтор запрещён')
            if action == 'add_public_source' and not row['channel_username']:
                raise ValueError('Нет проверенного публичного источника')
            if action in {'reject', 'mark_duplicate'} and row['status'] in {
                'saved',
                'published',
                'pending',
            }:
                raise ValueError('Состояние записи не допускает действие')
            c.execute(
                "UPDATE premium_search_results SET requested_action=?,action_origin=?,action_state='queued',action_error=NULL,updated_at=? WHERE result_id=?",
                (action, origin, now(), result_id),
            )
        return self.public_result(result_id)

    def claim_action(self, result_id: str | None = None) -> dict | None:
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute(
                "SELECT * FROM premium_search_results WHERE action_state='queued' AND (action_origin='manual' OR search_run_id IN (SELECT search_run_id FROM premium_search_runs WHERE cancel_requested=0)) AND (? IS NULL OR result_id=?) ORDER BY updated_at LIMIT 1",
                (result_id, result_id),
            ).fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE premium_search_results SET action_state='running' WHERE result_id=?",
                (row['result_id'],),
            )
        return self.get_result(row['result_id'])

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                'SELECT * FROM premium_search_runs WHERE search_run_id=?', (run_id,)
            ).fetchone()
        return self._run(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._connect() as c:
            return [
                self._run(r)
                for r in c.execute(
                    'SELECT * FROM premium_search_runs ORDER BY created_at DESC,rowid DESC LIMIT ?',
                    (min(limit, 100),),
                )
            ]

    def get_result(self, result_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                'SELECT * FROM premium_search_results WHERE result_id=?', (result_id,)
            ).fetchone()
        return self._result(row, True) if row else None

    def public_result(self, result_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                'SELECT * FROM premium_search_results WHERE result_id=?', (result_id,)
            ).fetchone()
        return self._result(row, False) if row else None

    def pending_results(self, run_id: str) -> list[dict]:
        with self._connect() as c:
            return [
                self._result(r, True)
                for r in c.execute(
                    "SELECT * FROM premium_search_results WHERE search_run_id=? AND (status='pending' OR (status='accepted' AND action_state IN ('idle','queued'))) ORDER BY rowid",
                    (run_id,),
                )
            ]

    def list_results(
        self, run_id: str, offset: int = 0, limit: int = 50, status: str | None = None
    ) -> dict:
        if not status:
            items = self.ranked_results(run_id)
            return dict(
                items=items[offset : offset + limit],
                total=len(items),
                offset=offset,
                limit=limit,
            )
        clause = 'search_run_id=? AND status=?'
        args: list = [run_id, status]
        with self._connect() as c:
            total = c.execute(
                'SELECT COUNT(*) FROM premium_search_results WHERE ' + clause, args
            ).fetchone()[0]
            rows = c.execute(
                'SELECT * FROM premium_search_results WHERE '
                + clause
                + ' ORDER BY rowid LIMIT ? OFFSET ?',
                (*args, limit, offset),
            ).fetchall()
        return dict(
            items=[self._result(r, False) for r in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    def ranked_results(self, run_id: str) -> list[dict]:
        """Return the best unique suitable posts; audit statuses remain queryable."""
        run = self.get_run(run_id)
        if not run:
            return []
        statuses = "'accepted','saved','published'"
        if run['include_review']:
            statuses += ",'review'"
        with self._connect() as c:
            rows = c.execute(
                f"SELECT * FROM premium_search_results WHERE search_run_id=? AND status IN ({statuses}) "
                "ORDER BY CASE WHEN status='review' THEN 1 ELSE 0 END, "
                "confidence DESC, published_at DESC, result_id LIMIT ?",
                (run_id, run['result_limit']),
            ).fetchall()
        return [self._result(row, False) for row in rows]

    def cleanup(self, raw_days: int = 30, metadata_days: int = 90) -> None:
        raw_cutoff = (datetime.now(timezone.utc) - timedelta(days=raw_days)).isoformat()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=metadata_days)
        ).isoformat()
        with self._connect() as c:
            c.execute(
                "UPDATE premium_search_results SET raw_text=NULL WHERE found_at<? AND action_state NOT IN ('queued','running') AND search_run_id IN (SELECT search_run_id FROM premium_search_runs WHERE status NOT IN ('queued','running'))",
                (raw_cutoff,),
            )
            c.execute(
                "DELETE FROM premium_search_results WHERE search_run_id IN (SELECT search_run_id FROM premium_search_runs WHERE finished_at<? AND status NOT IN ('queued','running')) AND action_state NOT IN ('queued','running')",
                (cutoff,),
            )
            c.execute(
                "DELETE FROM premium_search_runs WHERE finished_at<? AND status NOT IN ('queued','running') AND NOT EXISTS(SELECT 1 FROM premium_search_results r WHERE r.search_run_id=premium_search_runs.search_run_id)",
                (cutoff,),
            )

    @staticmethod
    def _metrics(c: sqlite3.Connection, run_id: str) -> None:
        run = c.execute(
            'SELECT * FROM premium_search_runs WHERE search_run_id=?', (run_id,)
        ).fetchone()
        if not run:
            return
        metrics = dict.fromkeys(METRICS, 0)
        for row in c.execute(
            'SELECT status,decision_status,decision_reason,delivery_state,action_state FROM premium_search_results WHERE search_run_id=?',
            (run_id,),
        ):
            metrics['found'] += 1
            status = row['status']
            for key in ('accepted', 'rejected', 'review'):
                metrics[key] += int((row['decision_status'] or status) == key)
            metrics['duplicates'] += int(status == 'duplicate')
            metrics['saved'] += int(status in {'saved', 'published'})
            metrics['published'] += int(status == 'published')
            metrics['errors'] += int(
                status == 'error' or row['action_state'] == 'failed'
            )
            metrics['invalid'] += int(
                row['decision_reason']
                in {'invalid_public_source', 'invalid_date', 'empty_text'}
            )
            metrics['old'] += int(row['decision_reason'] == 'old_content')
            metrics['prefilter_rejected'] += int(
                row['decision_reason'].startswith('prefilter:')
            )
        metrics['errors'] += int(run['status'] == 'failed')
        metrics['llm_analyzed'] = run['llm_calls']
        metrics['rate_limit_waits'] = run['rate_limit_waits']
        if run['started_at']:
            metrics['duration_seconds'] = max(
                0,
                (
                    datetime.fromisoformat(run['finished_at'] or now())
                    - datetime.fromisoformat(run['started_at'])
                ).total_seconds(),
            )
        c.execute(
            'UPDATE premium_search_runs SET metrics_json=? WHERE search_run_id=?',
            (json.dumps(metrics), run_id),
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> dict:
        result = dict(row)
        result['metrics'] = json.loads(result.pop('metrics_json'))
        result['quota'] = json.loads(result.pop('quota_json'))
        return result

    @staticmethod
    def _result(row: sqlite3.Row, raw: bool) -> dict:
        result = dict(row)
        result['analysis'] = json.loads(result.pop('analysis_json') or 'null')
        result['apply_urls'] = json.loads(result.pop('apply_urls_json'))
        if not raw:
            result['can_persist'] = bool(
                result['raw_text']
                and result['post_link']
                and result['published_at']
                and result['channel_name']
                and result['status'] != 'published'
                and result['delivery_state'] != 'sending'
            )
            if result['analysis']:
                result['language'] = result['analysis'].get('language')
                result['go_role_strength'] = result['analysis'].get('go_role_strength')
            for key in (
                'raw_text',
                'telegram_chat_id',
                'telegram_message_id',
                'text_hash',
                'apply_urls',
                'analysis',
                'vacancy_id',
            ):
                result.pop(key, None)
        return result
