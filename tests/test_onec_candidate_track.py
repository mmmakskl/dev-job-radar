import asyncio
from dataclasses import replace

from tg_vacancy_bot.candidate_catalog import CATALOG, validate_profile_path
from tg_vacancy_bot.llm.universal import _catalog_prompt
from tg_vacancy_bot.pipeline.prefilter import (
    candidate_profile_reasons,
    universal_prefilter,
)
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore
from tests.test_candidate_bot import FakeBotApi, message
from tests.test_candidate_integration import setup


def test_onec_catalog_keeps_existing_ids_and_resolves_written_variants():
    from tg_vacancy_bot.candidate_catalog import resolve_alias

    direction = next(item for item in CATALOG['directions'] if item['id'] == 'onec')
    assert {
        role['id'] for spec in direction['specializations'] for role in spec['roles']
    } == {'onec_developer', 'onec_analyst_consultant', 'onec_administrator'}
    for alias in ('1С', '1C', '1С:Предприятие', 'BSL'):
        assert resolve_alias(alias) == ('direction', 'onec')
    validate_profile_path('onec', 'onec_development', 'onec_developer', [])
    assert resolve_alias('backend_developer') == ('role', 'backend_developer')


def test_onec_prefilter_and_classifier_instructions_cover_real_hiring_only():
    assert universal_prefilter('Ищем разработчика 1С:Предприятие, BSL.')
    assert not universal_prefilter('Курс разработчика 1С:Предприятие. Записывайтесь!')
    assert not universal_prefilter('Продажа лицензий 1С:Предприятие, цены в личку.')
    resume = 'Резюме. Опыт работы разработчиком 1С, желаемая зарплата 250 000.'
    assert candidate_profile_reasons(resume)
    assert not universal_prefilter(resume)

    prompt = _catalog_prompt(CATALOG)
    for phrase in ('1С:Предприятие', 'BSL', 'license sales', 'resumes/CVs'):
        assert phrase in prompt


def test_onec_profile_wizard_skips_primary_programming_language(tmp_path):
    values = {
        'direction_id': 'onec',
        'specialization_id': 'onec_development',
        'role_id': 'onec_developer',
        'preferences': {},
    }
    assert 'primary_language' not in CandidateBot._basic_steps(values)
    draft = {'wizard': 'basic', 'step': 'role_id', 'values': values}
    bot = CandidateBot(
        FakeBotApi(), CandidateStore(str(tmp_path / 'candidate.sqlite3')), {7}
    )
    bot._advance(draft)
    assert draft['step'] == 'seniority'


def test_onec_match_appears_in_personal_feed_with_source_publication_time(tmp_path):
    store, registry, _, decision = setup(tmp_path)
    profile = store.create_profile(
        7,
        name='Мой 1С профиль',
        direction_id='onec',
        specialization_id='onec_development',
        role_id='onec_developer',
    )
    onec_decision = replace(
        decision,
        classifications=(
            {
                'direction_id': 'onec',
                'specialization_id': 'onec_development',
                'role_id': 'onec_developer',
                'confidence': 96,
            },
        ),
    )
    registry.ingest(
        vacancy_id='onec-job-1',
        post_link='https://t.me/onec_jobs/1',
        decision=onec_decision,
        published_at='2026-10-04T08:00:00+00:00',
    )
    api = FakeBotApi()
    bot = CandidateBot(api, store, {7}, registry=registry)

    asyncio.run(bot.handle_update(message(7, 'Для меня')))

    assert len(api.messages) == 1
    assert profile.name in api.messages[0][1]
    assert '04.10.2026' in api.messages[0][1]
