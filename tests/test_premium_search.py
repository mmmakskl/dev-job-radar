"""Offline contracts for Premium search: no network clients are created here."""

import asyncio
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import errors, functions, types

from tests.test_llm_schemas import valid_payload
from tg_vacancy_bot.admin.settings import SettingsStore
from tg_vacancy_bot.admin.telemetry import TelemetryStore
from tg_vacancy_bot.llm.schemas import EXPECTED_FIELDS, InvalidAnalysisResultError
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.premium_search.analyzer import parse_premium_analysis
from tg_vacancy_bot.premium_search.service import (
    PremiumSearchService,
    extract_apply_urls,
    normalize_result,
)
from tg_vacancy_bot.premium_search.store import ActiveRunError, PremiumSearchStore
from tg_vacancy_bot.premium_search.tracks import normalize_query, prefilter_reason
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
from tg_vacancy_bot.telegram.candidate_notifier import CandidateVacancyNotifier
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


def decision_payload(**changes):
    analysis = {key: None for key in EXPECTED_FIELDS}
    analysis.update(valid_payload())
    for key in (
        'required_stack',
        'preferred_stack',
        'primary_roles',
        'specializations',
    ):
        analysis[key] = analysis.get(key) or []
    return dict(
        is_vacancy=True,
        is_track_match=True,
        go_role_strength='primary',
        confidence=90,
        needs_review=False,
        decision_reason='go_primary',
        language='ru',
        analysis=analysis,
        **changes,
    )


def decision():
    return parse_premium_analysis(decision_payload())


def channel(channel_id=123, username='public_jobs', **kwargs):
    return types.Channel(
        id=channel_id,
        title='Public jobs',
        photo=types.ChatPhotoEmpty(),
        date=datetime.now(timezone.utc),
        broadcast=True,
        username=username,
        **kwargs,
    )


def message(mid=1, text='Hiring Golang backend developer', **kwargs):
    return SimpleNamespace(
        id=mid,
        message=text,
        date=kwargs.pop('date', datetime.now(timezone.utc)),
        peer_id=types.PeerChannel(123),
        entities=kwargs.pop('entities', []),
        **kwargs,
    )


class FakeTelegram:
    def __init__(self, messages=None, quota=None):
        self.calls = []
        self.quota = quota or types.SearchPostsFlood(10, 5, 20)
        self.response = SimpleNamespace(messages=messages or [], chats=[channel()])

    async def __call__(self, request, **kwargs):
        self.calls.append(request)
        assert kwargs == {'flood_sleep_threshold': 0}
        if isinstance(request, functions.channels.CheckSearchPostsFloodRequest):
            return self.quota
        assert isinstance(request, functions.channels.SearchPostsRequest)
        assert request.allow_paid_stars is None
        return self.response


def setup(tmp_path, messages=None, analyzer=None, **kwargs):
    store = PremiumSearchStore(str(tmp_path / 'premium.sqlite3'))
    sheet = AsyncMock(return_value=True)
    processor = VacancyProcessor(
        lambda _: True,
        AsyncMock(),
        sheet,
        dedupe_state=JsonlDedupeState(str(tmp_path / 'state.jsonl'), 30),
        group_store=VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), 14),
    )
    client = FakeTelegram(messages)
    analyzer = analyzer or AsyncMock(return_value=decision())
    service = PremiumSearchService(
        client,
        store,
        processor,
        TelemetryStore(str(tmp_path)),
        analyzer=analyzer,
        **kwargs,
    )
    return service, store, processor, client, analyzer, sheet


@pytest.mark.parametrize(
    ('text', 'accepted'),
    [
        ('Hiring Golang engineer', True),
        ('#вакансия Ищем Go-разработчика', True),
        ('We are hiring backend engineers. Language: Go and SQL', True),
        ('Ищем разработчика на Go', True),
        ('Hiring Java developer. Go optional', False),
        ('Golang engineer open to work', False),
        ('Ищу работу Golang разработчиком', False),
        ('Вакансия Golang: подпишитесь на наш канал', False),
        ('Golang webinar hiring tips', False),
        ('Hiring sales: go to the office', False),
        ('Golang developer', False),
        ('Hiring engineers ' + '.' * 160 + ' go', False),
        ('', False),
    ],
)
def test_prefilter(text, accepted):
    assert (prefilter_reason(text, 'go') is None) is accepted


