"""Single-concurrency public post search on the live process's Telethon client."""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from openai import APIConnectionError, APIStatusError, APITimeoutError
from telethon import errors, functions, types, utils

from tg_vacancy_bot.pipeline.fingerprints import (
    build_text_hash,
    normalize_application_url,
)
from tg_vacancy_bot.premium_search.analyzer import (
    PremiumAnalyzer,
    PremiumAnalysis,
    analyze_premium_text,
    adapt_universal,
    parse_premium_analysis,
)
from tg_vacancy_bot.premium_search.store import PremiumSearchStore, now
from tg_vacancy_bot.telegram.links import build_vacancy_id
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore

_USERNAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{3,31}\Z')
CATALOG_MAX_MESSAGES = 10_000
DEFAULT_MAX_MESSAGES = 1_000
CATALOG_TIME_LIMIT = timedelta(minutes=20)
_URL = re.compile(r'https?://[^\s<>]+', re.I)
_APPLY = re.compile(
    r'apply|application|careers?|jobs?|vacanc|отклик|подать|анкет', re.I
)


class RunCancelled(Exception):
    pass


class QuotaExhausted(Exception):
    pass


def extract_apply_urls(text: str, entities: list | None = None) -> list[str]:
    """Extract application URLs only; ignore homepages, post links and trackers."""
    candidates = [
        (m.group().rstrip('.,;:!?)\"]}'), text[max(0, m.start() - 60) : m.end()])
        for m in _URL.finditer(text)
    ]
    for entity in entities or []:
        url = getattr(entity, 'url', None)
        if url:
            start = max(0, getattr(entity, 'offset', 0) - 60)
            end = getattr(entity, 'offset', 0) + getattr(entity, 'length', 0)
            context = text.encode('utf-16-le')[start * 2 : end * 2].decode(
                'utf-16-le', errors='ignore'
            )
            candidates.append((url, context))
    urls = set()
    for url, context in candidates:
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {'http', 'https'}
                or not parsed.hostname
                or parsed.username
            ):
                continue
            if parsed.hostname.casefold() in {'t.me', 'telegram.me'}:
                continue
            if parsed.path in {'', '/'} or not _APPLY.search(
                context + ' ' + parsed.path
            ):
                continue
            normalized = normalize_application_url(url)
            if normalized:
                urls.add(normalized)
        except ValueError:
            continue
    return sorted(urls)


def normalize_result(
    message: Any,
    channel: Any,
    period_days: int,
    track: str = 'go',
    profile_snapshot: dict | None = None,
) -> dict:
    """Keep rejected candidates in the audit while exposing only verified sources."""
    text = str(getattr(message, 'message', '') or '').strip()
    values = dict(
        raw_text=text,
        text_hash=build_text_hash(text),
        status='pending',
        decision_reason='pending',
        apply_urls_json='[]',
        published_at=None,
    )
    date = getattr(message, 'date', None)
    if isinstance(date, datetime):
        date = (
            date.replace(tzinfo=timezone.utc)
            if date.tzinfo is None
            else date.astimezone(timezone.utc)
        )
        values['published_at'] = date.isoformat()
    username = getattr(channel, 'username', None)
    if not username:
        username = next(
            (
                getattr(item, 'username', None)
                for item in getattr(channel, 'usernames', []) or []
                if getattr(item, 'active', False)
            ),
            None,
        )
    message_id = getattr(message, 'id', None)
    channel_id = getattr(channel, 'id', None)
    peer_id = getattr(getattr(message, 'peer_id', None), 'channel_id', None)
    if (
        not isinstance(channel, types.Channel)
        or not channel.broadcast
        or channel.megagroup
        or not isinstance(username, str)
        or not _USERNAME.fullmatch(username)
        or getattr(channel, 'restricted', False)
        or getattr(message, 'restriction_reason', None)
        or type(channel_id) is not int
        or channel_id <= 0
        or type(message_id) is not int
        or message_id <= 0
        or channel_id != peer_id
    ):
        return dict(values, status='rejected', decision_reason='invalid_public_source')
    link = f'https://t.me/{username.lower()}/{message_id}'
    values.update(
        post_link=link,
        channel_username=username.lower(),
        channel_name=channel.title or username,
        telegram_chat_id=str(-(1000000000000 + channel_id)),
        telegram_message_id=message_id,
        vacancy_id=build_vacancy_id(link),
    )
    if date is not None and not isinstance(date, datetime):
        return dict(values, status='rejected', decision_reason='invalid_date')
    if not text:
        return dict(values, status='review', decision_reason='empty_text')
    if date is not None and date < datetime.now(timezone.utc) - timedelta(
        days=period_days
    ):
        return dict(values, status='rejected', decision_reason='old_content')
    if track == 'catalog':
        from tg_vacancy_bot.premium_search.tracks import prefilter_reason
        from tg_vacancy_bot.profile_matcher import profile_text_prefilter_reason

        reason = prefilter_reason(text, track)
        if reason is None and profile_snapshot:
            reason = profile_text_prefilter_reason(
                profile_snapshot.get('preferences') or {}, text
            )
        if reason:
            return dict(
                values, status='rejected', decision_reason=f'prefilter:{reason}'
            )
    values['apply_urls_json'] = json.dumps(
        extract_apply_urls(text, getattr(message, 'entities', None))
    )
    return values


