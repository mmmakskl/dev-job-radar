"""Mock integration checks; these do not measure live Mistral quality."""

import asyncio
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_premium_search import message, setup
from tests.test_profile_matcher import profile
from tests.test_universal_analysis import _payload
from tg_vacancy_bot import config
from tg_vacancy_bot.llm import universal
from tg_vacancy_bot.premium_search.analyzer import (
    adapt_universal,
    parse_premium_analysis,
)
from tg_vacancy_bot.premium_search.store import PremiumSearchStore
from tg_vacancy_bot.search.service import SearchService
from tg_vacancy_bot.search.store import SearchStore


def enabled(monkeypatch):
    monkeypatch.setenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'true')
    monkeypatch.setattr(config, 'CANDIDATE_BOT_ALLOWED_USER_IDS', [1, 2])
    monkeypatch.setattr(config, 'CANDIDATE_CATALOG_SEARCH_ENABLED', True, raising=False)
    monkeypatch.setattr(
        config, 'CANDIDATE_PROFILE_ALLOWED_USER_IDS', [1, 2], raising=False
    )


def universal_decision(required=('Python',), preferred=(), **changes):
    payload = _payload()
    payload['analysis'].update(
        required_stack=list(required), preferred_stack=list(preferred)
    )
    payload.update(changes)
    return universal._validate(payload)


@pytest.mark.parametrize(
    'required,preferred,expected',
    [
        (['Python'], [], 'rejected'),
        (['Python'], ['Go'], 'rejected'),
        (['Go'], [], 'accepted'),
    ],
)
def test_premium_backend_role_requires_significant_go(required, preferred, expected):
    decision = adapt_universal(universal_decision(required, preferred))
    assert decision.status() == expected
    restored = parse_premium_analysis(json.loads(decision.as_json()))
    assert (
        restored.universal_decision['classifications'][0]['role_id']
        == 'backend_developer'
    )
    assert restored.analysis.required_stack == required


def test_low_confidence_go_is_review():
    assert (
        adapt_universal(universal_decision(['Go'], confidence=89)).status() == 'review'
    )


def test_catalog_requests_are_owner_scoped_and_idempotent(tmp_path, monkeypatch):
    enabled(monkeypatch)
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    params = dict(
        query='Python Developer',
        sources=['premium'],
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile(stacks=['Python'])),
        client_request_id='intent-1',
    )
    first = store.create_run(**params)
    store.cancel(first['id'], owner='candidate:1')
    assert SearchStore(store.path).create_run(**params)['id'] == first['id']
    assert store.get_run(first['id'], owner='candidate:2') is None
    assert store.cancel(first['id'], owner='candidate:2') is None
    with pytest.raises(ValueError):
        store.create_run(**{**params, 'sources': ['threads']})
    with pytest.raises(ValueError):
        store.create_run(**{**params, 'owner': 'candidate:2'})
    monkeypatch.setattr(config, 'CANDIDATE_CATALOG_SEARCH_ENABLED', False)
    with pytest.raises(ValueError):
        store.create_run(**params)


def test_catalog_end_to_end_preview_is_private_and_keeps_source_date(
    tmp_path, monkeypatch
):
    enabled(monkeypatch)
    published = datetime.now(timezone.utc) - timedelta(days=4)
    svc, premium, processor, telegram, legacy, sheets = setup(
        tmp_path, [message(text='Hiring Python backend developer', date=published)]
    )
    processor.registry = SimpleNamespace(ingest=AsyncMock())
    llm = AsyncMock(return_value=universal_decision())
    monkeypatch.setattr(universal, 'analyze_universal_text', llm)
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    service = SearchService(
        store,
        processor,
        candidate_path=str(tmp_path / 'candidate.sqlite3'),
        premium_store=premium,
    )
    run = store.create_run(
        query='Python Developer',
        sources=['premium'],
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile(stacks=['Python'])),
        client_request_id='one',
    )

    async def scenario():
        await service._premium()
        # Recovery between child creation and parent linkage cannot create a second run.
        with store.connect() as c:
            c.execute(
                "UPDATE search_tasks SET status='queued',child_id=NULL WHERE run_id=?",
                (run['id'],),
            )
        await service._premium()
        await svc.tick()
        await service._premium()

    asyncio.run(scenario())
    items = store.list_results(run['id'], owner='candidate:1')['items']
    assert len(items) == 1
    assert items[0]['timestamp'] == published.isoformat()
    assert items[0]['publication_status'] == 'preview'
    assert len(premium.list_runs()) == 1
    assert llm.await_count == 1
    legacy.assert_not_awaited()
    sheets.assert_not_awaited()
    processor.registry.ingest.assert_not_called()
    assert telegram.calls


