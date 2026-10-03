"""Offline integration checks; mocked decisions do not measure Mistral quality."""

import asyncio
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tg_vacancy_bot.llm.schemas import EXPECTED_FIELDS
from tg_vacancy_bot.llm.universal import (
    AnalysisUnavailable,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    _validate,
    go_projection_accepted,
)
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.registry_migration import backup_database, import_legacy


def decision(stack='Python', *, confidence=95, role_confidence=95):
    analysis = {key: None for key in EXPECTED_FIELDS}
    analysis.update(
        is_match=True,
        title='Backend developer',
        primary_roles=['Backend'],
        specializations=[],
        required_stack=[stack],
        preferred_stack=[],
    )
    return _validate(
        dict(
            schema_version=SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            is_vacancy=True,
            classifications=[
                dict(
                    direction_id='development',
                    specialization_id='backend',
                    role_id='backend_developer',
                    confidence=role_confidence,
                )
            ],
            analysis=analysis,
            confidence=confidence,
            reason_code='vacancy_match',
            needs_review=False,
        )
    )


def profile(registry, user=1):
    return registry.candidates.create_profile(
        user,
        name='Backend',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
    )


def ingest(registry, **kwargs):
    return registry.ingest(
        vacancy_id='jobs_1',
        post_link='https://t.me/jobs/1',
        decision=kwargs.pop('decision', decision()),
        **kwargs,
    )


