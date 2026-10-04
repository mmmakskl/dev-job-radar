import json
from pathlib import Path

from tg_vacancy_bot.candidate_catalog import CATALOG
from tg_vacancy_bot.premium_search.templates import (
    _TEMPLATES,
    compose_query,
    get_template,
    list_templates,
)


def test_templates_cover_catalog_roles_and_role_stack_pairs():
    expected_roles = set()
    expected_stacks = set()
    for direction in CATALOG['directions']:
        for specialization in direction['specializations']:
            for role in specialization['roles']:
                expected_roles.add(role['id'])
                expected_stacks.update((role['id'], stack) for stack in role['stacks'])
    actual_roles = {item.role_id for item in _TEMPLATES if item.kind == 'role'}
    actual_stacks = {
        (item.role_id, item.stack_id) for item in _TEMPLATES if item.kind == 'stack'
    }
    assert not expected_roles - actual_roles, sorted(expected_roles - actual_roles)
    assert not expected_stacks - actual_stacks, sorted(expected_stacks - actual_stacks)
    assert not actual_roles - expected_roles
    assert not actual_stacks - expected_stacks
    assert len(actual_roles) == 29
    assert len(actual_stacks) == 133
    assert len({item.id for item in _TEMPLATES}) == len(_TEMPLATES)
    for item in _TEMPLATES:
        assert item.label and item.main and item.ru and item.en and item.synonyms


def test_template_ids_lookup_and_profile_filtering():
    profile = {
        'role_id': 'backend_developer',
        'preferences': {'stacks': ['go', 'python']},
    }
    templates = list_templates(profile)
    assert [item.id for item in templates] == [
        'role:backend_developer',
        'stack:backend_developer:go',
        'stack:backend_developer:python',
    ]
    assert get_template('role:backend_developer') is templates[0]
    assert get_template('stack:backend_developer:go').stack_id == 'go'
    assert get_template('unknown') is None
    assert [item.id for item in list_templates({'role_id': 'backend_developer'})] == [
        'role:backend_developer'
    ]


def test_compose_query_language_parameters_and_length_validation():
    profile = {'role_id': 'backend_developer', 'preferences': {'stacks': ['go']}}
    assert 'вакансии' in compose_query('role:backend_developer', profile, language='ru')
    assert 'jobs' in compose_query('role:backend_developer', profile, language='en')
    query = compose_query(
        'stack:backend_developer:go',
        profile,
        grade='Senior',
        work_format='remote',
        location='Europe',
    )
    assert query.endswith('Senior remote Europe')
    try:
        compose_query(
            'role:backend_developer',
            profile,
            location='somewhere ' * 30,
        )
    except ValueError as error:
        assert '3 до 160' in str(error)
    else:
        raise AssertionError('expected oversized query to raise ValueError')


def test_onec_templates_cover_ecosystem_and_do_not_require_stack_selection():
    profile = {'role_id': 'onec_developer', 'preferences': {'stacks': []}}
    templates = list_templates(profile)
    assert templates[0].id == 'role:onec_developer'
    assert {item.stack_id for item in templates if item.kind == 'stack'} == {
        '1c_enterprise',
        'bsl',
        'erp',
        'zup',
        'accounting',
        'trade_management',
    }
    assert '1С:Предприятие' in templates[0].synonyms
    assert 'BSL' in templates[0].synonyms
    assert 'ЗУП' in templates[0].synonyms
    assert 'Управление торговлей' in templates[0].synonyms
    assert 'bsl' in compose_query('stack:onec_developer:bsl', profile).casefold()


def test_versioned_data_file_matches_template_count():
    path = (
        Path(__file__).parents[1]
        / 'src/tg_vacancy_bot/premium_search/data/query_templates.v1.json'
    )
    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload['version'] == 1
    assert payload['catalog_version'] == 2
    assert len(payload['templates']) == len(_TEMPLATES)