def test_catalog_child_isolated_and_cancelled_with_parent(tmp_path, monkeypatch):
    enabled(monkeypatch)
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    premium = PremiumSearchStore(str(tmp_path / 'premium.sqlite3'))
    service = SearchService(store, None, candidate_path='unused', premium_store=premium)
    runs = [
        store.create_run(
            query='Python Developer',
            sources=['premium'],
            track='catalog',
            owner=f'candidate:{uid}',
            profile_snapshot=asdict(
                replace(profile(stacks=['Python']), telegram_user_id=uid)
            ),
            client_request_id='same-token',
        )
        for uid in (1, 2)
    ]

    async def scenario():
        await service._premium()
        await service._premium()
        store.cancel(runs[0]['id'], owner='candidate:1')
        await service._premium()

    asyncio.run(scenario())
    children = {run['owner']: run for run in premium.list_runs()}
    assert children['candidate:1']['status'] == 'cancelled'
    assert children['candidate:2']['status'] == 'queued'


def test_catalog_matching_is_not_shared_between_profiles(tmp_path, monkeypatch):
    enabled(monkeypatch)
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    runs = [
        store.create_run(
            query='Python Developer',
            sources=['premium'],
            track='catalog',
            owner=f'candidate:{uid}',
            profile_snapshot=asdict(replace(profile(), telegram_user_id=uid)),
        )
        for uid in (1, 2)
    ]
    for run, classification in zip(runs, ('accepted', 'review')):
        store.retain(
            run['id'],
            source='premium',
            external_id='jobs_1',
            text='Python developer',
            timestamp='2020-01-01T00:00:00+00:00',
            classification=classification,
            classification_override=classification,
        )
    assert store.list_results(runs[0]['id'])['total'] == 1
    assert store.list_results(runs[1]['id'])['total'] == 0
    assert store.list_results(runs[1]['id'], include_review=True)['total'] == 1


def test_revoked_catalog_run_does_not_contact_telegram(tmp_path, monkeypatch):
    enabled(monkeypatch)
    service, premium, _, telegram, _, _ = setup(tmp_path)
    run = premium.create_run(
        query='Python Developer',
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile()),
    )
    monkeypatch.setattr(config, 'CANDIDATE_PROFILE_ALLOWED_USER_IDS', [])
    asyncio.run(service.tick())
    assert (
        premium.get_run(run['search_run_id'])['error_reason'] == 'catalog_unavailable'
    )
    assert telegram.calls == []


def test_one_cached_analysis_matches_two_profiles_without_new_llm(
    tmp_path, monkeypatch
):
    enabled(monkeypatch)
    service, store, _, _, _, _ = setup(
        tmp_path, [message(text='Hiring Python developer')]
    )
    llm = AsyncMock(return_value=universal_decision())
    monkeypatch.setattr(universal, 'analyze_universal_text', llm)
    first = store.create_run(
        query='Python Developer',
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile(stacks=['Python'])),
    )
    asyncio.run(service.tick())
    second = store.create_run(
        query='Backend Developer',
        track='catalog',
        owner='candidate:2',
        profile_snapshot=asdict(replace(profile(stacks=['Go']), telegram_user_id=2)),
    )
    asyncio.run(service.tick())
    assert store.list_results(first['search_run_id'])['total'] == 1
    assert store.list_results(second['search_run_id'])['total'] == 0
    assert llm.await_count == 1
    assert store.get_run(second['search_run_id'])['llm_calls'] == 0