def test_shared_analysis_current_profile_versions_and_owner_isolation(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
    first, second = profile(registry), profile(registry)
    ingest(registry)
    result = registry.list_personal_matches(1)
    assert len(result) == 1
    assert {p.profile_id for p in result[0].matched_profiles} == {
        first.profile_id,
        second.profile_id,
    }
    assert registry.list_personal_matches(2) == []
    registry.candidates.update_profile(1, first.profile_id, is_active=False)
    registry.candidates.update_profile(
        1, second.profile_id, preferences={'stacks': ['go']}
    )
    assert registry.list_personal_matches(1) == []
    with registry._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM registry_analyses').fetchone()[0] == 1
        assert (
            c.execute('SELECT COUNT(*) FROM registry_profile_matches').fetchone()[0]
            == 3
        )


@pytest.mark.parametrize(
    'mode', ['review', 'low', 'role_low', 'unavailable', 'negative']
)
def test_uncertainty_does_not_match(tmp_path, mode):
    registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
    profile(registry)
    value = decision(
        confidence=20 if mode == 'low' else 95,
        role_confidence=20 if mode == 'role_low' else 95,
    )
    value = replace(value, needs_review=mode == 'review', is_vacancy=mode != 'negative')
    ingest(registry, decision=None if mode == 'unavailable' else value)
    assert registry.list_personal_matches(1) == []


def test_identity_dates_and_legacy_callbacks_are_preserved(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
    profile(registry)
    stamp = '2024-01-01T07:30:00+03:00'
    original = ingest(registry, published_at=stamp)
    with registry._connect() as c:
        c.execute("UPDATE vacancies SET callback_key='old-card-key'")
    repeated = ingest(registry, published_at=None)
    assert repeated.published_at == '2024-01-01T04:30:00+00:00'
    assert repeated.discovered_at == original.discovered_at
    assert repeated.eligible_at == original.eligible_at
    assert registry.list_personal_matches(1)[0].callback_key == 'old-card-key'
    assert registry.candidates.list_for_user(1, 'new') == []
    assert registry.candidates.save_vacancy(1, 'old-card-key')
    assert len(registry.candidates.list_for_user(1, 'saved')) == 1
    same = registry.ingest(
        vacancy_id='premium_other',
        post_link=original.post_link,
        decision=decision(),
        source_type='premium',
    )
    assert same.vacancy_id == original.vacancy_id
    registry.ingest(
        vacancy_id='jobs_2',
        post_link='https://t.me/jobs/2',
        decision=decision(),
        raw_text='same text',
    )
    assert len(registry.list_vacancies()) == 2


@pytest.mark.parametrize('stamp', [None, '', 'invalid', '2024-01-01T10:30:00'])
def test_unknown_dates_stay_unknown(tmp_path, stamp):
    registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
    assert ingest(registry, published_at=stamp).published_at is None


def test_go_projection_needs_required_significant_go():
    assert go_projection_accepted(decision('Go'))
    assert not go_projection_accepted(decision('Python'))
    optional = decision('Python')
    optional = replace(
        optional, analysis=replace(optional.analysis, preferred_stack=['Go'])
    )
    assert not go_projection_accepted(optional)
    assert not go_projection_accepted(decision('Go', confidence=89))
    assert not go_projection_accepted(decision('Go', role_confidence=89))


def test_source_analysis_registry_matching_and_projection_path(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(
            'tg_vacancy_bot.pipeline.processor.asyncio.sleep', AsyncMock()
        )
        registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
        profile(registry)
        analyzer, sheets = AsyncMock(return_value=decision()), AsyncMock(
            return_value=True
        )
        processor = VacancyProcessor(
            lambda text: True, analyzer, sheets, registry=registry
        )
        published = datetime(2023, 1, 2, tzinfo=timezone.utc)
        args = (
            'Hiring backend developer',
            'Hiring backend developer',
            'https://t.me/jobs/1',
            published,
            'jobs',
        )
        assert await processor.process_message(*args)
        assert await processor.process_message(*args)
        assert analyzer.await_count == 1
        sheets.assert_not_awaited()
        assert registry.list_personal_matches(1)[0].published_at.startswith(
            '2023-01-02'
        )
        assert registry.candidates.list_for_user(1, 'new') == []
        analyzer.return_value = decision('Go')
        assert await processor.process_message(
            args[0], args[1], 'https://t.me/jobs/2', None, 'jobs'
        )
        sheets.assert_awaited_once()
        assert len(registry.candidates.list_for_user(1, 'new')) == 1

    asyncio.run(run())


def test_analysis_failure_is_durable_review(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(
            'tg_vacancy_bot.pipeline.processor.asyncio.sleep', AsyncMock()
        )
        registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
        profile(registry)
        processor = VacancyProcessor(
            lambda text: True,
            AsyncMock(side_effect=AnalysisUnavailable('daily_limit')),
            AsyncMock(),
            registry=registry,
        )
        assert not await processor.process_message(
            'Hiring backend', 'Hiring backend', 'https://t.me/jobs/1', None, 'jobs'
        )
        vacancy = registry.list_vacancies(eligible_only=False)[0]
        assert vacancy.unavailable_reason == 'daily_limit'
        assert registry.list_personal_matches(1) == []

    asyncio.run(run())


def test_dry_run_repeat_import_backup_and_restart_preserve_data(tmp_path):
    path = str(tmp_path / 'candidate.db')
    registry = VacancyRegistry(path)
    p = profile(registry)
    ingest(registry, published_at='2024-01-01T00:00:00+00:00')
    with registry._connect() as c:
        c.execute(
            "UPDATE vacancies SET callback_key='historical-key', delivery_state='sending', channel_message_id=4"
        )
    registry.candidates.save_vacancy(1, 'historical-key')
    before = {
        table: _read(path, table)
        for table in (
            'vacancies',
            'candidate_profiles',
            'candidate_profile_versions',
            'user_saved_vacancies',
        )
    }
    dry = import_legacy(path)
    assert dry['dry_run'] and dry['foreign_key_violations'] == 0
    assert len(_read(path, 'registry_sources')) == 1
    applied = import_legacy(path, dry_run=False)
    again = import_legacy(path, dry_run=False)
    assert applied['counts'] == again['counts']
    assert again['new_registry_vacancies'] == 0
    reopened = VacancyRegistry(path)
    assert (
        reopened.list_personal_matches(1)[0].matched_profiles[0].profile_id
        == p.profile_id
    )
    assert before == {table: _read(path, table) for table in before}
    backup = str(tmp_path / 'backup.db')
    backup_database(path, backup)
    assert _read(backup, 'user_saved_vacancies') == before['user_saved_vacancies']
    with pytest.raises(ValueError, match='backup_destination_exists'):
        backup_database(path, backup)


def _read(path, table):
    with sqlite3.connect(path) as c:
        return c.execute(f'SELECT * FROM {table}').fetchall()


def test_migration_does_not_publish_personal_preview_archive(tmp_path):
    path = str(tmp_path / 'candidate.db')
    registry = VacancyRegistry(path)
    profile(registry)
    registry.candidates.register_vacancy(
        vacancy_id='preview_1',
        title='Private preview',
        company=None,
        summary=None,
        post_link='https://t.me/private_preview/1',
        apply_link=None,
        published_at=None,
        go_visible=False,
    )
    result = import_legacy(path, dry_run=False)
    assert result['candidate_admitted'] == 0
    assert registry.list_vacancies() == []


def test_analysis_cache_requires_unchanged_text_and_versions(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
    ingest(registry, raw_text='original source')
    assert registry.reusable_decision('jobs_1', 'original source') is not None
    assert registry.reusable_decision('jobs_1', 'edited source') is None
    with registry._connect() as c:
        c.execute("UPDATE registry_analyses SET prompt_version='old'")
    assert registry.reusable_decision('jobs_1', 'original source') is None


def test_readiness_starts_after_analysis_confirmation(tmp_path, monkeypatch):
    registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
    profile(registry)
    monkeypatch.setattr(
        'tg_vacancy_bot.registry._now', lambda: '2026-01-01T08:00:00+00:00'
    )
    first = ingest(registry, decision=None, unavailable_reason='daily_limit')
    assert first.eligible_at is None
    monkeypatch.setattr(
        'tg_vacancy_bot.registry._now', lambda: '2026-01-01T09:30:00+00:00'
    )
    ready = ingest(registry)
    assert ready.discovered_at == first.discovered_at
    assert ready.eligible_at == '2026-01-01T09:30:00+00:00'
    assert registry.list_personal_matches(1)[0].eligible_at == ready.eligible_at


def test_proven_group_reposts_have_one_match_and_two_sources(tmp_path, monkeypatch):
    from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
    from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore

    async def run():
        monkeypatch.setattr(
            'tg_vacancy_bot.pipeline.processor.asyncio.sleep', AsyncMock()
        )
        registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
        profile(registry)
        value = decision('Go')
        value = replace(
            value,
            analysis=replace(
                value.analysis, apply_link='https://employer.example/jobs/1'
            ),
        )
        analyzer, sheets, notify = (
            AsyncMock(return_value=value),
            AsyncMock(return_value=True),
            AsyncMock(return_value=True),
        )
        state = JsonlDedupeState(tmp_path / 'state.jsonl', 30)
        groups = VacancyGroupStore(str(tmp_path / 'groups.db'), 30)
        processor = VacancyProcessor(
            lambda text: True,
            analyzer,
            sheets,
            dedupe_state=state,
            notify_vacancy=notify,
            group_store=groups,
            registry=registry,
        )
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second = datetime(2026, 1, 2, tzinfo=timezone.utc)
        assert await processor.process_message(
            'Hiring Go original',
            'Hiring Go original',
            'https://t.me/jobs/1',
            first,
            'jobs',
        )
        assert await processor.process_message(
            'Hiring Go repost',
            'Hiring Go repost',
            'https://t.me/other/2',
            second,
            'other',
        )
        assert len(registry.list_personal_matches(1)) == 1
        match = registry.list_personal_matches(1)[0]
        assert match.post_link == 'https://t.me/jobs/1'
        assert match.published_at == first.isoformat()
        assert match.vacancy_id == 'jobs_1'
        canonical_card = registry.candidates.get_vacancy(match.callback_key)
        assert canonical_card.post_link == match.post_link
        assert canonical_card.published_at == match.published_at
        with registry._connect() as c:
            assert c.execute('SELECT COUNT(*) FROM registry_sources').fetchone()[0] == 2
            assert (
                c.execute('SELECT COUNT(*) FROM registry_vacancies').fetchone()[0] == 1
            )
        sheets.assert_awaited_once()
        notify.assert_awaited_once()
        assert {'jobs_1', 'other_2'} <= state.exported_ids
        # TTL text dedupe skips analysis but still attaches a proven group source.
        assert not await processor.process_message(
            'Hiring Go original',
            'Hiring Go original',
            'https://t.me/third/3',
            second,
            'third',
        )
        assert analyzer.await_count == 2
        assert len(registry.list_personal_matches(1)) == 1
        with registry._connect() as c:
            assert c.execute('SELECT COUNT(*) FROM registry_sources').fetchone()[0] == 3
        notify.assert_awaited_once()

    asyncio.run(run())


def test_importer_rejects_private_runs_even_with_saved_status(tmp_path):
    candidate = str(tmp_path / 'candidate.db')
    premium = str(tmp_path / 'premium.db')
    search = str(tmp_path / 'search.db')
    with sqlite3.connect(premium) as c:
        c.execute('CREATE TABLE premium_search_runs(search_run_id TEXT,owner TEXT)')
        c.execute("INSERT INTO premium_search_runs VALUES ('private','candidate:1')")
        c.execute('CREATE TABLE premium_search_results(search_run_id TEXT,status TEXT)')
        c.execute("INSERT INTO premium_search_results VALUES ('private','saved')")
    with sqlite3.connect(search) as c:
        c.execute('CREATE TABLE search_items(scope TEXT,publication_status TEXT)')
        c.execute("INSERT INTO search_items VALUES ('candidate:1','published')")
    result = import_legacy(
        candidate, premium_path=premium, search_path=search, dry_run=False
    )
    assert result['premium_admitted'] == result['search_admitted'] == 0
    assert result['counts']['registry_vacancies'] == 0


@pytest.mark.parametrize('initial', ['review', 'unavailable'])
def test_previously_reviewed_source_moves_to_proven_canonical(tmp_path, initial):
    from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore

    async def run():
        registry = VacancyRegistry(str(tmp_path / 'candidate.db'))
        profile(registry)
        value = decision('Go')
        value = replace(
            value,
            analysis=replace(
                value.analysis, apply_link='https://employer.example/job/1'
            ),
        )
        registry.ingest(
            vacancy_id='repost_2',
            post_link='https://t.me/repost/2',
            decision=replace(value, needs_review=True) if initial == 'review' else None,
        )
        legacy = None
        if initial == 'review':
            with registry._connect() as c:
                c.execute(
                    "UPDATE vacancies SET callback_key='historical-repost' WHERE vacancy_id='repost_2'"
                )
            registry.candidates.save_vacancy(1, 'historical-repost')
            legacy = registry.candidates.get_vacancy('historical-repost')
        groups = VacancyGroupStore(str(tmp_path / 'groups.db'), 30)
        sheets, notify = AsyncMock(return_value=True), AsyncMock(return_value=True)
        processor = VacancyProcessor(
            lambda text: True,
            AsyncMock(),
            sheets,
            notify_vacancy=notify,
            group_store=groups,
            registry=registry,
        )
        for source, message_id in [('canonical', 1), ('repost', 2)]:
            await processor.persist_analyzed_message(
                raw_text=f'Hiring Go {source}',
                post_link=f'https://t.me/{source}/{message_id}',
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                channel_name=source,
                analysis_result=value.analysis,
                universal_decision=value,
                publish=True,
            )
        assert [m.vacancy_id for m in registry.list_personal_matches(1)] == [
            'canonical_1'
        ]
        assert registry.get('repost_2').eligible_at is None
        with registry._connect() as c:
            assert {
                row[0] for row in c.execute('SELECT vacancy_id FROM registry_sources')
            } == {'canonical_1'}
            assert c.execute('SELECT COUNT(*) FROM registry_sources').fetchone()[0] == 2
        if legacy:
            assert registry.candidates.get_vacancy('historical-repost') == legacy
            assert [
                v.vacancy_id for v in registry.candidates.list_for_user(1, 'saved')
            ] == ['repost_2']
        sheets.assert_awaited_once()
        notify.assert_awaited_once()

    asyncio.run(run())


def test_alias_delivery_history_blocks_canonical_resend_without_deleting_history(
    tmp_path,
):
    from tg_vacancy_bot.telegram.candidate_delivery_worker import PersonalDeliveryStore
    from tg_vacancy_bot.telegram.candidate_delivery import delivery_key

    path = str(tmp_path / 'candidate.db')
    registry = VacancyRegistry(path)
    queue = PersonalDeliveryStore(path)
    registry.ingest(
        vacancy_id='alias', post_link='https://t.me/alias/1', decision=decision()
    )
    registry.ingest(
        vacancy_id='canonical',
        post_link='https://t.me/canonical/1',
        decision=decision(),
    )
    now = datetime.now(timezone.utc)
    token, _ = queue.reserve(1, ('alias',), now)
    queue.transition(token, 'reserved', 'sending')
    queue.transition(token, 'sending', 'sent', message_id=7)
    reserved_token, _ = queue.reserve(2, ('alias',), now)
    canonical_token, _ = queue.reserve(1, ('canonical',), now)
    registry.ingest(
        vacancy_id='canonical',
        proven_canonical_id='canonical',
        external_id='alias',
        post_link='https://t.me/alias/1',
        decision=decision(),
    )
    assert delivery_key(1, 'canonical') in queue.excluded_keys(1, now)
    assert queue.reserve(1, ('canonical',), now)[1] == ()
    assert queue.reserve(1, ('alias',), now)[1] == ()
    assert not queue.transition(reserved_token, 'reserved', 'sending')
    assert not queue.transition(canonical_token, 'reserved', 'sending')
    assert queue.reserve(2, ('canonical',), now)[1] == ('canonical',)
    with queue.connect() as c:
        row = c.execute(
            "SELECT state,message_id FROM candidate_personal_deliveries WHERE telegram_user_id=1 AND vacancy_id='alias'"
        ).fetchone()
        assert tuple(row) == ('sent', 7)