def test_query_uniqueness_and_constraints(tmp_path):
    store = PremiumSearchStore(str(tmp_path / 'premium.sqlite3'))
    run = store.create_run(query='  Go   Jobs  ')
    assert run['query'] == 'Go Jobs'
    with pytest.raises(ActiveRunError) as caught:
        store.create_run(query='go jobs', mode='save', period_days=20)
    assert caught.value.run_id == run['search_run_id']
    store.cancel(run['search_run_id'])
    assert store.create_run(query='GO JOBS')['status'] == 'queued'
    with store._connect() as conn:
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE premium_search_runs SET status='unknown'")
    with pytest.raises(ValueError):
        normalize_query('a')
    with pytest.raises(ValueError):
        store.create_run(query='Go jobs', result_limit=101)


@pytest.mark.parametrize(
    ('field', 'value'),
    [('confidence', 89), ('needs_review', True), ('go_role_strength', 'significant')],
)
def test_decision_threshold(field, value):
    parsed = replace(decision(), **{field: value})
    assert parsed.status() == ('accepted' if field == 'go_role_strength' else 'review')


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('go_role_strength', 'optional'),
        ('go_role_strength', 'absent'),
        ('is_vacancy', False),
        ('is_track_match', False),
    ],
)
def test_decision_negative_always_rejected(field, value):
    assert (
        replace(decision(), needs_review=True, **{field: value}).status() == 'rejected'
    )
    assert (
        replace(
            decision(), analysis=replace(decision().analysis, is_match=False)
        ).status()
        == 'rejected'
    )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('confidence', True),
        ('confidence', 101),
        ('confidence', 90.5),
        ('needs_review', 'false'),
        ('go_role_strength', []),
        ('language', None),
        ('extra', True),
    ],
)
def test_strict_schema_no_payload_logs(field, value, caplog):
    payload = decision_payload()
    payload[field] = value
    with pytest.raises(InvalidAnalysisResultError):
        parse_premium_analysis(payload)
    assert not caplog.text


def test_strict_nested_schema(caplog):
    payload = decision_payload()
    payload['analysis']['salary_from'] = 'SECRET_POST'
    with pytest.raises(InvalidAnalysisResultError):
        parse_premium_analysis(payload)
    assert 'SECRET_POST' not in caplog.text
    del payload['analysis']['salary_from']
    with pytest.raises(InvalidAnalysisResultError):
        parse_premium_analysis(payload)


def test_dates_sources_and_entities():
    assert normalize_result(message(date=datetime.now()), channel(), 7)[
        'published_at'
    ].endswith('+00:00')
    assert (
        normalize_result(message(date=None), channel(), 7)['decision_reason']
        == 'invalid_date'
    )
    assert (
        normalize_result(
            message(date=datetime.now(timezone.utc) - timedelta(days=8)), channel(), 7
        )['decision_reason']
        == 'old_content'
    )
    assert (
        normalize_result(message(text=''), channel(), 7)['decision_reason']
        == 'empty_text'
    )
    for bad in [
        None,
        channel(username=None),
        channel(username='bad/name'),
        channel(megagroup=True),
        channel(restricted=True),
    ]:
        row = normalize_result(message(), bad, 7)
        assert row['decision_reason'] == 'invalid_public_source'
        assert 'channel_username' not in row
    urls = extract_apply_urls(
        'Apply https://example.com/jobs/42/?utm_source=tg&b=2&a=1. https://t.me/jobs/42'
    )
    assert urls == ['https://example.com/jobs/42?a=1&b=2']
    assert extract_apply_urls('Visit https://example.com') == []
    assert extract_apply_urls(
        '🚀 Apply now',
        [types.MessageEntityTextUrl(3, 9, 'https://example.com/positions/42')],
    ) == ['https://example.com/positions/42']


