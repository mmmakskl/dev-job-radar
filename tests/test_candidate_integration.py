"""Offline integration checks with local SQLite and mocked Bot API responses."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from tests.test_candidate_bot import FakeBotApi, callback, message
from tests.test_universal_analysis import _payload
from tg_vacancy_bot import config
from tg_vacancy_bot.llm.universal import _validate
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.search.store import SearchStore
from tg_vacancy_bot.telegram.bot_api import BotApiError, BotApiRejected
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_delivery_worker import (
    CandidateDeliveryWorker,
    PersonalDeliveryStore,
    determining_profile,
)
from tg_vacancy_bot.telegram.candidate_search import CandidateSearch
from tg_vacancy_bot.telegram.candidate_store import CandidateStore

NOW = datetime(2026, 10, 4, 9, 30, tzinfo=timezone.utc)


def setup(tmp_path, mode='immediate'):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    registry = VacancyRegistry(store.path)
    profile = store.create_profile(
        1,
        name='Backend <team>',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={
            'delivery_mode': mode,
            'premium_template_id': 'role:backend_developer',
        },
    )
    payload = _payload()
    payload['analysis'].update(title='Backend <role>', summary='Работа & API')
    decision = _validate(payload)
    return store, registry, profile, decision


def ingest(registry, decision, vacancy_id='jobs_1', **kwargs):
    return registry.ingest(
        vacancy_id=vacancy_id,
        post_link=f'https://t.me/jobs/{vacancy_id.split("_")[-1]}',
        decision=decision,
        eligible_at=NOW - timedelta(hours=1),
        published_at='2020-01-01T00:00:00+00:00',
        **kwargs,
    )


def worker(api, registry, **kwargs):
    return CandidateDeliveryWorker(
        api,
        registry,
        PersonalDeliveryStore(registry.path),
        allowed_user_ids={1},
        enabled=True,
        **kwargs,
    )


def states(store):
    with store.connect() as connection:
        return {
            row['vacancy_id']: row['state']
            for row in connection.execute('SELECT * FROM candidate_personal_deliveries')
        }


def test_feed_is_owner_scoped_current_confirmed_and_keeps_new_go_only(tmp_path):
    store, registry, profile, decision = setup(tmp_path)
    ingest(registry, decision)
    second = store.create_profile(
        1,
        name='Другой профиль',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
    )
    ingest(registry, replace(decision, needs_review=True), 'jobs_2')
    api = FakeBotApi()
    bot = CandidateBot(
        api,
        store,
        {1, 2},
        registry=registry,
        profile_feed_enabled=True,
        profile_allowed_user_ids={1, 2},
    )
    asyncio.run(bot.handle_update(message(1, 'Для меня')))
    assert len(api.messages) == 1
    text = api.messages[0][1]
    assert 'Backend &lt;team&gt;' in text and 'Другой профиль' in text
    assert '2020' in text and '&lt;role&gt;' in text
    assert store.list_for_user(1, 'new') == []
    asyncio.run(bot.handle_update(message(2, 'Для меня')))
    assert 'пока нет' in api.messages[-1][1]
    store.update_profile(1, profile.profile_id, is_active=False)
    store.update_profile(1, second.profile_id, is_active=False)
    asyncio.run(bot.handle_update(message(1, 'Для меня')))
    assert 'пока нет' in api.messages[-1][1]


def test_personal_feed_requires_independent_rollout(tmp_path):
    store, registry, _, decision = setup(tmp_path)
    ingest(registry, decision)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1}, registry=registry, profile_feed_enabled=True)
    asyncio.run(bot.handle_update(message(1, 'Для меня')))
    assert 'недоступна' in api.messages[-1][1]


def test_delivery_priority_manual_nonblocking_and_stable_timezone(tmp_path):
    store, registry, manual, decision = setup(tmp_path, 'manual')
    ingest(registry, decision)
    hourly = store.create_profile(
        1,
        name='Часовой',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'delivery_mode': 'hourly', 'timezone': 'Asia/Kolkata'},
    )
    immediate = store.create_profile(
        1,
        name='Сразу',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'delivery_mode': 'immediate', 'timezone': 'Europe/Moscow'},
    )
    match = registry.list_personal_matches(1)[0]
    assert determining_profile(match).profile_id == immediate.profile_id
    api = FakeBotApi()
    runner = worker(api, registry)
    asyncio.run(runner.tick(NOW))
    assert len(api.messages) == 1 and 'Часовая сводка' not in api.messages[0][1]
    assert all(name in api.messages[0][1] for name in ['Часовой', 'Сразу', 'Backend'])
    store.update_profile(
        1, immediate.profile_id, preferences={'delivery_mode': 'hourly'}
    )
    asyncio.run(worker(api, registry).tick(NOW + timedelta(hours=2)))
    assert len(api.messages) == 1
    assert states(runner.store) == {'jobs_1': 'sent'}
    assert manual.is_active and hourly.is_active


def test_hourly_eligibility_uses_winning_profile_local_hour(tmp_path):
    store, registry, profile, decision = setup(tmp_path, 'hourly')
    store.update_profile(
        1,
        profile.profile_id,
        preferences={'delivery_mode': 'hourly', 'timezone': 'Asia/Kolkata'},
    )
    registry.ingest(
        vacancy_id='jobs_1',
        post_link='https://t.me/jobs/1',
        decision=decision,
        eligible_at=NOW - timedelta(minutes=15),
        published_at=None,
    )
    api = FakeBotApi()
    asyncio.run(worker(api, registry).tick(NOW))
    assert len(api.messages) == 1  # 15:00 local window, rather than 09:00 UTC.
    assert 'Дата публикации не указана' in api.messages[0][1]
    assert 'UTC+05:30' in api.messages[0][1]


def test_unknown_send_outcome_never_retries_after_restart(tmp_path):
    _, registry, _, decision = setup(tmp_path)
    ingest(registry, decision)
    api = FakeBotApi()
    api.send_message = AsyncMock(side_effect=BotApiError('timeout'))
    runner = worker(api, registry)
    with pytest.raises(BotApiError):
        asyncio.run(runner.tick(NOW))
    api.send_message = AsyncMock(return_value={'message_id': 2})
    asyncio.run(worker(api, registry).tick(NOW + timedelta(days=3)))
    api.send_message.assert_not_called()
    assert states(runner.store) == {'jobs_1': 'unknown'}


def test_crash_after_send_before_confirm_never_retries(tmp_path, monkeypatch):
    _, registry, _, decision = setup(tmp_path)
    ingest(registry, decision)
    api = FakeBotApi()
    runner = worker(api, registry)
    original = runner.store.transition

    def crash(token, before, after, **kwargs):
        if after == 'sent':
            raise RuntimeError('power loss')
        return original(token, before, after, **kwargs)

    monkeypatch.setattr(runner.store, 'transition', crash)
    with pytest.raises(RuntimeError):
        asyncio.run(runner.tick(NOW))
    asyncio.run(worker(api, registry).tick(NOW + timedelta(days=1)))
    assert len(api.messages) == 1
    assert states(runner.store) == {'jobs_1': 'sending'}


def test_rejected_send_returns_to_queue_and_inactive_profile_is_rechecked(tmp_path):
    store, registry, profile, decision = setup(tmp_path)
    ingest(registry, decision)
    api = FakeBotApi()
    api.send_message = AsyncMock(side_effect=BotApiRejected('429'))
    runner = worker(api, registry)
    asyncio.run(runner.tick(NOW))
    assert states(runner.store) == {'jobs_1': 'pending'}
    store.update_profile(1, profile.profile_id, is_active=False)
    api.send_message.reset_mock()
    asyncio.run(runner.tick(NOW))
    api.send_message.assert_not_called()
    store.update_profile(1, profile.profile_id, is_active=True)
    api.send_message = AsyncMock(return_value={'message_id': 7})
    asyncio.run(runner.tick(NOW))
    assert states(runner.store) == {'jobs_1': 'sent'}


def test_hourly_partial_pages_confirm_only_successes(tmp_path):
    _, registry, _, decision = setup(tmp_path, 'hourly')
    for i in range(20):
        ingest(
            registry,
            replace(
                decision, analysis=replace(decision.analysis, title='Role ' + 'A' * 230)
            ),
            f'jobs_{i}',
        )
    api = FakeBotApi()
    calls = 0

    async def send(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BotApiError('unknown')
        return {'message_id': calls}

    api.send_message = send
    runner = worker(api, registry)
    with pytest.raises(BotApiError):
        asyncio.run(runner.tick(NOW))
    result = states(runner.store)
    assert set(result.values()) == {'sent', 'unknown'}
    assert len(result) < 20  # Future pages were never reserved.
    api.send_message = AsyncMock(return_value={'message_id': 10})
    asyncio.run(runner.tick(NOW + timedelta(hours=1)))
    assert len(states(runner.store)) == 20
    assert all(states(runner.store)[vid] == state for vid, state in result.items())


def test_concurrent_reservations_have_one_winner(tmp_path):
    store, _, _, _ = setup(tmp_path)
    first, second = PersonalDeliveryStore(store.path), PersonalDeliveryStore(store.path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda queue: queue.reserve(1, ('jobs_1',), NOW)[1], (first, second)
            )
        )
    assert sorted(len(ids) for ids in results) == [0, 1]


def test_delivery_is_disabled_by_default(tmp_path):
    _, registry, _, decision = setup(tmp_path)
    ingest(registry, decision)
    api = FakeBotApi()
    runner = CandidateDeliveryWorker(
        api, registry, PersonalDeliveryStore(registry.path), allowed_user_ids={1}
    )
    asyncio.run(runner.tick(NOW))
    assert api.messages == []


def search_setup(tmp_path, monkeypatch):
    store, registry, profile, _ = setup(tmp_path)
    monkeypatch.setenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'true')
    monkeypatch.setattr(config, 'CANDIDATE_CATALOG_SEARCH_ENABLED', True)
    monkeypatch.setattr(config, 'CANDIDATE_PROFILE_ALLOWED_USER_IDS', {1})
    monkeypatch.setattr(config, 'CANDIDATE_BOT_ALLOWED_USER_IDS', {1})
    searches = SearchStore(str(tmp_path / 'search.sqlite3'))
    api = FakeBotApi()
    ui = CandidateSearch(
        api, store, searches, enabled=True, premium_enabled=True, allowed_user_ids={1}
    )
    return store, registry, profile, searches, api, ui


def latest_button(api, label):
    return next(
        button['callback_data']
        for row in api.messages[-1][2]['reply_markup']['inline_keyboard']
        for button in row
        if button['text'] == label
    )


def test_search_preview_idempotency_ownership_restart_and_cancel(tmp_path, monkeypatch):
    store, _, profile, searches, api, ui = search_setup(tmp_path, monkeypatch)
    asyncio.run(ui.preview(1, profile))
    start = latest_button(api, 'Запустить поиск').split(':')
    assert searches.list_runs() == []
    assert not asyncio.run(ui.callback(2, start))
    assert asyncio.run(ui.callback(1, start))
    assert asyncio.run(ui.callback(1, start))
    assert len(searches.list_runs()) == 1
    run = searches.list_runs()[0]
    assert (
        run['owner'] == 'candidate:1'
        and run['mode'] == 'preview'
        and run['track'] == 'catalog'
    )
    assert run['profile_snapshot']['version'] == profile.version
    asyncio.run(ui.preview(1, profile))
    cancel = latest_button(api, 'Отменить').split(':')
    assert not asyncio.run(ui.callback(1, start))  # Superseded preview.
    assert asyncio.run(ui.callback(1, cancel))
    assert searches.get_run(run['id'])['status'] == 'cancelled'


def test_search_changed_profile_rejects_stale_start(tmp_path, monkeypatch):
    store, _, profile, searches, api, ui = search_setup(tmp_path, monkeypatch)
    asyncio.run(ui.preview(1, profile))
    start = latest_button(api, 'Запустить поиск').split(':')
    store.update_profile(1, profile.profile_id, name='Changed')
    assert not asyncio.run(ui.callback(1, start))
    assert searches.list_runs() == []


def test_search_save_is_personal_and_source_date_survives(tmp_path, monkeypatch):
    store, registry, profile, searches, api, ui = search_setup(tmp_path, monkeypatch)
    asyncio.run(ui.preview(1, profile))
    start = latest_button(api, 'Запустить поиск').split(':')
    asyncio.run(ui.callback(1, start))
    run = searches.list_runs()[0]
    item = searches.retain(
        run['id'],
        source='premium',
        external_id='jobs_88',
        vacancy_id='jobs_88',
        text='Backend role',
        permalink='https://t.me/jobs/88',
        timestamp='2020-01-01T00:00:00+00:00',
        classification='accepted',
        title='Backend',
        summary='Work',
    )
    save = ['cs', 'save', start[2], item]
    assert asyncio.run(ui.callback(1, save))
    assert (
        store.list_for_user(1, 'saved')[0].published_at == '2020-01-01T00:00:00+00:00'
    )
    assert store.list_for_user(2, 'saved') == []
    assert store.list_for_user(2, 'new') == []
    assert registry.list_personal_matches(1) == []
    assert searches.get_item(item)['publication_status'] == 'preview'
    assert not asyncio.run(ui.callback(2, save))
    assert not asyncio.run(ui.callback(1, ['cs', 'save', start[2], 'unrelated']))


def test_search_disabled_and_template_choice_does_not_start(tmp_path):
    store, _, profile, _ = setup(tmp_path)
    api = FakeBotApi()
    searches = SearchStore(str(tmp_path / 'search.sqlite3'))
    ui = CandidateSearch(api, store, searches)
    asyncio.run(ui.preview(1, profile))
    assert 'недоступен' in api.messages[-1][1] and searches.list_runs() == []
    bot = CandidateBot(api, store, {1}, search=ui)
    asyncio.run(
        bot.handle_update(
            callback(1, f'p:template:{profile.profile_id}:{profile.version}:0')
        )
    )
    assert searches.list_runs() == []


def test_known_private_callback_cannot_be_saved_by_another_user(tmp_path, monkeypatch):
    store, registry, profile, searches, api, ui = search_setup(tmp_path, monkeypatch)
    private = store.register_vacancy(
        vacancy_id='jobs_private',
        title='Private preview',
        company=None,
        summary=None,
        post_link='https://t.me/jobs/77',
        apply_link=None,
        published_at=None,
        go_visible=False,
    )
    store.save_vacancy(1, private.callback_key)
    bot = CandidateBot(
        api,
        store,
        {1, 2},
        registry=registry,
        profile_feed_enabled=True,
        profile_allowed_user_ids={1, 2},
    )
    asyncio.run(bot.handle_update(callback(2, f'v:s:{private.callback_key}')))
    assert 'недоступна' in api.answers[-1][1]
    assert store.list_for_user(2, 'saved') == []
    asyncio.run(bot.handle_update(callback(1, f'v:s:{private.callback_key}')))
    assert api.answers[-1][1] == 'Сохранено.'


def test_two_delivery_workers_racing_send_once(tmp_path):
    _, registry, _, decision = setup(tmp_path)
    ingest(registry, decision)
    api = FakeBotApi()
    first, second = worker(api, registry), worker(api, registry)

    async def race():
        await asyncio.gather(first.tick(NOW), second.tick(NOW))

    asyncio.run(race())
    assert len(api.messages) == 1


def test_profile_mode_edit_during_send_revalidates_remaining_pages(tmp_path):
    store, registry, profile, decision = setup(tmp_path)
    ingest(registry, decision, 'jobs_1')
    ingest(registry, decision, 'jobs_2')
    api = FakeBotApi()
    original_send = api.send_message

    async def send(*args, **kwargs):
        result = await original_send(*args, **kwargs)
        store.update_profile(
            1, profile.profile_id, preferences={'delivery_mode': 'manual'}
        )
        return result

    api.send_message = send
    runner = worker(api, registry)
    asyncio.run(runner.tick(NOW))
    assert len(api.messages) == 1
    assert states(runner.store) == {'jobs_1': 'sent'}


def test_partial_digest_rejection_leaves_future_pages_unreserved(tmp_path):
    _, registry, _, decision = setup(tmp_path, 'hourly')
    for i in range(20):
        ingest(
            registry,
            replace(decision, analysis=replace(decision.analysis, title='A' * 240)),
            f'jobs_{i}',
        )
    api = FakeBotApi()
    api.send_message = AsyncMock(
        side_effect=[{'message_id': 1}, BotApiRejected('retry later')]
    )
    runner = worker(api, registry)
    asyncio.run(runner.tick(NOW))
    before = states(runner.store)
    assert set(before.values()) == {'sent', 'pending'}
    assert len(before) < 20 and api.send_message.await_count == 2
    api.send_message = AsyncMock(return_value={'message_id': 2})
    asyncio.run(worker(api, registry).tick(NOW + timedelta(hours=1)))
    assert set(states(runner.store).values()) == {'sent'}
    assert len(states(runner.store)) == 20


def test_atomic_reservation_rejects_stale_profile_version(tmp_path):
    store, registry, profile, decision = setup(tmp_path)
    ingest(registry, decision)
    match = registry.list_personal_matches(1)[0]
    store.update_profile(1, profile.profile_id, preferences={'delivery_mode': 'manual'})
    queue = PersonalDeliveryStore(store.path)
    _, ids = queue.reserve(
        1,
        (match.vacancy_id,),
        NOW,
        expected_profiles={match.vacancy_id: match.matched_profiles},
    )
    assert ids == ()


def test_private_archive_save_does_not_rewrite_existing_shared_card(
    tmp_path, monkeypatch
):
    store, _, profile, searches, api, ui = search_setup(tmp_path, monkeypatch)
    shared = store.register_vacancy(
        vacancy_id='jobs_88',
        title='Published original',
        company='Original',
        summary='Preserved',
        post_link='https://t.me/jobs/88',
        apply_link=None,
        published_at='2020-01-01T00:00:00+00:00',
    )
    asyncio.run(ui.preview(1, profile))
    start = latest_button(api, 'Запустить поиск').split(':')
    assert asyncio.run(ui.callback(1, start))
    run = searches.list_runs()[0]
    item = searches.retain(
        run['id'],
        source='premium',
        external_id='jobs_88',
        vacancy_id='jobs_88',
        text='Updated text',
        permalink='https://t.me/jobs/88',
        timestamp='',
        classification='accepted',
        title='Private changed title',
    )
    assert asyncio.run(ui.callback(1, ['cs', 'save', start[2], item]))
    assert store.get_vacancy(shared.callback_key) == shared
    assert store.list_for_user(1, 'saved') == [shared]


def test_concurrent_private_save_never_overwrites_ingestion_card(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, _, _, _ = setup(tmp_path)
    barrier = Barrier(2)

    def write(private):
        barrier.wait()
        return store.register_vacancy(
            vacancy_id='race_card',
            title='Private' if private else 'Shared',
            company=None,
            summary=None,
            post_link='https://t.me/jobs/88',
            apply_link=None,
            published_at=None,
            go_visible=not private,
            update_existing=not private,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write, (True, False)))
    cards = store.list_for_user(2, 'new')
    assert len(cards) == 1 and cards[0].title == 'Shared'
