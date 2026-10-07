"""Current candidate catalog, profile migration and raw-text matching."""

import asyncio
import json
from dataclasses import replace

from tg_vacancy_bot.candidate_catalog import CATALOG, role_path
from tg_vacancy_bot.llm.universal import (
    LEGACY_PROMPT_VERSION,
    PROMPT_VERSION,
    _validate,
    decision_from_dict,
)
from tg_vacancy_bot.profile_matcher import match_profile
from tg_vacancy_bot.premium_search.analyzer import adapt_universal
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.candidate_store import CandidateProfile, CandidateStore
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tests.test_candidate_bot import FakeBotApi, message
from tests.test_universal_analysis import _payload

EXPECTED_DIRECTIONS = [
    'Контент',
    'Информационная безопасность',
    'Геймдев',
    'Поддержка и клиентский сервис',
    'Тестирование',
    'HR',
    'Аналитика',
    'Руководство и архитектура',
    'Дизайн',
    'Администрирование',
    'Маркетинг',
    'Продажи и развитие бизнеса',
    'Данные и AI',
    'Менеджмент',
    'Разработка',
]
EXPECTED_ROLES = [
    ['Технический писатель', 'Копирайтер', 'Контент-менеджер', 'UGC-креатор'],
    ['Специалист по ИБ'],
    ['Разработчик игр', 'Геймдизайнер'],
    ['Customer Succes менеджер', 'Инженер технической поддержки'],
    ['Инженер по тестированию (QA Engineer)'],
    ['HR-менеджер', 'Рекрутер'],
    ['Системный аналитик', 'Бизнес-аналитик', 'Дата-аналитик'],
    ['Руководитель разработки', 'Архитектор решений и ПО'],
    [
        'Продуктовый дизайнер',
        'UI/UX дизайнер',
        'Графический дизайнер',
        'Веб-дизайнер',
        'Моушн-Дизайнер',
        '3D-художник',
    ],
    ['DevOps / SRE инженер', 'Системный администратор'],
    ['Маркетолог', 'SEO-специалист', 'SMM-специалист', 'Таргетоолог', 'PR-специалист'],
    ['Менеджер по B2B-продажам', 'Аккаунт-менеджер', 'Специалист по развитию продаж'],
    ['Data Scientist', 'ML-инженер', 'Дата-инженер', 'AI-инженер'],
    ['Проджект-менеджер', 'Продакт-менеджер', 'Деливери-менеджер'],
    [
        'Бэкенд разработчик',
        'Фронтенд разработчик',
        'Фулстек разработчик',
        'Android разработчик',
        'IOS разработчик',
        '1С разработчик',
        'ERP/CRM разработчик',
    ],
]


def test_public_catalog_is_exact_and_each_role_has_skills():
    assert CATALOG['version'] == 3
    assert [
        direction['label'] for direction in CATALOG['directions']
    ] == EXPECTED_DIRECTIONS
    assert [
        [
            role['label']
            for spec in direction['specializations']
            for role in spec['roles']
        ]
        for direction in CATALOG['directions']
    ] == EXPECTED_ROLES
    roles = [
        role
        for direction in CATALOG['directions']
        for spec in direction['specializations']
        for role in spec['roles']
    ]
    assert len(roles) == 47
    assert len({role['id'] for role in roles}) == 47
    assert all(role['label'] and role['stacks'] for role in roles)
    assert role_path('onec_developer')[0] == 'development'
    assert role_path('qa_engineer')[0] == 'qa'


def _current_profile(**preferences):
    return CandidateProfile(
        'p1',
        1,
        'Backend',
        'development',
        'backend',
        'backend_developer',
        {'profile_contract': 'catalog-v3', **preferences},
        True,
        1,
    )


def _decision():
    payload = _payload()
    payload['analysis'].update(grade_from='Middle', grade_to='Senior')
    return _validate(payload)


def test_role_grade_any_skill_and_excluded_phrase_use_original_text():
    decision = _decision()
    profile = _current_profile(
        seniority=['middle', 'senior'],
        required_skills=['Rust', 'Go'],
        excluded_skills=['night shift'],
        formats=['office'],
        geography=['Germany'],
    )
    assert (
        match_profile(
            decision, profile, source_text='Ищем Go backend developer, remote.'
        ).status
        == 'match'
    )
    assert (
        match_profile(
            decision, profile, source_text='Ищем Go backend developer, night shift.'
        ).reason_code
        == 'excluded_word'
    )
    assert (
        match_profile(
            decision, profile, source_text='Ищем Golang backend developer.'
        ).reason_code
        == 'skill_missing'
    )
    assert (
        match_profile(
            decision,
            replace(profile, role_id='frontend_developer'),
            source_text='Go backend developer',
        ).reason_code
        == 'role_conflict'
    )
    assert (
        match_profile(
            decision,
            replace(
                profile, preferences={**profile.preferences, 'seniority': ['junior']}
            ),
            source_text='Go backend developer',
        ).reason_code
        == 'grade_conflict'
    )


def test_explicit_title_grade_is_used_when_grade_fields_are_empty():
    decision = _decision()
    profile = _current_profile(seniority=['middle', 'senior'], required_skills=['go'])
    unknown = replace(
        decision,
        analysis=replace(
            decision.analysis,
            grade_from='Не указано',
            grade_to='Не указано',
            title='Senior Backend Go разработчик',
        ),
    )
    assert match_profile(unknown, profile, source_text='Go developer').status == 'match'
    junior = replace(
        unknown, analysis=replace(unknown.analysis, title='Junior Backend Go')
    )
    assert (
        match_profile(junior, profile, source_text='Go developer').reason_code
        == 'grade_conflict'
    )
    untitled = replace(unknown, analysis=replace(unknown.analysis, title='Backend Go'))
    assert (
        match_profile(untitled, profile, source_text='Senior Go developer').reason_code
        == 'grade_missing'
    )


