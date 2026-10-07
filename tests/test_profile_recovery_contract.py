"""Regression checks for profile screens and backfill delivery."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from tests.test_candidate_bot import FakeBotApi, callback, message
from tests.test_multidirection_candidate_feed import decision_for
from tg_vacancy_bot.profile_matcher import match_profile
from tg_vacancy_bot.pipeline.prefilter import universal_prefilter
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_delivery_worker import (
    CandidateDeliveryWorker,
    PersonalDeliveryStore,
)
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


def _setup(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    registry = VacancyRegistry(store.path)
    go = store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='',
        role_id='',
        preferences={'stacks': ['go']},
    )
    hr = store.create_profile(
        1,
        name='HR',
        direction_id='hr_recruiting',
        specialization_id='',
        role_id='',
        preferences={'stacks': ['sourcing'], 'delivery_mode': 'manual'},
        is_active=False,
    )
    return store, registry, go, hr


def _ingest(registry, vacancy_id, decision, published):
    registry.ingest(
        vacancy_id=vacancy_id,
        post_link=f'https://t.me/jobs/{vacancy_id}',
        decision=decision,
        published_at=published,
        raw_text=decision.analysis.summary,
    )


def test_selected_stack_must_be_required_and_empty_stack_is_unrestricted(tmp_path):
    store, _, go, _ = _setup(tmp_path)
    decision = decision_for(
        'development', 'backend', 'backend_developer', 'Go', 'Go role'
    )
    preferred_only = replace(
        decision,
        analysis=replace(decision.analysis, required_stack=[], preferred_stack=['Go']),
    )
    assert match_profile(preferred_only, go).status != 'match'
    empty = store.update_profile(1, go.profile_id, preferences={'stacks': []})
    assert match_profile(preferred_only, empty).status == 'match'


def test_age_navigation_expires_after_profile_switch_and_delete_preserves_archive(
    tmp_path,
):
    store, registry, go, hr = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    go_decision = decision_for(
        'development', 'backend', 'backend_developer', 'Go', 'Go role'
    )
    hr_decision = decision_for(
        'hr_recruiting', 'recruiting', 'it_recruiter', 'Sourcing', 'HR role'
    )
    for index in (1, 2):
        _ingest(
            registry,
            f'go-{index}',
            go_decision,
            (now - timedelta(hours=index)).isoformat(),
        )
    _ingest(registry, 'hr-1', hr_decision, (now - timedelta(hours=1)).isoformat())
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1}, registry=registry)
    asyncio.run(bot.handle_update(message(1, '/new')))
    assert 'Go role' in api.messages[-1][1]
    nav = next(
        button['callback_data']
        for row in api.messages[-1][2]['reply_markup']['inline_keyboard']
        for button in row
        if button['text'] == '→'
    )
    assert len(nav.encode()) <= 64
    store.activate_profile(1, hr.profile_id)
    update = callback(1, nav)
    update['callback_query']['message'] = {
        'message_id': 1,
        'chat': {'id': 1, 'type': 'private'},
    }
    asyncio.run(bot.handle_update(update))
    assert api.edits == []
    assert 'устарела' in api.answers[-1][1]
    asyncio.run(bot.handle_update(message(1, '/new')))
    assert 'HR role' in api.messages[-1][1]
    assert 'Go role' not in api.messages[-1][1]
    vacancy = store.get_vacancy(registry.list_personal_matches(1)[0].callback_key)
    assert store.save_vacancy(1, vacancy.callback_key)
    store.put_profile_draft(
        1, {'token': 'draft', 'revision': 0, 'profile_id': hr.profile_id}
    )
    assert store.delete_profile(1, hr.profile_id)
    assert store.get_active_profile(1) is None
    assert store.get_profile_draft(1) is None
    assert store.get_profile_versions(1, hr.profile_id) == []
    assert store.list_for_user(1, 'saved')[0].vacancy_id == 'hr-1'
    with store._connect() as connection:
        assert (
            connection.execute(
                'SELECT 1 FROM registry_profile_matches WHERE profile_id=?',
                (hr.profile_id,),
            ).fetchone()
            is None
        )


def test_profile_delete_requires_current_version_and_confirmation(tmp_path):
    store, registry, go, _ = _setup(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1}, registry=registry)
    asyncio.run(
        bot.handle_update(callback(1, f'p:delete:{go.profile_id}:{go.version}'))
    )
    assert store.get_profile(1, go.profile_id) is not None
    confirm = next(
        button['callback_data']
        for row in api.messages[-1][2]['reply_markup']['inline_keyboard']
        for button in row
        if button['text'] == 'Да, удалить'
    )
    store.update_profile(1, go.profile_id, name='Updated')
    asyncio.run(bot.handle_update(callback(1, confirm)))
    assert store.get_profile(1, go.profile_id) is not None
    current = store.get_profile(1, go.profile_id)
    asyncio.run(
        bot.handle_update(callback(1, f'p:confirm:{go.profile_id}:{current.version}'))
    )
    assert store.get_profile(1, go.profile_id) is None


def test_backfill_digest_pages_are_idempotent_and_manual_profiles_are_quiet(tmp_path):
    store, registry, go, hr = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    decision = decision_for(
        'development', 'backend', 'backend_developer', 'Go', 'Go role'
    )
    for index in range(8):
        _ingest(
            registry, f'go-{index}', decision, (now - timedelta(days=2)).isoformat()
        )
    api = FakeBotApi()
    worker = CandidateDeliveryWorker(
        api,
        registry,
        PersonalDeliveryStore(store.path),
        allowed_user_ids={1},
        enabled=True,
    )
    first = asyncio.run(worker.deliver_history(now))
    second = asyncio.run(worker.deliver_history(now))
    assert first['vacancies_sent'] == 8
    assert first['pages_sent'] >= 1
    assert any('История вакансий' in item[1] for item in api.messages)
    assert second == {'pages_sent': 0, 'vacancies_sent': 0, 'unknown': 0}
    store.activate_profile(1, hr.profile_id)
    _ingest(
        registry,
        'hr-1',
        decision_for(
            'hr_recruiting', 'recruiting', 'it_recruiter', 'Sourcing', 'HR role'
        ),
        now.isoformat(),
    )
    assert asyncio.run(worker.deliver_history(now))['vacancies_sent'] == 0


def test_unavailable_analysis_can_be_replayed_and_prefilter_keeps_tracks(tmp_path):
    for text in (
        'Ищем Java backend разработчика. Spring Boot обязателен.',
        'Открыта вакансия IT-рекрутера: sourcing и поиск разработчиков.',
        'Ищем разработчика 1С:Предприятие, BSL и ERP обязательны.',
    ):
        assert universal_prefilter(text)
    store, registry, _, _ = _setup(tmp_path)
    registry.ingest(
        vacancy_id='retry',
        post_link='https://t.me/jobs/retry',
        decision=None,
        raw_text='Ищем Go разработчика',
        unavailable_reason='invalid_schema_primary_roles',
    )
    assert registry.get('retry').unavailable_reason == 'invalid_schema_primary_roles'
    _ingest(
        registry,
        'retry',
        decision_for('development', 'backend', 'backend_developer', 'Go', 'Go role'),
        datetime.now(timezone.utc).isoformat(),
    )
    assert registry.get('retry').decision is not None
    assert [item.vacancy_id for item in registry.list_personal_matches(1)] == ['retry']