def test_preview_search_correct_api_and_redaction(tmp_path):
    svc, store, _, client, analyzer, sheets = setup(tmp_path, [message()])
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert [type(c) for c in client.calls] == [
        functions.channels.CheckSearchPostsFloodRequest,
        functions.channels.SearchPostsRequest,
    ]
    assert client.calls[1].query == 'Golang'
    assert client.calls[1].limit == 50
    assert analyzer.await_count == 1
    assert sheets.await_count == 0
    result = store.list_results(run['search_run_id'])['items'][0]
    assert result['status'] == 'accepted'
    assert not {
        'raw_text',
        'telegram_chat_id',
        'telegram_message_id',
        'analysis',
        'vacancy_id',
    } & set(result)
    metrics = store.get_run(run['search_run_id'])['metrics']
    assert metrics['accepted'] == metrics['found'] == metrics['llm_analyzed'] == 1


def test_quota_never_spends_stars(tmp_path):
    svc, store, _, client, analyzer, _ = setup(tmp_path, [message()])
    client.quota = types.SearchPostsFlood(10, 0, 20, wait_till=2000000000)
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert len(client.calls) == 1
    assert not analyzer.called
    result = store.get_run(run['search_run_id'])
    assert result['status'] == 'failed'
    assert result['error_reason'] == 'free_quota_exhausted'
    assert result['quota']['reset_at']
    client.quota.query_is_free = True
    store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert analyzer.called
    assert svc.telemetry.attention_errors() == []


def test_budget_counts_retries_and_review_retained(tmp_path):
    analyzer = AsyncMock(side_effect=asyncio.TimeoutError())
    svc, store, _, _, _, _ = setup(
        tmp_path,
        [message(), message(2, 'Hiring Go backend engineer extra')],
        analyzer=analyzer,
        max_llm_calls=2,
    )
    svc._wait = AsyncMock()
    run = store.create_run(query='Golang', include_review=False)
    asyncio.run(svc.tick())
    assert analyzer.await_count == 2
    assert store.get_run(run['search_run_id'])['metrics']['llm_analyzed'] == 2
    assert store.list_results(run['search_run_id'])['total'] == 0
    assert store.list_results(run['search_run_id'], status='review')['total'] == 2
    assert svc._wait.await_args_list[0].args[1] >= 1


def test_schema_failure_not_retried(tmp_path):
    svc, store, _, _, analyzer, _ = setup(
        tmp_path,
        [message()],
        analyzer=AsyncMock(side_effect=InvalidAnalysisResultError('safe')),
    )
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert analyzer.await_count == 1
    assert store.get_run(run['search_run_id'])['status'] == 'completed_with_errors'


def test_flood_wait_is_persisted_and_cancellable(tmp_path):
    svc, store, _, _, _, _ = setup(tmp_path)
    svc.client = AsyncMock(side_effect=errors.FloodWaitError(None, capture=60))
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    current = store.get_run(run['search_run_id'])
    assert current['status'] == 'queued' and current['retry_at']
    assert current['metrics']['rate_limit_waits'] == 1
    asyncio.run(svc.tick())
    assert svc.client.await_count == 1
    store.cancel(run['search_run_id'])
    assert store.get_run(run['search_run_id'])['status'] == 'cancelled'


def test_live_queue_has_priority(tmp_path):
    async def scenario():
        queue = asyncio.Queue()
        queue.put_nowait('live')
        svc, store, _, client, _, _ = setup(tmp_path, [message()], live_queue=queue)
        store.create_run(query='Golang')
        task = asyncio.create_task(svc.tick())
        await asyncio.sleep(0.01)
        assert not client.calls
        queue.get_nowait()
        queue.task_done()
        await task
        assert len(client.calls) == 2

    asyncio.run(scenario())


def test_cancellation_during_analysis(tmp_path):
    svc, store, _, _, _, sheets = setup(tmp_path, [message()])
    run = store.create_run(query='Golang', mode='save')

    async def analyze(_):
        store.cancel(run['search_run_id'])
        return decision()

    svc.analyzer = analyze
    asyncio.run(svc.tick())
    assert store.get_run(run['search_run_id'])['status'] == 'cancelled'
    assert sheets.await_count == 0