def test_shared_save_ingests_full_catalog_decision_without_go_export(
    tmp_path, monkeypatch
):
    from tg_vacancy_bot.registry import VacancyRegistry

    enabled(monkeypatch)
    published = datetime.now(timezone.utc) - timedelta(days=3)
    service, store, processor, _, _, sheets = setup(
        tmp_path, [message(text='Hiring Python developer', date=published)]
    )
    processor.registry = VacancyRegistry(str(tmp_path / 'candidate.sqlite3'))
    monkeypatch.setattr(
        universal,
        'analyze_universal_text',
        AsyncMock(return_value=universal_decision()),
    )
    service.analyzer = AsyncMock(return_value=adapt_universal(universal_decision()))
    run = store.create_run(query='Python Developer')
    asyncio.run(service.tick())
    result = store.list_results(run['search_run_id'], status='rejected')['items'][0]
    result = store.get_result(result['result_id'])
    assert processor.registry.get(result['vacancy_id']) is None
    # This existing admin action admits the preview to the common registry.
    store.request_action(result['result_id'], 'publish')
    asyncio.run(service.tick())
    result = store.get_result(result['result_id'])
    assert result['status'] == 'saved'
    assert result['action_state'] == 'idle'
    vacancy = processor.registry.get(result['vacancy_id'])
    assert vacancy.published_at == published.isoformat()
    assert vacancy.decision.analysis.required_stack == ['Python']
    sheets.assert_not_awaited()


def test_threads_save_keeps_full_decision_and_timestamp(tmp_path, monkeypatch):
    from tests.test_search_service import setup as threads_setup
    from tg_vacancy_bot.registry import VacancyRegistry
    from tg_vacancy_bot.pipeline.processor import VacancyProcessor

    sheets = AsyncMock(return_value=True)
    processor = VacancyProcessor(
        lambda _: True,
        AsyncMock(),
        sheets,
        registry=VacancyRegistry(str(tmp_path / 'candidate.sqlite3')),
    )
    store, service = threads_setup(tmp_path, processor=processor)
    published = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    run = store.create_run(query='Golang Developer', sources=['threads'])
    item_id = store.retain(
        run['id'],
        source='threads',
        external_id='abc',
        text='Hiring Python developer',
        timestamp=published,
        permalink='https://www.threads.com/@team/post/abc',
        vacancy_id='threads:abc',
    )
    monkeypatch.setattr(
        universal,
        'analyze_universal_text',
        AsyncMock(return_value=universal_decision()),
    )
    store.request_action(item_id, 'save')
    asyncio.run(service._action(store.claim_action()))
    assert store.get_item(item_id)['publication_status'] == 'saved'
    vacancy = processor.registry.get('threads:abc')
    assert vacancy.published_at == published
    assert vacancy.decision.classifications
    assert (
        store.get_item(item_id)['universal_decision']['schema_version']
        == universal.SCHEMA_VERSION
    )
    # A repeat import missing its timestamp retains the established source date.
    store.retain(
        run['id'],
        source='threads',
        external_id='abc',
        text='Hiring Python developer',
        timestamp='',
        permalink='https://www.threads.com/@team/post/abc',
    )
    assert store.get_item(item_id)['timestamp'] == published
    sheets.assert_not_awaited()


