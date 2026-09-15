"""Independent source tasks composed around the existing Premium worker."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.premium_search.store import ActiveRunError
from tg_vacancy_bot.search.settings import SearchSettings, source_capabilities
from tg_vacancy_bot.search.store import SearchStore
from tg_vacancy_bot.search.terms import expand_queries
from tg_vacancy_bot.threads.rules import classify_rules, classify_text
from tg_vacancy_bot.threads.source import ThreadsError, ThreadsSource


def relevance(query: str, text: str) -> int:
    def tokens(value):
        value = re.sub(r'\bgo\b', 'golang', value.casefold())
        return set(re.findall(r'\w+', value))

    return len(tokens(query) & tokens(text))


class SearchService:
    def __init__(
        self,
        store: SearchStore,
        processor,
        *,
        candidate_path: str,
        settings: SearchSettings | None = None,
        premium_store=None,
        source=None,
        classifier=classify_text,
        analyzer=None,
    ):
        self.store, self.processor = store, processor
        self.settings = settings or SearchSettings.from_env()
        self.candidate_path = candidate_path
        self.premium_store = premium_store
        self.classifier, self.analyzer = classifier, analyzer
        self.source = source
        self._threads_pause: str | None = None
        self._threads_task: asyncio.Task | None = None
        self._action_task: asyncio.Task | None = None
        self._last_cleanup = 0.0
        self._threads_run_id: str | None = None

    async def serve(self, shutdown: asyncio.Event) -> None:
        self.store.recover()
        if self.source is None and self._threads_available():
            self.source = ThreadsSource(
                self.settings.token,
                timeout=self.settings.timeout,
                max_attempts=self.settings.max_attempts,
                before_request=self._reserve_request,
            )
        try:
            while not shutdown.is_set():
                try:
                    await self.tick()
                except Exception:
                    # Do not leak request exceptions, token-bearing URLs, or source text.
                    logging.error('Поисковый worker: ошибка локальной обработки')
                try:
                    await asyncio.wait_for(shutdown.wait(), 1)
                except asyncio.TimeoutError:
                    pass
        finally:
            tasks = [t for t in (self._threads_task, self._action_task) if t]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.source is not None:
                await self.source.aclose()

    def _threads_available(self) -> bool:
        return next(
            s['enabled']
            for s in source_capabilities(self.settings)
            if s['key'] == 'threads'
        )

    async def tick(self) -> None:
        current = asyncio.get_running_loop().time()
        if current - self._last_cleanup > 3600:
            self.store.cleanup()
            self._last_cleanup = current
        self._schedule()
        local = self.store.claim_task('telegram')
        if local:
            try:
                await asyncio.to_thread(self._local, local['run_id'])
                self.store.update_task(local['run_id'], 'telegram', status='completed')
            except Exception:
                self.store.update_task(
                    local['run_id'],
                    'telegram',
                    status='failed',
                    reason='local_search_unavailable',
                )
        await self._premium()
        if self._threads_task is None or self._threads_task.done():
            if self._threads_task:
                await self._threads_task
            task = self.store.claim_task('threads')
            self._threads_task = (
                asyncio.create_task(self._threads(task['run_id'])) if task else None
            )
        if self._action_task is None or self._action_task.done():
            if self._action_task:
                await self._action_task
            item = self.store.claim_action()
            self._action_task = (
                asyncio.create_task(self._action(item)) if item else None
            )
        self._sync_premium_actions()
        self._automatic_actions()

    def _schedule(self) -> None:
        if not self._threads_available() or not self.settings.interval_hours:
            return
        current = datetime.now(timezone.utc)
        due = self.store.state('threads_next_collection')
        if due and datetime.fromisoformat(due) > current:
            return
        # The live process is the sole scheduler, as for Premium's Telethon session.
        self.store.set_state(
            'threads_next_collection',
            (current + timedelta(hours=self.settings.interval_hours)).isoformat(),
        )
        self.store.create_run(
            query='Golang developer',
            sources=['threads'],
            owner='background',
            include_review=True,
        )

    async def _reserve_request(self) -> None:
        if (
            self._threads_run_id
            and self.store.get_run(self._threads_run_id)['status'] == 'cancelled'
        ):
            raise ThreadsError('cancelled')
        cooldown = self.store.state('threads_cooldown')
        if cooldown:
            delay = (
                datetime.fromisoformat(cooldown) - datetime.now(timezone.utc)
            ).total_seconds()
            if delay > 0:
                raise ThreadsError('rate_limited', delay)
        retry = self.store.reserve_request(self.settings.daily_budget)
        if retry:
            raise ThreadsError(
                'daily_budget_exhausted',
                (
                    datetime.fromisoformat(retry) - datetime.now(timezone.utc)
                ).total_seconds(),
            )

    def _local(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not Path(self.candidate_path).exists():
            return
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=run['period_days'])
        ).isoformat()
        # Read-only: a disabled candidate publisher must not create a new database.
        uri = Path(self.candidate_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT * FROM vacancies WHERE delivery_state='published' AND published_at>=? ORDER BY published_at DESC",
                (cutoff,),
            ).fetchall()
        for row in rows:
            if row['vacancy_id'].startswith('threads:'):
                continue
            text = '\n'.join(
                str(row[k]) for k in ('title', 'company', 'summary') if row[k]
            )
            score = relevance(run['query'], text)
            if not score:
                continue
            self.store.retain(
                run_id,
                source='telegram',
                external_id=row['vacancy_id'],
                text=text,
                timestamp=row['published_at'],
                permalink=row['post_link'],
                title=row['title'],
                company=row['company'],
                summary=row['summary'],
                vacancy_id=row['vacancy_id'],
                classification='accepted',
                publication_status='published',
                queries=[run['query']],
                score=score,
            )

    async def _premium(self) -> None:
        task = self.store.claim_task('premium')
        if task:
            run = self.store.get_run(task['run_id'])
            if self.premium_store is None:
                self.store.update_task(
                    run['id'], 'premium', status='unavailable', reason='disabled'
                )
            else:
                try:
                    child = self.premium_store.create_run(
                        query=run['query'],
                        track=run['track'],
                        mode='preview',
                        result_limit=run['result_limit'],
                        period_days=run['period_days'],
                        include_review=True,
                    )
                    child_id = child['search_run_id']
                except ActiveRunError as error:
                    child_id = error.run_id
                self.store.update_task(
                    run['id'], 'premium', status='running', child_id=child_id
                )
        if self.premium_store is None:
            return
        for task in self.store.active_tasks('premium'):
            run = self.store.get_run(task['run_id'])
            child = self.premium_store.get_run(task['child_id'])
            if child is None:
                self.store.update_task(
                    run['id'],
                    'premium',
                    status='failed',
                    reason='premium_run_unavailable',
                )
                continue
            # Include decisions as they become available rather than waiting for all LLM calls.
            for status in ('accepted', 'review', 'saved', 'published'):
                offset = 0
                while True:
                    page = self.premium_store.list_results(
                        task['child_id'], offset=offset, limit=100, status=status
                    )
                    for preview in page['items']:
                        result = self.premium_store.get_result(preview['result_id'])
                        published = result.get('published_at')
                        if not result.get('vacancy_id') or not published:
                            continue
                        if datetime.fromisoformat(published) < datetime.now(
                            timezone.utc
                        ) - timedelta(days=run['period_days']):
                            continue
                        self.store.retain(
                            run['id'],
                            source='premium',
                            external_id=result['vacancy_id'],
                            text=result.get('raw_text') or '',
                            timestamp=published,
                            permalink=result['post_link'],
                            username=result['channel_username'],
                            title=result['title'],
                            company=result['company'],
                            summary=result['summary'],
                            classification=(
                                'review' if status == 'review' else 'accepted'
                            ),
                            confidence=(
                                result['confidence'] / 100
                                if result.get('confidence') is not None
                                else None
                            ),
                            language=(result.get('analysis') or {}).get(
                                'language', 'und'
                            ),
                            vacancy_id=result['vacancy_id'],
                            premium_result_id=result['result_id'],
                            publication_status=(
                                status
                                if status in {'saved', 'published'}
                                else 'preview'
                            ),
                            queries=[run['query']],
                            score=relevance(run['query'], result.get('raw_text') or ''),
                        )
                    offset += len(page['items'])
                    if not page['items'] or offset >= page['total']:
                        break
            if child['status'] not in {'queued', 'running'}:
                success = child['status'] == 'completed'
                self.store.update_task(
                    run['id'],
                    'premium',
                    status='completed' if success else 'failed',
                    reason=(
                        None
                        if success
                        else child.get('error_reason') or 'premium_search_failed'
                    ),
                )

    async def _threads(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        self._threads_run_id = run_id
        try:
            if (
                not self._threads_available()
                or self.source is None
                or self._threads_pause
            ):
                reason = (
                    self._threads_pause
                    or next(
                        s['reason']
                        for s in source_capabilities(self.settings)
                        if s['key'] == 'threads'
                    )
                    or 'unavailable'
                )
                self.store.update_task(
                    run_id, 'threads', status='unavailable', reason=reason
                )
                return
            until = datetime.now(timezone.utc)
            since = until - timedelta(days=run['period_days'])
            previous = self.store.state('threads_last_collection')
            if run['owner'] == 'background' and previous:
                since = max(
                    since, datetime.fromisoformat(previous) - timedelta(hours=1)
                )
            queries = expand_queries(
                run['query'], self.settings.variants_limit, run['track']
            )
            cursors, seen_cursors, seen_posts = {}, set(), set()
            remaining = self.settings.search_limit
            page_limit = max(1, min(25, remaining // len(queries)))
            invalid = 0
            for page_number in range(self.settings.max_pages):
                for query in queries:
                    if self.store.get_run(run_id)['status'] == 'cancelled':
                        return
                    if remaining <= 0 or (page_number and cursors.get(query) is None):
                        continue
                    page = await self.source.search_page(
                        query,
                        since=since,
                        until=until,
                        limit=min(page_limit, remaining),
                        after=cursors.get(query),
                    )
                    invalid += page.invalid_count
                    for post in page.posts:
                        if self.store.get_run(run_id)['status'] == 'cancelled':
                            return
                        if post.timestamp < since or post.timestamp > until:
                            continue
                        cached = self.store.find_item('threads', post.external_id)
                        if post.external_id not in seen_posts:
                            if remaining <= 0:
                                continue
                            remaining -= 1
                            seen_posts.add(post.external_id)
                        decision = classify_rules(post.text, run['track'])
                        if (
                            cached
                            and cached['text'] == post.text
                            and cached['reason']
                            not in {'classification_failed', 'invalid_classification'}
                        ):
                            classification, confidence, language, reason = (
                                cached[k]
                                for k in (
                                    'classification',
                                    'confidence',
                                    'language',
                                    'reason',
                                )
                            )
                        else:
                            if decision.label == 'AMBIGUOUS':
                                try:
                                    decision = await asyncio.wait_for(
                                        self.classifier(post.text), 30
                                    )
                                except Exception:
                                    if (
                                        self.store.get_run(run_id)['status']
                                        == 'cancelled'
                                    ):
                                        return
                                    item_id = self.store.retain(
                                        run_id,
                                        source='threads',
                                        external_id=post.external_id,
                                        text=post.text,
                                        timestamp=post.timestamp.isoformat(),
                                        permalink=post.permalink,
                                        username=post.username,
                                        raw_data=post.raw_data,
                                        queries=[query],
                                        reason='classification_failed',
                                        vacancy_id='threads:' + post.external_id,
                                    )
                                    self.store.update_item(
                                        item_id,
                                        classification='review',
                                        reason='classification_failed',
                                    )
                                    continue
                            if self.store.get_run(run_id)['status'] == 'cancelled':
                                return
                            classification = (
                                'accepted'
                                if decision.is_job and decision.confidence >= 0.9
                                else (
                                    'review'
                                    if decision.confidence < 0.9
                                    else 'rejected'
                                )
                            )
                            confidence, language, reason = (
                                decision.confidence,
                                decision.language,
                                decision.reason,
                            )
                        item_id = self.store.retain(
                            run_id,
                            source='threads',
                            external_id=post.external_id,
                            text=post.text,
                            timestamp=post.timestamp.isoformat(),
                            permalink=post.permalink,
                            username=post.username,
                            raw_data=post.raw_data,
                            classification=classification,
                            confidence=confidence,
                            language=language,
                            reason=reason,
                            vacancy_id='threads:' + post.external_id,
                            queries=[query],
                            score=relevance(run['query'], post.text),
                        )
                        if cached and cached['reason'] in {
                            'classification_failed',
                            'invalid_classification',
                        }:
                            self.store.update_item(
                                item_id,
                                classification=classification,
                                confidence=confidence,
                                language=language,
                                reason=reason,
                            )
                    cursor = page.after
                    if cursor and (query, cursor) in seen_cursors:
                        invalid += 1
                        cursor = None
                    if cursor:
                        seen_cursors.add((query, cursor))
                    cursors[query] = cursor
            self.store.update_task(
                run_id,
                'threads',
                status='completed' if not invalid else 'failed',
                reason='invalid_response_items' if invalid else None,
            )
            if run['owner'] == 'background' and not invalid:
                self.store.set_state('threads_last_collection', until.isoformat())
        except ThreadsError as error:
            if error.reason in {'rate_limited', 'daily_budget_exhausted'}:
                retry = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=max(1, error.retry_after or 60))
                ).isoformat()
                self.store.set_state('threads_cooldown', retry)
                self.store.update_task(
                    run_id,
                    'threads',
                    status='queued',
                    reason=error.reason,
                    retry_at=retry,
                )
            else:
                if error.reason in {
                    'expired_token',
                    'permission_error',
                    'permission_denied',
                    'invalid_token',
                }:
                    self._threads_pause = error.reason
                self.store.update_task(
                    run_id, 'threads', status='failed', reason=error.reason
                )
        except Exception:
            self.store.update_task(
                run_id, 'threads', status='failed', reason='threads_search_failed'
            )

    def _automatic_actions(self) -> None:
        for run in self.store.list_runs(100):
            if (
                run['status'] not in {'completed', 'completed_with_errors'}
                or run['mode'] == 'preview'
            ):
                continue
            key = 'auto:' + run['id']
            if self.store.state(key):
                continue
            for result in self.store.list_results(run['id'], limit=100)['items']:
                if (
                    result['publication_status'] != 'preview'
                    or not result['can_persist']
                ):
                    continue
                try:
                    self.store.request_action(
                        result['id'],
                        'publish' if run['mode'] == 'save_publish' else 'save',
                    )
                except ValueError:
                    pass
            self.store.set_state(key, 'queued')

    async def _action(self, item: dict) -> None:
        item_id, action = item['id'], item['action']
        try:
            if action in {'reject', 'mark_duplicate'}:
                self.store.update_item(
                    item_id,
                    classification='rejected',
                    reason='administrator_rejected',
                    publication_status=(
                        'duplicate'
                        if action == 'mark_duplicate'
                        else item['publication_status']
                    ),
                    action_state='idle',
                )
                return
            if item['source'] == 'premium':
                if self.premium_store is None:
                    raise ValueError('premium_unavailable')
                self.premium_store.request_action(item['premium_result_id'], action)
                return  # Existing Premium worker owns persistence and its delivery state.
            if self.analyzer is None:
                from tg_vacancy_bot.llm.mistral import analyze_text

                self.analyzer = analyze_text
            if item['analysis']:
                analysis = validate_analysis_result(item['analysis'])
            else:
                analysis = await asyncio.wait_for(self.analyzer(item['text']), 60)
            if analysis is None or not analysis.is_match:
                raise ValueError('analysis_not_accepted')
            if self.store.get_item(item_id)['text'] != item['text']:
                raise ValueError('post_changed')
            self.store.update_item(
                item_id,
                analysis_json=json.dumps(asdict(analysis), ensure_ascii=False),
                title=analysis.title,
                company=analysis.company,
                summary=analysis.summary,
            )
            outcome = await self.processor.persist_analyzed_message(
                raw_text=item['text'],
                post_link=item['permalink'],
                published_at=datetime.fromisoformat(item['timestamp']),
                channel_name='Threads · ' + (item['username'] or 'автор не указан'),
                analysis_result=analysis,
                publish=action == 'publish',
                strict_delivery=True,
                vacancy_id=item['vacancy_id'],
            )
            if outcome.outcome not in {'saved', 'published', 'duplicate'}:
                raise ValueError(outcome.outcome)
            self.store.update_item(
                item_id,
                publication_status=outcome.outcome,
                action_state='idle',
                action_error=None,
            )
        except Exception as error:
            safe = (
                str(error)
                if isinstance(error, ValueError)
                and str(error)
                in {
                    'post_changed',
                    'analysis_not_accepted',
                    'delivery_uncertain',
                    'save_failed',
                    'publish_failed',
                    'publisher_not_configured',
                    'premium_unavailable',
                }
                else 'action_failed'
            )
            self.store.update_item(item_id, action_state='failed', action_error=safe)

    def _sync_premium_actions(self) -> None:
        if self.premium_store is None:
            return
        with self.store.connect() as c:
            rows = c.execute(
                "SELECT id,premium_result_id FROM search_items WHERE source='premium' AND action_state='running'"
            ).fetchall()
        for row in rows:
            result = self.premium_store.get_result(row['premium_result_id'])
            if result is None or result['action_state'] in {'queued', 'running'}:
                continue
            values = dict(
                action_state=result['action_state'],
                action_error=result.get('action_error'),
            )
            if result['status'] in {'saved', 'published', 'duplicate'}:
                values['publication_status'] = result['status']
            self.store.update_item(row['id'], **values)
