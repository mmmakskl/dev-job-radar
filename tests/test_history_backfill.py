"""Registry-only history retains every catalog direction without Go exports."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_vacancy_bot.llm.universal import AnalysisUnavailable, PROMPT_VERSION, _validate
from tg_vacancy_bot.candidate_catalog import CATALOG
from tg_vacancy_bot.pipeline.history_backfill import ingest_registry_history_post
from tg_vacancy_bot.pipeline.history_backfill import BackfillResult
from tg_vacancy_bot.pipeline.prefilter import universal_prefilter
from tg_vacancy_bot.registry import VacancyRegistry
from tests.test_universal_analysis import _payload


def _writer_decision():
    payload = _payload()
    payload['classifications'][0].update(
        direction_id='content',
        specialization_id='technical_writer',
        role_id='technical_writer',
    )
    payload['analysis']['title'] = 'Технический писатель'
    return _validate(payload)


def test_non_go_history_is_visible_to_matching_profile_without_public_export(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.sqlite3'))
    registry.candidates.create_profile(
        1,
        name='Writer',
        direction_id='content',
        specialization_id='technical_writer',
        role_id='technical_writer',
        preferences={'profile_contract': 'catalog-v3', 'required_skills': ['Markdown']},
    )
    analyze = AsyncMock(return_value=_writer_decision())
    kwargs = dict(
        text='Ищем технического писателя: документация в Markdown',
        post_link='https://t.me/jobs/1',
        published_at=datetime.now(timezone.utc),
        channel_name='Jobs',
        analyze=analyze,
    )
    first = asyncio.run(ingest_registry_history_post(registry, **kwargs))
    second = asyncio.run(ingest_registry_history_post(registry, **kwargs))
    assert first.status == 'analyzed' and first.confirmed
    assert second.status == 'reused' and second.confirmed
    analyze.assert_awaited_once()
    assert [item.vacancy_id for item in registry.list_personal_matches(1)] == ['jobs_1']
    with registry._connect() as connection:
        assert (
            connection.execute('SELECT COUNT(*) FROM registry_projections').fetchone()[
                0
            ]
            == 0
        )


def test_old_catalog_analysis_is_replaced_with_current_contract(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.sqlite3'))
    old = _payload()
    old['prompt_version'] = 'catalog-classifier.v4'
    from tg_vacancy_bot.llm.universal import decision_from_dict

    registry.ingest(
        vacancy_id='jobs_2',
        post_link='https://t.me/jobs/2',
        decision=decision_from_dict(old),
        raw_text='Ищем Go backend developer',
    )
    analyze = AsyncMock(return_value=_validate(_payload()))
    result = asyncio.run(
        ingest_registry_history_post(
            registry,
            text='Ищем Go backend developer',
            post_link='https://t.me/jobs/2',
            published_at=datetime.now(timezone.utc),
            channel_name='Jobs',
            analyze=analyze,
        )
    )
    assert result.status == 'analyzed'
    assert registry.get('jobs_2').prompt_version == PROMPT_VERSION


def test_unrelated_post_is_skipped_without_api_call(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.sqlite3'))
    analyze = AsyncMock()
    result = asyncio.run(
        ingest_registry_history_post(
            registry,
            text='Привет всем!',
            post_link='https://t.me/jobs/3',
            published_at=datetime.now(timezone.utc),
            channel_name='Jobs',
            analyze=analyze,
        )
    )
    assert result.status == 'prefilter_skipped'
    analyze.assert_not_awaited()
    assert registry.get('jobs_3') is None


def test_every_catalog_role_title_passes_history_prefilter():
    labels = [
        role['label']
        for direction in CATALOG['directions']
        for specialization in direction['specializations']
        for role in specialization['roles']
    ]
    assert len(labels) == 47
    assert all(universal_prefilter(label) for label in labels)


def test_quota_exhaustion_stops_without_marking_remaining_post(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'candidate.sqlite3'))
    with pytest.raises(AnalysisUnavailable, match='daily_limit'):
        asyncio.run(
            ingest_registry_history_post(
                registry,
                text='Ищем технического писателя',
                post_link='https://t.me/jobs/4',
                published_at=datetime.now(timezone.utc),
                channel_name='Jobs',
                analyze=AsyncMock(side_effect=AnalysisUnavailable('daily_limit')),
            )
        )
    assert registry.get('jobs_4') is None


def test_registry_history_scans_every_source_without_google_or_publication(
    tmp_path, monkeypatch
):
    from scripts import parse_history as history

    class FakeClient:
        start = AsyncMock()
        get_dialogs = AsyncMock()

        async def iter_messages(self, source, **_kwargs):
            yield SimpleNamespace(date=datetime.now(timezone.utc), text=source)

    ingest = AsyncMock(return_value=BackfillResult('analyzed', confirmed=True))
    monkeypatch.setattr(history, 'client', FakeClient())
    monkeypatch.setattr(history, 'ingest_registry_history_post', ingest)
    monkeypatch.setattr(
        history, 'get_message_link', lambda message: 'https://t.me/jobs/1'
    )
    monkeypatch.setattr(history, 'get_message_channel_name', lambda message: 'Jobs')
    monkeypatch.setattr(
        history,
        'get_existing_links',
        AsyncMock(side_effect=AssertionError('Google called')),
    )
    monkeypatch.setattr(history.config, 'TARGET_CHANNELS', ['source_1', 'source_2'])
    monkeypatch.setattr(
        history.config, 'CANDIDATE_BOT_DB_PATH', str(tmp_path / 'candidate.sqlite3')
    )
    monkeypatch.setattr(history.config, 'LEGACY_GO_ANALYSIS', False)
    monkeypatch.setattr(
        history.config, 'validate_required_settings', lambda **kwargs: None
    )
    counts = asyncio.run(history.backfill_registry_history())
    assert counts['received'] == 2
    assert counts['analyzed'] == 2
    assert counts['confirmed'] == 2
    assert ingest.await_count == 2