@pytest.mark.parametrize('source', ['premium', 'threads'])
def test_unknown_source_date_stays_unknown_on_shared_save(
    tmp_path, monkeypatch, source
):
    from tg_vacancy_bot.registry import VacancyRegistry
    from tg_vacancy_bot.threads.source import ThreadsSource

    enabled(monkeypatch)
    service, premium, processor, _, _, sheets = setup(
        tmp_path, [message(text='Hiring Python developer', date=None)]
    )
    processor.registry = VacancyRegistry(str(tmp_path / 'candidate.sqlite3'))
    monkeypatch.setattr(
        universal,
        'analyze_universal_text',
        AsyncMock(return_value=universal_decision()),
    )
    if source == 'premium':
        service.analyzer = AsyncMock(return_value=adapt_universal(universal_decision()))
        run = premium.create_run(query='Python Developer')
        asyncio.run(service.tick())
        result = premium.list_results(run['search_run_id'], status='rejected')['items'][
            0
        ]
        assert result['can_persist'] is True
        premium.request_action(result['result_id'], 'save')
        asyncio.run(service.tick())
        result = premium.get_result(result['result_id'])
        assert result['status'] == 'saved'
        vacancy_id = result['vacancy_id']
    else:
        page = ThreadsSource._parse_page(
            {
                'data': [
                    {
                        'id': 'abc',
                        'text': 'Hiring Python developer',
                        'permalink': 'https://www.threads.com/@team/post/abc',
                    }
                ]
            }
        )
        assert page.invalid_count == 0
        assert page.posts[0].timestamp is None
        store = SearchStore(str(tmp_path / 'search.sqlite3'))
        service = SearchService(
            store, processor, candidate_path=processor.registry.path
        )
        run = store.create_run(query='Python Developer', sources=['threads'])
        item_id = store.retain(
            run['id'],
            source='threads',
            external_id='abc',
            text=page.posts[0].text,
            timestamp='',
            permalink=page.posts[0].permalink,
            vacancy_id='threads:abc',
        )
        assert store.get_item(item_id)['can_persist'] is True
        store.request_action(item_id, 'save')
        asyncio.run(service._action(store.claim_action()))
        assert store.get_item(item_id)['publication_status'] == 'saved'
        vacancy_id = 'threads:abc'
    assert processor.registry.get(vacancy_id).published_at is None
    sheets.assert_not_awaited()


def test_base_bot_access_revocation_blocks_queued_catalog(tmp_path, monkeypatch):
    enabled(monkeypatch)
    service, premium, _, telegram, _, _ = setup(tmp_path)
    run = premium.create_run(
        query='Python Developer',
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile()),
    )
    monkeypatch.setattr(config, 'CANDIDATE_BOT_ALLOWED_USER_IDS', [])
    asyncio.run(service.tick())
    assert (
        premium.get_run(run['search_run_id'])['error_reason'] == 'catalog_unavailable'
    )
    assert telegram.calls == []


def test_candidate_refresh_never_retargets_admin_action(tmp_path, monkeypatch):
    enabled(monkeypatch)
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    admin = store.create_run(query='Golang Developer', sources=['premium'])
    candidate = store.create_run(
        query='Golang Developer',
        sources=['premium'],
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile()),
    )
    common = dict(
        source='premium',
        external_id='jobs_1',
        text='Hiring Golang developer',
        timestamp='2020-01-01T00:00:00+00:00',
        permalink='https://t.me/jobs/1',
        classification='accepted',
        vacancy_id='jobs_1',
    )
    admin_item = store.retain(admin['id'], **common, premium_result_id='admin-result')
    store.request_action(admin_item, 'save')
    candidate_item = store.retain(
        candidate['id'], **common, premium_result_id='private-result'
    )
    assert candidate_item != admin_item
    assert store.get_item(admin_item)['premium_result_id'] == 'admin-result'
    # Repeated shared refresh also leaves an already queued target frozen.
    store.retain(admin['id'], **common, premium_result_id='new-admin-result')
    assert store.claim_action()['premium_result_id'] == 'admin-result'
    assert (
        store.list_results(candidate['id'], owner='candidate:1')['items'][0]['id']
        == candidate_item
    )
    assert store.list_results(admin['id'])['items'][0]['id'] == admin_item
    # Scope and keyed URLs survive migration and restart.
    restored = SearchStore(store.path)
    assert restored.get_item(admin_item)['premium_result_id'] == 'admin-result'


def test_candidate_preview_does_not_suppress_later_admin_search(tmp_path, monkeypatch):
    enabled(monkeypatch)
    service, store, _, _, _, _ = setup(tmp_path, [message()])
    monkeypatch.setattr(
        universal,
        'analyze_universal_text',
        AsyncMock(return_value=universal_decision(['Go'])),
    )
    private = store.create_run(
        query='Golang',
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile(stacks=['Go'])),
    )
    asyncio.run(service.tick())
    assert store.list_results(private['search_run_id'])['total'] == 1
    shared = store.create_run(query='Golang')
    asyncio.run(service.tick())
    assert store.list_results(shared['search_run_id'])['total'] == 1
    assert store.list_results(shared['search_run_id'], status='duplicate')['total'] == 0