@pytest.mark.parametrize('key', ['link', 'pair', 'hash', 'url'])
def test_pre_llm_dedupe(tmp_path, key):
    svc, store, _, _, analyzer, _ = setup(tmp_path)
    first = store.create_run(query='Golang')
    original = normalize_result(
        message(text='Hiring Golang apply https://example.com/jobs/42'), channel(), 7
    )
    rid = store.add_result(first['search_run_id'], **original)
    store.update_result(rid, status='accepted')
    other = dict(
        original,
        post_link='https://t.me/other_jobs/2',
        telegram_message_id=2,
        text_hash='different',
        apply_urls_json='[]',
    )
    if key == 'link':
        other['post_link'] = original['post_link']
    if key == 'pair':
        other['telegram_message_id'] = original['telegram_message_id']
    if key == 'hash':
        other['text_hash'] = original['text_hash']
    if key == 'url':
        other['apply_urls_json'] = original['apply_urls_json']
    rid2 = (
        store.add_result(first['search_run_id'], **other)
        if key != 'link'
        else store.add_result(store.create_run(query='other')['search_run_id'], **other)
    )
    asyncio.run(svc._evaluate(first, store.get_result(rid2)))
    assert store.get_result(rid2)['status'] == 'duplicate'
    assert analyzer.await_count == 0


def test_fuzzy_grouping_and_ambiguous_matches(tmp_path):
    groups = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), 14)
    tokens = ' '.join('skill' + str(i) for i in range(30))
    data = replace(
        decision().analysis,
        company='Exact company',
        title='Senior Golang developer',
        contact=None,
        apply_link=None,
        responsibilities=tokens,
        requirements=None,
    )
    date = datetime.now(timezone.utc)
    groups.register_publication(
        vacancy_id='first',
        post_link='https://t.me/jobs/1',
        channel_name='jobs',
        data=data,
        published_at=date,
        text_hash='a',
    )
    other = replace(data, title='Senior Golang developers')
    assert not groups.preview_publication(
        vacancy_id='other', data=other, published_at=date, fuzzy=True
    ).is_canonical
    assert groups.preview_publication(
        vacancy_id='other', data=other, published_at=date
    ).is_canonical
    assert groups.preview_publication(
        vacancy_id='other',
        data=replace(other, company='Other company'),
        published_at=date,
        fuzzy=True,
    ).is_canonical
    assert groups.preview_publication(
        vacancy_id='other',
        data=replace(other, responsibilities='short'),
        published_at=date,
        fuzzy=True,
    ).is_canonical
    groups.register_publication(
        vacancy_id='second',
        post_link='https://t.me/jobs/2',
        channel_name='jobs',
        data=replace(data, title='Senior Golang developer!'),
        published_at=date,
        text_hash='b',
    )
    # Punctuation normalizes to the same exact title, so create a distinct title.
    groups.register_publication(
        vacancy_id='third',
        post_link='https://t.me/jobs/3',
        channel_name='jobs',
        data=replace(data, title='Senior Golang developer II'),
        published_at=date,
        text_hash='c',
    )
    assert groups.preview_publication(
        vacancy_id='other', data=other, published_at=date, fuzzy=True
    ).is_canonical


def test_save_publish_idempotency_and_recovery(tmp_path):
    svc, store, processor, _, _, sheets = setup(tmp_path, [message()])
    candidate_store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    bot = SimpleNamespace(send_message=AsyncMock(return_value={'message_id': 9}))
    processor.notify_vacancy = CandidateVacancyNotifier(bot, candidate_store, '@cards')
    run = store.create_run(query='Golang', mode='save_publish')
    asyncio.run(svc.tick())
    result = store.list_results(run['search_run_id'])['items'][0]
    assert result['status'] == 'published'
    assert sheets.await_count == bot.send_message.await_count == 1
    store.request_action(result['result_id'], 'publish')
    asyncio.run(svc.tick())
    assert sheets.await_count == bot.send_message.await_count == 1
    assert store.get_run(run['search_run_id'])['metrics']['published'] == 1
    store.update_result(
        result['result_id'], action_state='running', delivery_state='sending'
    )
    store.recover()
    assert store.get_result(result['result_id'])['action_error'] == 'delivery_uncertain'
    assert store.claim_action() is None
    with pytest.raises(ValueError):
        store.request_action(result['result_id'], 'publish')