def test_premium_catalog_uses_original_text_and_current_decision():
    decision = _decision()
    profile = _current_profile(required_skills=['Go'], excluded_skills=['agency'])
    snapshot = profile.__dict__
    assert adapt_universal(
        decision, snapshot, source_text='Go backend developer'
    ).is_track_match
    assert not adapt_universal(
        decision, snapshot, source_text='Go backend developer, agency'
    ).is_track_match
    assert not adapt_universal(decision, snapshot).is_track_match
    assert not adapt_universal(
        replace(decision, prompt_version=LEGACY_PROMPT_VERSION),
        snapshot,
        source_text='Go backend developer',
    ).is_track_match


def test_old_profiles_are_migrated_or_require_role_selection(tmp_path):
    path = str(tmp_path / 'candidate.sqlite3')
    store = CandidateStore(path)
    exact = store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'stacks': ['go']},
    )
    unresolved = store.create_profile(
        1,
        name='Manual QA',
        direction_id='qa',
        specialization_id='manual_qa',
        role_id='manual_qa_engineer',
    )
    onec = store.create_profile(
        1,
        name='1C',
        direction_id='onec',
        specialization_id='onec_development',
        role_id='onec_developer',
        preferences={'stacks': ['bsl'], 'delivery_mode': 'immediate'},
    )
    old_go_versions = len(store.get_profile_versions(1, exact.profile_id))
    old_qa_versions = len(store.get_profile_versions(1, unresolved.profile_id))
    with store._connect() as connection:
        connection.execute('PRAGMA user_version = 4')
    migrated = CandidateStore(path)
    go = migrated.get_profile(1, exact.profile_id)
    qa = migrated.get_profile(1, unresolved.profile_id)
    migrated_onec = migrated.get_profile(1, onec.profile_id)
    assert go.preferences['profile_contract'] == 'catalog-v3'
    assert go.preferences['required_skills'] == ['go']
    assert (
        len(migrated.get_profile_versions(1, exact.profile_id)) == old_go_versions + 1
    )
    assert qa.preferences['needs_role_selection'] is True
    assert not qa.is_active
    assert migrated.activate_profile(1, qa.profile_id) is None
    assert len(migrated.get_profile_versions(1, qa.profile_id)) == old_qa_versions + 1
    assert migrated_onec.direction_id == 'development'
    assert migrated_onec.preferences['delivery_mode'] == 'manual'


def test_registry_requires_current_analysis_and_source_text(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'registry.sqlite3'))
    registry.candidates.create_profile(
        1,
        name='Backend',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={
            'profile_contract': 'catalog-v3',
            'required_skills': ['Go'],
            'delivery_mode': 'manual',
        },
    )
    decision = _decision()
    registry.ingest(
        vacancy_id='jobs_1',
        post_link='https://t.me/jobs/1',
        decision=decision,
    )
    assert registry.list_personal_matches(1) == []
    registry.ingest(
        vacancy_id='jobs_1',
        post_link='https://t.me/jobs/1',
        decision=decision,
        raw_text='Ищем Go backend developer',
    )
    assert [item.vacancy_id for item in registry.list_personal_matches(1)] == ['jobs_1']

    old_payload = _payload()
    old_payload['prompt_version'] = LEGACY_PROMPT_VERSION
    old_payload['classifications'][0]['role_id'] = 'api_developer'
    old = decision_from_dict(old_payload)
    assert old.prompt_version == LEGACY_PROMPT_VERSION
    registry.ingest(
        vacancy_id='jobs_2',
        post_link='https://t.me/jobs/2',
        decision=old,
        raw_text='Ищем Go API developer',
    )
    assert registry.get('jobs_2').prompt_version == LEGACY_PROMPT_VERSION
    assert [item.vacancy_id for item in registry.list_personal_matches(1)] == ['jobs_1']
    assert PROMPT_VERSION != LEGACY_PROMPT_VERSION


def test_unsupported_historical_analysis_does_not_block_personal_feed(tmp_path):
    registry = VacancyRegistry(str(tmp_path / 'registry.sqlite3'))
    registry.candidates.create_profile(
        1,
        name='Backend',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'profile_contract': 'catalog-v3', 'required_skills': ['go']},
    )
    decision = _decision()
    for number in (1, 2):
        registry.ingest(
            vacancy_id=f'jobs_{number}',
            post_link=f'https://t.me/jobs/{number}',
            decision=decision,
            raw_text='Middle Go backend developer',
        )
    with registry._connect() as connection:
        row = connection.execute(
            "SELECT analysis_id,decision_json FROM registry_analyses WHERE vacancy_id='jobs_1'"
        ).fetchone()
        payload = json.loads(row['decision_json'])
        payload['prompt_version'] = 'catalog-classifier.v3'
        connection.execute(
            'UPDATE registry_analyses SET decision_json=?,prompt_version=? WHERE analysis_id=?',
            (json.dumps(payload), payload['prompt_version'], row['analysis_id']),
        )
    assert registry.get('jobs_1').unavailable_reason == 'invalid_saved_analysis'
    assert [item.vacancy_id for item in registry.list_personal_matches(1)] == ['jobs_2']
    api = FakeBotApi()
    bot = CandidateBot(api, registry.candidates, {1}, registry=registry)
    asyncio.run(bot.handle_update(message(1, '/forme')))
    assert api.messages and 'jobs/2' in api.messages[-1][1]
