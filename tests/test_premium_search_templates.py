import json
from pathlib import Path

from tg_vacancy_bot.candidate_catalog import CATALOG
from tg_vacancy_bot.premium_search.templates import (
    _TEMPLATES,
    compose_query,
    get_template,
    list_templates,
    profile_search_queries,
)


def test_templates_cover_catalog_roles_and_role_stack_pairs():
    expected_roles = set()
    for direction in CATALOG['directions']:
        for specialization in direction['specializations']:
            for role in specialization['roles']:
                expected_roles.add(role['id'])
    assert len(expected_roles) == 47
    for role_id in expected_roles:
        templates = list_templates({'role_id': role_id, 'preferences': {}})
        assert templates and templates[0].id == f'role:{role_id}'
        assert get_template(templates[0].id) is templates[0]
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
    current = {
        'role_id': 'backend_developer',
        'preferences': {'required_skills': ['go']},
    }
    assert [item.id for item in list_templates(current)] == [
        'role:backend_developer',
        'stack:backend_developer:go',
    ]
    assert compose_query('stack:backend_developer:go', current)


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
    assert payload['catalog_version'] == 2  # archived curated templates
    assert len(payload['templates']) == len(_TEMPLATES)


def test_profile_search_uses_broad_role_aliases_without_mandatory_vacancy_word():
    analyst = {'role_id': 'systems_analyst', 'preferences': {}}
    queries = profile_search_queries(analyst, '"Системный аналитик" вакансии')
    assert 'системный аналитик' in [query.casefold() for query in queries]
    assert 'systems analyst' in [query.casefold() for query in queries]
    assert all('вакансии' not in query.casefold() for query in queries)
    assert len(queries) <= 3

    devops = {'role_id': 'devops_sre_engineer', 'preferences': {}}
    queries = profile_search_queries(devops, '"DevOps / SRE инженер" вакансии')
    assert any(query.casefold() == 'devops' for query in queries)
    assert any(query.casefold() == 'sre' for query in queries)


def test_every_catalog_role_has_short_search_queries():
    for direction in CATALOG['directions']:
        for specialization in direction['specializations']:
            for role in specialization['roles']:
                profile = {'role_id': role['id'], 'preferences': {}}
                queries = profile_search_queries(profile, role['label'])
                assert 1 <= len(queries) <= 3
                assert all(3 <= len(query) <= 160 for query in queries)


def test_custom_admin_query_remains_in_profile_search_plan():
    profile = {'role_id': 'systems_analyst', 'preferences': {}}
    queries = profile_search_queries(profile, 'аналитик SQL вакансии')
    assert queries[0] == 'аналитик SQL вакансии'
    assert len(queries) <= 3