def test_uncertain_bot_delivery_never_retried(tmp_path):
    svc, store, processor, _, _, sheets = setup(tmp_path, [message()])
    candidate_store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
    processor.notify_vacancy = CandidateVacancyNotifier(bot, candidate_store, '@cards')
    run = store.create_run(query='Golang', mode='save_publish')
    asyncio.run(svc.tick())
    result = store.list_results(run['search_run_id'])['items'][0]
    assert result['status'] == 'saved'
    assert result['action_error'] == 'delivery_uncertain'
    assert result['delivery_state'] == 'sending'
    assert sheets.await_count == bot.send_message.await_count == 1


def test_interrupted_search_and_analysis_recovery(tmp_path):
    svc, store, _, client, analyzer, _ = setup(tmp_path)
    run = store.create_run(query='Golang')
    store.claim_next_run()
    store.update_run(run['search_run_id'], phase='searching')
    store.recover()
    assert store.get_run(run['search_run_id'])['status'] == 'failed'
    run = store.create_run(query='Golang')
    store.claim_next_run()
    store.retain_search(
        run['search_run_id'], [normalize_result(message(), channel(), 7)]
    )
    store.consume_llm_call(run['search_run_id'], 20)
    store.recover()
    asyncio.run(svc.tick())
    assert not client.calls
    assert analyzer.await_count == 1
    assert store.get_run(run['search_run_id'])['metrics']['llm_analyzed'] == 2


def test_preview_duplicate_preserves_source_and_registry_action(tmp_path):
    svc, store, processor, client, _, sheets = setup(tmp_path, [message()])
    first = store.create_run(query='Golang', mode='save')
    asyncio.run(svc.tick())
    client.response.messages = [message(2)]
    second = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    result = store.list_results(second['search_run_id'])['items'][0]
    assert result['status'] == 'duplicate'
    assert sheets.await_count == 1
    group = processor.group_store.get_group(result['group_id'])
    assert len(group['publications']) == 2
    svc.settings_store = SettingsStore(str(tmp_path))
    store.request_action(result['result_id'], 'add_public_source')
    asyncio.run(svc.tick())
    store.request_action(result['result_id'], 'add_public_source')
    asyncio.run(svc.tick())
    sources = svc.settings_store.list_sources()
    assert sum(s['username'] == 'public_jobs' for s in sources) == 1
    assert store.get_run(first['search_run_id'])['status'] == 'completed'


def test_retention_does_not_delete_saved_data(tmp_path):
    svc, store, processor, _, _, _ = setup(tmp_path, [message()])
    run = store.create_run(query='Golang', mode='save')
    asyncio.run(svc.tick())
    cutoff = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with store._connect() as conn:
        conn.execute('UPDATE premium_search_results SET found_at=?', (cutoff,))
    store.cleanup()
    result_id = store.list_results(run['search_run_id'])['items'][0]['result_id']
    assert store.get_result(result_id)['raw_text'] is None
    store.update_run(run['search_run_id'], finished_at=cutoff)
    store.cleanup()
    assert store.list_runs() == []
    assert len(processor.group_store.list_groups()) == 1


def test_persistence_serialized(tmp_path):
    svc, _, processor, _, _, sheets = setup(tmp_path)

    async def scenario():
        kwargs = dict(
            raw_text='Hiring Golang',
            post_link='https://t.me/jobs/1',
            published_at=datetime.now(timezone.utc),
            channel_name='jobs',
            analysis_result=decision().analysis,
        )
        await asyncio.gather(
            processor.persist_analyzed_message(**kwargs),
            processor.persist_analyzed_message(**kwargs),
        )

    asyncio.run(scenario())
    assert sheets.await_count == 1