def test_candidate_preview_cannot_be_published_through_shared_actions(
    tmp_path, monkeypatch
):
    enabled(monkeypatch)
    service, store, _, _, _, _ = setup(tmp_path, [message()])
    monkeypatch.setattr(
        universal,
        'analyze_universal_text',
        AsyncMock(return_value=universal_decision(['Go'])),
    )
    private = store.create_run(
        query='Golang',
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile(stacks=['Go'])),
    )
    asyncio.run(service.tick())
    result = store.list_results(private['search_run_id'])['items'][0]
    assert result['can_persist'] is False
    for action in ('save', 'publish'):
        with pytest.raises(ValueError, match='предпросмотр'):
            store.request_action(result['result_id'], action)


def test_private_run_keeps_its_source_snapshot_when_later_search_changes(
    tmp_path, monkeypatch
):
    enabled(monkeypatch)
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    options = dict(
        query='Backend Developer',
        sources=['premium'],
        track='catalog',
        owner='candidate:1',
        profile_snapshot=asdict(profile()),
    )
    first = store.create_run(**options)
    first_item = store.retain(
        first['id'],
        source='premium',
        external_id='jobs_1',
        text='Hiring Go developer',
        timestamp='2020-01-01T00:00:00+00:00',
        permalink='https://t.me/jobs/1',
        classification='accepted',
        classification_override='accepted',
    )
    store.cancel(first['id'], owner='candidate:1')
    second = store.create_run(**options)
    second_item = store.retain(
        second['id'],
        source='premium',
        external_id='jobs_1',
        text='Hiring Python developer',
        timestamp='2020-01-02T00:00:00+00:00',
        permalink='https://t.me/jobs/1',
        classification='accepted',
        classification_override='accepted',
    )
    assert first_item != second_item
    assert store.get_item(first_item)['text'] == 'Hiring Go developer'
    assert store.get_item(first_item)['timestamp'] == '2020-01-01T00:00:00+00:00'


def test_admin_api_does_not_expose_private_runs_or_actions(tmp_path, monkeypatch):
    from tests.test_admin_api import _client, _login
    from tests.test_premium_search import channel
    from tg_vacancy_bot.premium_search.service import normalize_result
    from tg_vacancy_bot.search.settings import search_database_path

    enabled(monkeypatch)
    api = _client(tmp_path, monkeypatch)
    csrf = _login(api)
    headers = {'X-CSRF-Token': csrf}
    premium = PremiumSearchStore(str(tmp_path / 'premium_search.sqlite3'))
    private = premium.create_run(
        query='Golang', owner='candidate:1', profile_snapshot=asdict(profile())
    )
    result_id = premium.add_result(
        private['search_run_id'], **normalize_result(message(), channel(), 7)
    )
    search = SearchStore(search_database_path(str(tmp_path)))
    personal = search.create_run(
        query='Golang', sources=['premium'], owner='candidate:1'
    )
    item_id = search.retain(
        personal['id'],
        source='premium',
        external_id='jobs_1',
        text='Hiring Golang',
        timestamp='',
        permalink='https://t.me/jobs/1',
    )
    for prefix, run_id, rid in (
        ('/api/v1/premium-search', private['search_run_id'], result_id),
        ('/api/v1/search', personal['id'], item_id),
    ):
        assert api.get(prefix + '/runs').json()['items'] == []
        assert api.get(prefix + '/runs/' + run_id).status_code == 404
        assert api.get(prefix + '/runs/' + run_id + '/results').status_code == 404
        assert (
            api.post(
                prefix + '/runs/' + run_id + '/cancel',
                headers=headers,
                json={'confirmed': True},
            ).status_code
            == 404
        )
        assert (
            api.post(
                prefix + '/results/' + rid + '/actions',
                headers=headers,
                json={'confirmed': True, 'action': 'save'},
            ).status_code
            == 404
        )
    tracks = api.get('/api/v1/premium-search/capabilities').json()['tracks']
    assert [track['key'] for track in tracks] == ['go']
