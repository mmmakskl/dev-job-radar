"""Single-concurrency public post search on the live process's Telethon client."""

from __future__ import annotations

import asyncio
import json
import random
import re
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
    analyze_premium_text,
    parse_premium_analysis,
)
from tg_vacancy_bot.premium_search.store import PremiumSearchStore, now
from tg_vacancy_bot.telegram.links import build_vacancy_id
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore

_USERNAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{3,31}\Z')
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
    message: Any, channel: Any, period_days: int, track: str = 'go'
) -> dict:
    """Keep rejected candidates in the audit while exposing only verified sources."""
    text = str(getattr(message, 'message', '') or '').strip()
    values = dict(
        raw_text=text,
        text_hash=build_text_hash(text),
        status='pending',
        decision_reason='pending',
        apply_urls_json='[]',
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
    if not isinstance(date, datetime):
        return dict(values, status='rejected', decision_reason='invalid_date')
    if not text:
        return dict(values, status='rejected', decision_reason='empty_text')
    if date < datetime.now(timezone.utc) - timedelta(days=period_days):
        return dict(values, status='rejected', decision_reason='old_content')
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
        while not shutdown.is_set():
            if asyncio.get_running_loop().time() >= cleanup_at:
                self.store.cleanup()
                cleanup_at = asyncio.get_running_loop().time() + 86400
            await self.tick()
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

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
                quota = await self._telegram(
                    run_id,
                    functions.channels.CheckSearchPostsFloodRequest(query=run['query']),
                )
                quota_data = dict(
                    remains=quota.remains,
                    total_daily=quota.total_daily,
                    reset_at=(
                        datetime.fromtimestamp(
                            quota.wait_till, timezone.utc
                        ).isoformat()
                        if quota.wait_till
                        else None
                    ),
                )
                self.store.update_run(run_id, quota_json=json.dumps(quota_data))
                if not quota.query_is_free and quota.remains <= 0:
                    raise QuotaExhausted()
                self._recovered('free_quota_exhausted')
                self.store.update_run(run_id, phase='searching')
                await self._collect(run)
                self._recovered('search_failed')
            self.store.update_run(run_id, phase='analyzing', retry_at=None)
            for result in self.store.pending_results(run_id):
                await self._priority(run_id)
                if result['status'] == 'pending':
                    await self._evaluate(run, result)
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
            status = (
                'completed_with_errors'
                if self.store.get_run(run_id)['metrics']['errors']
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

    async def _collect(self, run: dict) -> None:
        offset_rate, offset_id = 0, 0
        offset_peer = types.InputPeerEmpty()
        seen = set()
        for _ in range(10):
            response = await self._telegram(
                run['search_run_id'],
                functions.channels.SearchPostsRequest(
                    query=run['query'],
                    offset_rate=offset_rate,
                    offset_peer=offset_peer,
                    offset_id=offset_id,
                    limit=100,
                    allow_paid_stars=None,
                ),
            )
            channels = {c.id: c for c in getattr(response, 'chats', [])}
            messages = list(getattr(response, 'messages', []))
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
                        )
                    )
            if not fresh:
                break
            self.store.retain_search(run['search_run_id'], fresh, phase='collecting')
            next_rate = getattr(response, 'next_rate', None)
            if not messages or next_rate is None:
                break
            last = messages[-1]
            channel = channels.get(getattr(last.peer_id, 'channel_id', None))
            if channel is None:
                break
            offset_peer = utils.get_input_peer(channel)
            offset_rate, offset_id = next_rate, last.id

    async def _evaluate(self, run: dict, result: dict) -> None:
        rid = result['result_id']
        group_store = self.processor.group_store
        group = (
            group_store.known_duplicate(
                post_link=result['post_link'],
                text_hash=result['text_hash'],
                apply_urls=result['apply_urls'],
            )
            if group_store
            else None
        )
        duplicate = self.store.duplicate(result)
        exact = (
            self.processor.dedupe_state
            and self.processor.dedupe_state.is_duplicate(
                result['post_link'], result['text_hash'], result['vacancy_id']
            )
        )
        if group or duplicate or exact:
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
                    datetime.fromisoformat(result['published_at']),
                )
            self.store.update_result(
                rid,
                status='duplicate',
                decision_reason='exact_duplicate',
                group_id=group_id,
            )
            return
        decision = None
        for attempt in range(3):
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
                    self.analyzer(result['raw_text']), timeout=60
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
                if not temporary or attempt == 2:
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
            self.store.update_result(rid, status='error', decision_reason='llm_failed')
            return
        self._check_cancel(run['search_run_id'])
        status = decision.status(run['track'])
        group_id = None
        if status in {'accepted', 'review'} and group_store:
            group = group_store.preview_publication(
                vacancy_id=result['vacancy_id'],
                data=decision.analysis,
                published_at=datetime.fromisoformat(result['published_at']),
                fuzzy=True,
            )
            if not group.is_canonical:
                group_store.register_publication(
                    data=decision.analysis, fuzzy=True, **self._source(result)
                )
                group_id, status = group.group_id, 'duplicate'
        if status in {'accepted', 'review'}:
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

    def _structured_duplicate(self, result: dict, decision) -> dict | None:
        keys = VacancyGroupStore._keys(decision.analysis)
        fuzzy = {}
        for previous in self.store.analyzed_results(result['result_id']):
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
            published_at=datetime.fromisoformat(result['published_at']),
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
            candidate = await asyncio.wait_for(self.analyzer(result['raw_text']), 60)
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
        if publish and not self.processor.notify_vacancy:
            raise ValueError('publisher_not_configured')
        decision = parse_premium_analysis(result['analysis'])
        saved = await self.processor.persist_analyzed_message(
            raw_text=result['raw_text'],
            post_link=result['post_link'],
            published_at=datetime.fromisoformat(result['published_at']),
            channel_name=result['channel_name'],
            analysis_result=decision.analysis,
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
                published_at=datetime.fromisoformat(result['published_at']),
                channel_name=result['channel_name'],
                analysis_result=decision.analysis,
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