class PremiumSearchService:
    def __init__(
        self,
        client,
        store: PremiumSearchStore,
        processor,
        telemetry,
        analyzer: PremiumAnalyzer = analyze_premium_text,
        max_llm_calls: int = 3000,
        live_queue: asyncio.Queue | None = None,
        settings_store=None,
    ):
        self.client, self.store, self.processor, self.telemetry = (
            client,
            store,
            processor,
            telemetry,
        )
        self.analyzer = analyzer
        self.max_llm_calls = max_llm_calls
        self.live_queue = live_queue
        self.settings_store = settings_store
        self._lock = asyncio.Lock()

    async def serve(self, shutdown: asyncio.Event) -> None:
        self.store.recover()
        self.store.cleanup()
        cleanup_at = asyncio.get_running_loop().time() + 86400
        source_retry_at = asyncio.get_running_loop().time() + 300
        while not shutdown.is_set():
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= cleanup_at:
                self.store.cleanup()
                cleanup_at = loop_time + 86400
            if loop_time >= source_retry_at:
                await self._retry_pending_sources()
                source_retry_at = loop_time + 300
            await self.tick()
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

    async def _retry_pending_sources(self) -> None:
        if not self.settings_store:
            return
        for source in self.settings_store.list_sources():
            if (
                'premium' in source.get('origins', [])
                and source.get('verification_status') != 'verified'
                and not source.get('administrator_disabled')
            ):
                await self._track_catalog_source(
                    {
                        'telegram_chat_id': source['telegram_id'],
                        'channel_username': source.get('username'),
                        'channel_name': source.get('title'),
                    }
                )

    async def tick(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            await self._priority()
            action = self.store.claim_action()
            if action:
                await self._action(action)
            run = self.store.claim_next_run()
            if run:
                await self._run(run)

    async def _priority(self, run_id: str | None = None) -> None:
        if self.live_queue is not None:
            while True:
                try:
                    await asyncio.wait_for(self.live_queue.join(), 0.2)
                    break
                except asyncio.TimeoutError:
                    if run_id:
                        self._check_cancel(run_id)
        if run_id:
            self._check_cancel(run_id)

    def _check_cancel(self, run_id: str) -> None:
        if self.store.get_run(run_id)['cancel_requested']:
            raise RunCancelled()

    async def _wait(self, run_id: str, seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            self._check_cancel(run_id)
            await asyncio.sleep(
                min(0.2, max(0, deadline - asyncio.get_running_loop().time()))
            )

    def _error(self, component: str) -> None:
        self.telemetry.record_error('premium_search', 'Premium: ' + component)

    def _recovered(self, component: str) -> None:
        for error in self.telemetry.attention_errors(days=90):
            if error['summary'] == 'Premium: ' + component:
                self.telemetry.resolve_error(error['id'])

    async def _telegram(self, run_id: str, request: Any) -> Any:
        for attempt in range(3):
            await self._priority(run_id)
            try:
                response = await asyncio.wait_for(
                    self.client(request, flood_sleep_threshold=0), timeout=45
                )
                self._recovered('telegram_unavailable')
                self._recovered('telegram_flood_wait')
                return response
            except errors.FloodWaitError:
                raise
            except (OSError, asyncio.TimeoutError, errors.ServerError):
                self._error('telegram_unavailable')
                if attempt == 2:
                    raise
                await self._wait(run_id, 2**attempt + random.uniform(0, 0.5))

    async def _run(self, run: dict) -> None:
        run_id = run['search_run_id']
        try:
            if run['track'] == 'catalog':
                from tg_vacancy_bot.premium_search.settings import (
                    catalog_search_allowed,
                )

                if not catalog_search_allowed(run['owner']):
                    self.store.update_run(
                        run_id,
                        status='failed',
                        error_reason='catalog_unavailable',
                        finished_at=now(),
                    )
                    return
            await self._priority(run_id)
            if run['mode'] == 'save_publish' and not self.processor.notify_vacancy:
                self.store.update_run(
                    run_id,
                    status='failed',
                    error_reason='publisher_not_configured',
                    finished_at=now(),
                )
                return
            if run['phase'] not in {'analyzing', 'llm_retry'}:
                self.store.update_run(run_id, phase='quota_check', retry_at=None)
                if run['track'] != 'catalog':
                    await self._check_query_quota(run_id, run['query'])
                self.store.update_run(run_id, phase='searching')
                await self._collect(run, analyze_pages=run['track'] == 'catalog')
                self._recovered('search_failed')
            self.store.update_run(run_id, phase='analyzing', retry_at=None)
            await self._analyze_pending(run)
            # Publish only after all candidates have been scored and ranked.
            for result in self.store.ranked_results(run_id):
                current = self.store.get_result(result['result_id'])
                if current['action_state'] == 'queued':
                    await self._priority(run_id)
                    claimed = self.store.claim_action(current['result_id'])
                    if claimed:
                        await self._action(claimed)
                    current = self.store.get_result(result['result_id'])
                if current['status'] == 'accepted' and run['mode'] != 'preview':
                    action = 'publish' if run['mode'] == 'save_publish' else 'save'
                    await self._priority(run_id)
                    try:
                        self.store.request_action(
                            current['result_id'], action, origin='run'
                        )
                    except ValueError:
                        self._check_cancel(run_id)
                        raise
                    claimed = self.store.claim_action(current['result_id'])
                    if claimed:
                        await self._action(claimed)
            self._check_cancel(run_id)
            completed = self.store.get_run(run_id)
            metrics = completed['metrics']
            if (
                run['track'] == 'catalog'
                and not completed['error_reason']
                and metrics['found'] > 0
                and metrics['llm_analyzed'] == 0
                and metrics['old'] + metrics['invalid'] + metrics['prefilter_rejected']
                == metrics['found']
            ):
                self.store.update_run(run_id, error_reason='no_recent_candidates')
            status = (
                'completed_with_errors'
                if self.store.get_run(run_id)['metrics']['errors']
                or self.store.get_run(run_id)['error_reason']
                else 'completed'
            )
            self.store.update_run(
                run_id, status=status, phase='finished', finished_at=now()
            )
            self.telemetry.record('premium_search_completed')
        except RunCancelled:
            self.store.update_run(
                run_id, status='cancelled', phase='finished', finished_at=now()
            )
        except QuotaExhausted:
            self._error('free_quota_exhausted')
            self.store.update_run(
                run_id,
                status='failed',
                error_reason='free_quota_exhausted',
                finished_at=now(),
            )
        except errors.FloodWaitError as error:
            self._error('telegram_flood_wait')
            if self.store.get_run(run_id)['cancel_requested']:
                self.store.update_run(run_id, status='cancelled', finished_at=now())
                return
            phase = self.store.get_run(run_id)['phase']
            self.store.defer(
                run_id,
                max(1, error.seconds),
                'analyzing' if phase in {'analyzing', 'llm_retry'} else 'flood_wait',
            )
        except Exception as error:
            self._error('search_failed')
            reason = {
                'PremiumAccountRequiredError': 'premium_required',
                'AuthKeyUnregisteredError': 'telegram_auth_required',
                'SessionRevokedError': 'telegram_auth_required',
                'UserDeactivatedError': 'telegram_auth_required',
            }.get(type(error).__name__, 'telegram_or_search_failure')
            self.store.update_run(
                run_id, status='failed', error_reason=reason, finished_at=now()
            )

    def _catalog_timed_out(self, run: dict) -> bool:
        return bool(
            run['track'] == 'catalog'
            and run.get('started_at')
            and datetime.now(timezone.utc)
            >= datetime.fromisoformat(run['started_at']) + CATALOG_TIME_LIMIT
        )

    async def _check_query_quota(self, run_id: str, query: str) -> None:
        quota = await self._telegram(
            run_id, functions.channels.CheckSearchPostsFloodRequest(query=query)
        )
        quota_data = dict(
            remains=quota.remains,
            total_daily=quota.total_daily,
            reset_at=(
                datetime.fromtimestamp(quota.wait_till, timezone.utc).isoformat()
                if quota.wait_till
                else None
            ),
        )
        self.store.update_run(run_id, quota_json=json.dumps(quota_data))
        if not quota.query_is_free and quota.remains <= 0:
            raise QuotaExhausted()
        self._recovered('free_quota_exhausted')

    async def _analyze_pending(self, run: dict) -> bool:
        """Score available posts and stop once the catalog result cap is met."""
        run_id = run['search_run_id']
        for result in self.store.pending_results(run_id):
            await self._priority(run_id)
            if run['track'] == 'catalog':
                if (
                    self.store.get_run(run_id)['metrics']['accepted']
                    >= run['result_limit']
                ):
                    return True
                if self._catalog_timed_out(run):
                    self.store.update_run(run_id, error_reason='time_limit_reached')
                    return True
            if result['status'] == 'pending':
                await self._evaluate(run, result)
                if (
                    run['track'] == 'catalog'
                    and self.store.get_run(run_id)['metrics']['accepted']
                    >= run['result_limit']
                ):
                    return True
        return False

    async def _collect(self, run: dict, *, analyze_pages: bool = False) -> None:
        run_id = run['search_run_id']
        catalog = run['track'] == 'catalog'
        limit = CATALOG_MAX_MESSAGES if catalog else DEFAULT_MAX_MESSAGES
        scanned = run.get('messages_scanned', 0)
        queries = run.get('search_queries') or [run['query']]
        states = [
            dict(
                query=query,
                rate=0,
                peer=types.InputPeerEmpty(),
                mid=0,
                checked=False,
                last_key=None,
            )
            for query in queries
        ]
        seen: set[tuple[int | None, int]] = set()
        while states and scanned < limit:
            for state in tuple(states):
                await self._priority(run_id)
                if scanned >= limit:
                    break
                if self._catalog_timed_out(run):
                    self.store.update_run(run_id, error_reason='time_limit_reached')
                    return
                if catalog and not state['checked']:
                    try:
                        await self._check_query_quota(run_id, state['query'])
                    except QuotaExhausted:
                        if not scanned:
                            raise
                        self._error('free_quota_exhausted')
                        self.store.update_run(
                            run_id, error_reason='free_quota_exhausted'
                        )
                        states.remove(state)
                        continue
                    state['checked'] = True
                page_limit = min(100, limit - scanned)
                response = await self._telegram(
                    run_id,
                    functions.channels.SearchPostsRequest(
                        query=state['query'],
                        offset_rate=state['rate'],
                        offset_peer=state['peer'],
                        offset_id=state['mid'],
                        limit=page_limit,
                        allow_paid_stars=None,
                    ),
                )
                channels = {c.id: c for c in getattr(response, 'chats', [])}
                messages = list(getattr(response, 'messages', []))[: limit - scanned]
                scanned += len(messages)
                fresh = []
                for message in messages:
                    key = (
                        getattr(getattr(message, 'peer_id', None), 'channel_id', None),
                        message.id,
                    )
                    if key not in seen:
                        seen.add(key)
                        fresh.append(
                            normalize_result(
                                message,
                                channels.get(key[0]),
                                run['period_days'],
                                run['track'],
                                run.get('profile_snapshot'),
                            )
                        )
                if fresh:
                    self.store.retain_search(run_id, fresh, phase='collecting')
                self.store.update_run(run_id, messages_scanned=scanned)
                if analyze_pages and await self._analyze_pending(run):
                    return
                next_rate = getattr(response, 'next_rate', None)
                if not messages or (len(messages) < page_limit and next_rate is None):
                    states.remove(state)
                    continue
                last = messages[-1]
                if next_rate is None and isinstance(
                    getattr(last, 'date', None), datetime
                ):
                    next_rate = int(last.date.timestamp())
                channel = channels.get(
                    getattr(getattr(last, 'peer_id', None), 'channel_id', None)
                )
                cursor_key = (getattr(channel, 'id', None), last.id, next_rate)
                if (
                    channel is None
                    or next_rate is None
                    or cursor_key == state['last_key']
                ):
                    states.remove(state)
                    continue
                try:
                    state['peer'] = utils.get_input_peer(channel)
                except TypeError:
                    states.remove(state)
                    continue
                state['rate'], state['mid'] = next_rate, last.id
                state['last_key'] = cursor_key

    async def _analyze(self, text: str, run: dict) -> PremiumAnalysis:
        if run['track'] == 'catalog':
            from tg_vacancy_bot.llm.universal import analyze_universal_text

            return adapt_universal(
                await analyze_universal_text(text),
                run['profile_snapshot'],
                source_text=text,
            )
        return await self.analyzer(text)

    async def _evaluate(self, run: dict, result: dict) -> None:
        rid = result['result_id']
        private_preview = (
            run['owner'].startswith('candidate:') and run['track'] != 'catalog'
        )
        group_store = (
            None
            if private_preview or not result['published_at']
            else self.processor.group_store
        )
        group = (
            group_store.known_duplicate(
                post_link=result['post_link'],
                text_hash=result['text_hash'],
                apply_urls=result['apply_urls'],
            )
            if group_store
            else None
        )
        duplicate = None if private_preview else self.store.duplicate(result)
        registry_duplicate = (
            self.processor.registry.get(result['vacancy_id'])
            if not private_preview and self.processor.registry is not None
            else None
        )
        exact = (
            not private_preview
            and self.processor.dedupe_state
            and self.processor.dedupe_state.is_duplicate(
                result['post_link'], result['text_hash'], result['vacancy_id']
            )
        )
        if group or duplicate or registry_duplicate or exact:
            group_id = (
                group['group_id']
                if group
                else duplicate.get('group_id') if duplicate else None
            )
            if group_id and group_store:
                group_store.record_duplicate_source(
                    group_id=group_id, **self._source(result)
                )
            elif exact:
                self.processor._skip_duplicate(
                    result['post_link'],
                    result['text_hash'],
                    result['vacancy_id'],
                    result['channel_name'],
                    (
                        datetime.fromisoformat(result['published_at'])
                        if result['published_at']
                        else None
                    ),
                )
            self.store.update_result(
                rid,
                status='duplicate',
                decision_reason='exact_duplicate',
                group_id=group_id,
            )
            if run['track'] == 'catalog' and registry_duplicate:
                await self._track_catalog_source(result)
            return
        decision = None
        from tg_vacancy_bot import config

        if run['track'] == 'catalog':
            cached = self.store.cached_universal(
                result['post_link'], result['raw_text']
            )
            if cached is not None:
                from tg_vacancy_bot.llm.universal import decision_from_dict

                decision = adapt_universal(
                    decision_from_dict(cached),
                    run['profile_snapshot'],
                    source_text=result['raw_text'],
                )
        attempts = 0 if decision is not None else 3
        for attempt in range(attempts):
            await self._priority(run['search_run_id'])
            if not self.store.consume_llm_call(
                run['search_run_id'], self.max_llm_calls
            ):
                self.store.update_result(
                    rid, status='review', decision_reason='llm_budget_exhausted'
                )
                return
            try:
                candidate = await asyncio.wait_for(
                    self._analyze(result['raw_text'], run), timeout=60
                )
                # Also validate injected analyzers and persisted shape consistently.
                decision = parse_premium_analysis(json.loads(candidate.as_json()))
                self._recovered('mistral_unavailable')
                self._recovered('mistral_schema_failure')
                break
            except (
                APIConnectionError,
                APITimeoutError,
                asyncio.TimeoutError,
                APIStatusError,
            ) as error:
                temporary = (
                    not isinstance(error, APIStatusError)
                    or error.status_code == 429
                    or error.status_code >= 500
                )
                self._error('mistral_unavailable')
                if not temporary or attempt + 1 >= attempts:
                    break
                delay = 2**attempt + random.uniform(0, 0.5)
                self.store.update_run(
                    run['search_run_id'],
                    phase='llm_retry',
                    retry_at=(
                        datetime.now(timezone.utc) + timedelta(seconds=delay)
                    ).isoformat(),
                )
                await self._wait(run['search_run_id'], delay)
                self.store.update_run(
                    run['search_run_id'], phase='analyzing', retry_at=None
                )
            except Exception:
                self._error('mistral_schema_failure')
                break
        if decision is None:
            self.store.update_result(
                rid,
                status='review' if not config.LEGACY_GO_ANALYSIS else 'error',
                decision_reason=(
                    'llm_unavailable' if not config.LEGACY_GO_ANALYSIS else 'llm_failed'
                ),
            )
            return
        self._check_cancel(run['search_run_id'])
        status = decision.status(run['track'])
        group_id = None
        if status in {'accepted', 'review'} and group_store:
            group = group_store.preview_publication(
                vacancy_id=result['vacancy_id'],
                data=decision.analysis,
                published_at=(
                    datetime.fromisoformat(result['published_at'])
                    if result['published_at']
                    else None
                ),
                fuzzy=True,
            )
            if not group.is_canonical:
                group_store.register_publication(
                    data=decision.analysis, fuzzy=True, **self._source(result)
                )
                group_id, status = group.group_id, 'duplicate'
        if status in {'accepted', 'review'} and not private_preview:
            previous = self._structured_duplicate(result, decision)
            if previous:
                status, group_id = 'duplicate', previous['group_id']
                if group_id and group_store:
                    group_store.record_duplicate_source(
                        group_id=group_id, **self._source(result)
                    )
        self.store.update_result(
            rid,
            status=status,
            decision_status=decision.status(run['track']),
            decision_reason=(
                'structured_duplicate' if status == 'duplicate' else 'llm_' + status
            ),
            analysis_json=decision.as_json(),
            confidence=decision.confidence,
            title=decision.analysis.title,
            company=decision.analysis.company,
            summary=decision.analysis.summary,
            group_id=group_id,
        )
        if run['track'] == 'catalog' and status == 'accepted':
            await self._save(self.store.get_result(rid), publish=False)
            await self._track_catalog_source(self.store.get_result(rid))

    async def _track_catalog_source(self, result: dict) -> None:
        """Join a public source from the bot-owned session and enable monitoring."""
        if not self.settings_store or not result.get('channel_username'):
            return
        username = result['channel_username']
        if not _USERNAME.fullmatch(username):
            return
        existing = next(
            (
                item
                for item in self.settings_store.list_sources()
                if item.get('telegram_id') == str(result.get('telegram_chat_id'))
                or (item.get('username') or '').casefold() == username.casefold()
            ),
            None,
        )
        if existing and existing.get('administrator_disabled'):
            return
        if (
            existing
            and existing.get('enabled')
            and existing.get('verification_status') == 'verified'
        ):
            return
        source = self.settings_store.upsert_source(
            int(result['telegram_chat_id']),
            username=username,
            title=result['channel_name'],
            chat_type='channel',
            origin='premium',
            verification_status='unverified',
            enabled=False,
        )
        if source.get('administrator_disabled'):
            return
        try:
            for attempt in range(3):
                try:
                    entity = await self.client.get_entity(username)
                    try:
                        await self.client(
                            functions.channels.JoinChannelRequest(channel=entity)
                        )
                    except errors.UserAlreadyParticipantError:
                        pass
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
            source = self.settings_store.mark_source_verified(
                source['id'],
                telegram_id=utils.get_peer_id(entity),
                username=username,
                title=result['channel_name'],
                chat_type='channel',
            )
            if source.get('administrator_disabled'):
                return
            from tg_vacancy_bot.admin.control import request_action

            request_action('restart', str(self.settings_store.directory.parent))
            self.telemetry.record('premium_source_auto_tracked')
        except Exception:
            # The accepted vacancy is already in the shared registry. Leave the
            # source untracked so an operator can retry after Telegram access is fixed.
            self.telemetry.record_error(
                'premium_source_join', 'Premium: source_join_failed'
            )

    def _structured_duplicate(self, result: dict, decision) -> dict | None:
        if not result['published_at']:
            return None
        keys = VacancyGroupStore._keys(decision.analysis)
        fuzzy = {}
        for previous in self.store.analyzed_results(result['result_id']):
            if not previous['published_at']:
                continue
            if (
                abs(
                    (
                        datetime.fromisoformat(previous['published_at'])
                        - datetime.fromisoformat(result['published_at'])
                    ).total_seconds()
                )
                > 14 * 86400
            ):
                continue
            previous_keys = VacancyGroupStore._keys(
                parse_premium_analysis(previous['analysis']).analysis
            )
            if VacancyGroupStore._merge_reason(keys, previous_keys):
                return previous
            if VacancyGroupStore._fuzzy_match(keys, previous_keys):
                fuzzy[previous['group_id'] or previous['result_id']] = previous
        return next(iter(fuzzy.values())) if len(fuzzy) == 1 else None

    @staticmethod
    def _source(result: dict) -> dict:
        return dict(
            vacancy_id=result['vacancy_id'],
            post_link=result['post_link'],
            channel_name=result['channel_name'],
            published_at=(
                datetime.fromisoformat(result['published_at'])
                if result['published_at']
                else None
            ),
            text_hash=result['text_hash'],
        )

    async def _action(self, result: dict) -> None:
        rid, action = result['result_id'], result['requested_action']
        try:
            if action in {'reject', 'mark_duplicate'}:
                self.store.update_result(
                    rid,
                    status='rejected' if action == 'reject' else 'duplicate',
                    decision_reason='administrator_' + action,
                    action_state='idle',
                )
            elif action == 'add_public_source':
                if not self.settings_store or not _USERNAME.fullmatch(
                    result['channel_username'] or ''
                ):
                    raise ValueError('invalid_public_source')
                self.settings_store.upsert_source(
                    int(result['telegram_chat_id']),
                    username=result['channel_username'],
                    title=result['channel_name'],
                    chat_type='channel',
                    origin='admin',
                    verification_status='verified',
                    verified_at=now(),
                )
                self.telemetry.record(
                    'premium_public_source_added', restart_required=True
                )
                self.store.update_result(
                    rid,
                    action_state='idle',
                    decision_reason='source_added_restart_required',
                )
            else:
                if not result['analysis']:
                    result = await self._manual_analysis(result)
                await self._save(result, publish=action == 'publish')
        except ValueError as error:
            self._error('result_action_failed')
            self.store.update_result(
                rid,
                action_state='failed',
                action_error=(
                    str(error)
                    if str(error)
                    in {
                        'manual_analysis_failed',
                        'publisher_not_configured',
                        'save_failed',
                        'delivery_uncertain',
                    }
                    else 'action_failed'
                ),
            )
        except Exception:
            self._error('result_action_failed')
            self.store.update_result(
                rid, action_state='failed', action_error='action_failed'
            )
        run = self.store.get_run(result['search_run_id'])
        if run['status'] in {'completed', 'completed_with_errors'}:
            self.store.update_run(
                run['search_run_id'],
                status=(
                    'completed_with_errors' if run['metrics']['errors'] else 'completed'
                ),
            )

    async def _manual_analysis(self, result: dict) -> dict:
        """Create the structured card needed to publish an admin-approved post."""
        try:
            candidate = await asyncio.wait_for(
                self._analyze(
                    result['raw_text'], self.store.get_run(result['search_run_id'])
                ),
                60,
            )
            decision = parse_premium_analysis(json.loads(candidate.as_json()))
        except Exception as error:
            self._error('manual_analysis_failed')
            raise ValueError('manual_analysis_failed') from error
        status = decision.status(self.store.get_run(result['search_run_id'])['track'])
        self.store.update_result(
            result['result_id'],
            status=status,
            decision_status=status,
            decision_reason='manual_override',
            analysis_json=decision.as_json(),
            confidence=decision.confidence,
            title=decision.analysis.title,
            company=decision.analysis.company,
            summary=decision.analysis.summary,
        )
        return self.store.get_result(result['result_id'])

    async def _save(self, result: dict, publish: bool) -> None:
        rid = result['result_id']
        run = self.store.get_run(result['search_run_id'])
        if run['owner'].startswith('candidate:') and run['track'] != 'catalog':
            raise ValueError('private_preview')
        decision = parse_premium_analysis(result['analysis'])
        from tg_vacancy_bot.llm.universal import decision_from_dict

        universal = (
            decision_from_dict(decision.universal_decision)
            if decision.universal_decision
            else None
        )
        if run['track'] == 'catalog':
            if universal is None or self.processor.registry is None:
                raise ValueError('save_failed')
            saved = await self.processor.persist_registry_only(
                raw_text=result['raw_text'],
                post_link=result['post_link'],
                published_at=(
                    datetime.fromisoformat(result['published_at'])
                    if result['published_at']
                    else None
                ),
                channel_name=result['channel_name'],
                universal_decision=universal,
                vacancy_id=result['vacancy_id'],
            )
            if not saved.saved:
                raise ValueError('save_failed')
            self.store.update_result(
                rid,
                status='saved',
                vacancy_id=saved.vacancy_id,
                action_state='idle',
                action_error=None,
            )
            return
        from tg_vacancy_bot.llm.universal import go_projection_accepted

        if universal is not None and not go_projection_accepted(universal):
            publish = False
        if publish and not self.processor.notify_vacancy:
            raise ValueError('publisher_not_configured')
        analysis = decision.analysis
        # An explicit manual publish action is an operator approval.  Keep the
        # extracted fields, but allow the shared persistence path to save and
        # deliver a card even when the classifier marked it as non-matching.
        if publish and not analysis.is_match:
            analysis = replace(analysis, is_match=True)
        saved = await self.processor.persist_analyzed_message(
            raw_text=result['raw_text'],
            post_link=result['post_link'],
            published_at=(
                datetime.fromisoformat(result['published_at'])
                if result['published_at']
                else None
            ),
            channel_name=result['channel_name'],
            analysis_result=analysis,
            **(
                {'universal_decision': universal, 'source_type': 'premium'}
                if universal
                else {}
            ),
            publish=False,
            strict_delivery=True,
        )
        if saved.outcome == 'duplicate':
            self.store.update_result(
                rid, status='duplicate', group_id=saved.group_id, action_state='idle'
            )
            return
        if not saved.saved:
            raise ValueError('save_failed')
        self.store.update_result(
            rid, status='saved', vacancy_id=saved.vacancy_id, group_id=saved.group_id
        )
        if publish:
            if result['delivery_state'] == 'sending':
                raise ValueError('delivery_uncertain')
            self.store.update_result(rid, delivery_state='sending')
            saved = await self.processor.persist_analyzed_message(
                raw_text=result['raw_text'],
                post_link=result['post_link'],
                published_at=(
                    datetime.fromisoformat(result['published_at'])
                    if result['published_at']
                    else None
                ),
                channel_name=result['channel_name'],
                analysis_result=analysis,
                **(
                    {'universal_decision': universal, 'source_type': 'premium'}
                    if universal
                    else {}
                ),
                publish=True,
                strict_delivery=True,
            )
            if saved.outcome != 'published':
                self.store.update_result(
                    rid, action_state='failed', action_error='delivery_uncertain'
                )
                return
            self.store.update_result(
                rid, status='published', delivery_state='published'
            )
        self._recovered('result_action_failed')
        self.store.update_result(rid, action_state='idle', action_error=None)