def test_telegram_transient_retry_jitter_and_permanent_failure(tmp_path):
    svc, store, _, client, analyzer, _ = setup(tmp_path, [message()])
    calls = 0

    async def telegram(request, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError('secret query must not be logged')
        return await client(request, **kwargs)

    svc.client = telegram
    svc._wait = AsyncMock()
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert calls == 3 and analyzer.await_count == 1
    assert 1 <= svc._wait.await_args.args[1] <= 1.5
    assert svc.telemetry.attention_errors() == []
    assert store.get_run(run['search_run_id'])['status'] == 'completed'
    svc.client = AsyncMock(side_effect=errors.PremiumAccountRequiredError(None))
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert svc.client.await_count == 1
    assert store.get_run(run['search_run_id'])['error_reason'] == 'premium_required'


def test_repair_partial_sheets_despite_legacy_link(tmp_path):
    _, _, processor, _, _, sheets = setup(tmp_path)
    link = 'https://t.me/public_jobs/1'
    processor.dedupe_state.exported_links.add(link)
    saved = asyncio.run(
        processor.persist_analyzed_message(
            raw_text='Hiring Golang',
            post_link=link,
            published_at=datetime.now(timezone.utc),
            channel_name='jobs',
            analysis_result=decision().analysis,
            strict_delivery=True,
        )
    )
    assert saved.saved and sheets.await_count == 1
    assert saved.vacancy_id in processor.dedupe_state.exported_ids


def test_cancelled_persistence_keeps_lock_until_sdk_finishes(tmp_path):
    _, _, processor, _, _, _ = setup(tmp_path)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def sheets(**kwargs):
            entered.set()
            await release.wait()
            return True

        processor.append_to_sheet = sheets
        kwargs = dict(
            raw_text='Hiring Golang',
            post_link='https://t.me/public_jobs/1',
            published_at=datetime.now(timezone.utc),
            channel_name='jobs',
            analysis_result=decision().analysis,
        )
        task = asyncio.create_task(processor.persist_analyzed_message(**kwargs))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert processor._persistence_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not processor._persistence_lock.locked()

    asyncio.run(scenario())


def test_source_action_during_analysis_does_not_disrupt_auto_save(tmp_path):
    svc, store, _, _, _, sheets = setup(tmp_path, [message()])
    svc.settings_store = SettingsStore(str(tmp_path))
    run = store.create_run(query='Golang', mode='save')

    async def analyze(_):
        result = store.list_results(run['search_run_id'])['items'][0]
        store.request_action(result['result_id'], 'add_public_source')
        return decision()

    svc.analyzer = analyze
    asyncio.run(svc.tick())
    assert sheets.await_count == 1
    assert store.get_run(run['search_run_id'])['status'] == 'completed'


def test_cancel_discards_automatic_actions_but_keeps_manual_actions(tmp_path):
    _, store, _, _, _, _ = setup(tmp_path)
    run = store.create_run(query='Golang')
    rid = store.add_result(
        run['search_run_id'], **normalize_result(message(), channel(), 7)
    )
    store.update_result(rid, status='accepted', analysis_json=decision().as_json())
    store.request_action(rid, 'save', origin='run')
    store.cancel(run['search_run_id'])
    assert store.claim_action() is None
    store.request_action(rid, 'save')
    assert store.claim_action()['result_id'] == rid


def test_prefilter_rejects_generic_go_with_technical_context():
    assert prefilter_reason('Hiring backend developer, go to the office daily.', 'go')


def test_budget_review_does_not_poison_next_search(tmp_path):
    svc, store, _, _, analyzer, _ = setup(tmp_path, [message()], max_llm_calls=0)
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert store.list_results(run['search_run_id'])['items'][0]['status'] == 'review'
    svc.max_llm_calls = 20
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert analyzer.await_count == 1
    assert store.list_results(run['search_run_id'])['items'][0]['status'] == 'accepted'


def test_rejected_llm_decision_is_deduplicated(tmp_path):
    svc, store, _, _, analyzer, _ = setup(
        tmp_path,
        [message()],
        analyzer=AsyncMock(
            return_value=replace(decision(), go_role_strength='optional')
        ),
    )
    store.create_run(query='Golang')
    asyncio.run(svc.tick())
    run = store.create_run(query='Golang')
    asyncio.run(svc.tick())
    assert analyzer.await_count == 1
    assert store.list_results(run['search_run_id'])['items'][0]['status'] == 'duplicate'


def test_mistral_sdk_retries_disabled(monkeypatch):
    import json
    from unittest.mock import Mock
    from tg_vacancy_bot.llm import mistral
    from tg_vacancy_bot.premium_search.analyzer import analyze_premium_text

    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(decision_payload()))
                )
            ]
        )
    )
    configured = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client = SimpleNamespace(with_options=Mock(return_value=configured))
    monkeypatch.setattr(mistral, '_get_client', lambda: client)
    assert asyncio.run(analyze_premium_text('Hiring Golang')).status() == 'accepted'
    client.with_options.assert_called_once_with(max_retries=0)
    assert create.await_count == 1
