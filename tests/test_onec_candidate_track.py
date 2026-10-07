"""1C remains a development role in the current candidate catalog."""

import asyncio
from dataclasses import replace

from tg_vacancy_bot.candidate_catalog import CATALOG, role_path, validate_profile_path
from tg_vacancy_bot.llm.universal import _catalog_prompt, _validate
from tg_vacancy_bot.premium_search.templates import compose_query, list_templates
from tg_vacancy_bot.pipeline.prefilter import (
    candidate_profile_reasons,
    universal_prefilter,
)
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore
from tests.test_candidate_bot import FakeBotApi, message
from tests.test_universal_analysis import _payload


def test_onec_is_a_development_role():
    assert role_path('onec_developer') == (
        'development',
        'onec_development',
        'onec_developer',
    )
    validate_profile_path(*role_path('onec_developer'), [])
    assert 'onec' not in {item['id'] for item in CATALOG['directions']}


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


def test_onec_wizard_requires_role_and_offers_direction_skills(tmp_path):
    values = {
        'direction_id': 'development',
        'specialization_id': '',
        'role_id': '',
        'preferences': {'profile_contract': 'catalog-v3'},
    }
    bot = CandidateBot(
        FakeBotApi(), CandidateStore(str(tmp_path / 'candidate.sqlite3')), {7}
    )
    assert CandidateBot._basic_steps(values) == [
        'direction_id',
        'role_id',
        'seniority',
        'required_skills',
        'excluded_skills',
        'preview',
    ]
    assert ('onec_developer', '1С разработчик') in bot._choices(
        {'step': 'role_id', 'values': values}
    )
    assert ('bsl', 'Язык 1С (BSL)') in bot._choices(
        {'step': 'required_skills', 'values': values}
    )


def test_onec_profile_template_and_personal_feed_use_exact_role(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    registry = VacancyRegistry(store.path)
    profile = store.create_profile(
        7,
        name='1С разработчик',
        direction_id='development',
        specialization_id='onec_development',
        role_id='onec_developer',
        preferences={
            'profile_contract': 'catalog-v3',
            'required_skills': ['BSL'],
            'delivery_mode': 'manual',
        },
    )
    templates = list_templates(profile)
    assert templates[0].id == 'role:onec_developer'
    assert '1с разработчик' in compose_query(templates[0].id, profile).lower()
    payload = _payload()
    payload['classifications'] = [
        {
            'direction_id': 'development',
            'specialization_id': 'onec_development',
            'role_id': 'onec_developer',
            'confidence': 96,
        }
    ]
    decision = replace(_validate(payload), confidence=96)
    registry.ingest(
        vacancy_id='onec-job-1',
        post_link='https://t.me/onec_jobs/1',
        decision=decision,
        published_at='2026-10-04T08:00:00+00:00',
        raw_text='Ищем разработчика 1С:Предприятие, BSL.',
    )
    api = FakeBotApi()
    bot = CandidateBot(api, store, {7}, registry=registry)
    asyncio.run(bot.handle_update(message(7, 'Для меня')))
    assert profile.name in api.messages[-1][1]
    assert '04.10.2026' in api.messages[-1][1]
