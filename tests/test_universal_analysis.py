import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tg_vacancy_bot.llm.universal import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    _validate,
)
from tg_vacancy_bot.llm.response_schemas import universal_response_format
from tg_vacancy_bot.pipeline.prefilter import universal_prefilter

CASES = json.loads(
    (Path(__file__).parent / 'fixtures' / 'universal_cases.json').read_text()
)


def _payload():
    from tg_vacancy_bot.llm.schemas import EXPECTED_FIELDS

    analysis = {key: None for key in EXPECTED_FIELDS}
    analysis.update(
        is_match=True,
        primary_roles=[],
        specializations=[],
        required_stack=[],
        preferred_stack=[],
    )
    return {
        'schema_version': SCHEMA_VERSION,
        'prompt_version': PROMPT_VERSION,
        'is_vacancy': True,
        'classifications': [
            {
                'direction_id': 'development',
                'specialization_id': 'backend',
                'role_id': 'backend_developer',
                'confidence': 90,
            }
        ],
        'analysis': analysis,
        'confidence': 90,
        'reason_code': 'vacancy_match',
        'needs_review': False,
    }


def test_universal_contract_and_versions_are_strict():
    result = _validate(_payload())
    assert result.analysis.is_match is True
    invalid = _payload()
    invalid['prompt_version'] = 'old'
    with pytest.raises(ValueError, match='unsupported_analysis_version'):
        _validate(invalid)


def test_universal_contract_rejects_unknown_catalog_role():
    payload = _payload()
    payload['classifications'][0]['role_id'] = 'unknown_role'
    with pytest.raises(ValueError, match='unknown_or_duplicate_role'):
        _validate(payload)


def test_universal_contract_rejects_object_primary_roles():
    payload = _payload()
    payload['analysis']['primary_roles'] = [{'role_id': 'backend_developer'}]
    with pytest.raises(ValueError, match='primary_roles'):
        _validate(payload)


def test_universal_response_schema_separates_roles_and_classifications():
    response_format = universal_response_format(SCHEMA_VERSION, PROMPT_VERSION)
    schema = response_format['json_schema']['schema']
    analysis = schema['properties']['analysis']
    classification = schema['properties']['classifications']['items']

    assert response_format['type'] == 'json_schema'
    assert response_format['json_schema']['strict'] is True
    assert schema['additionalProperties'] is False
    assert analysis['additionalProperties'] is False
    assert classification['additionalProperties'] is False
    assert set(schema['required']) == set(schema['properties'])
    assert set(analysis['required']) == set(analysis['properties'])
    assert set(classification['required']) == set(classification['properties'])
    assert analysis['properties']['primary_roles'] == {
        'type': 'array',
        'items': {'type': 'string'},
    }
    assert classification['properties']['role_id'] == {'type': 'string'}
    assert schema['properties']['schema_version']['enum'] == [SCHEMA_VERSION]
    assert schema['properties']['prompt_version']['enum'] == [PROMPT_VERSION]


def test_labeled_prefilter_report(capsys):
    false_positive = []
    false_negative = []
    for case in CASES:
        actual = universal_prefilter(case['text'])
        if actual and not case['expected']:
            false_positive.append(case['id'])
        if not actual and case['expected']:
            false_negative.append(case['id'])
    print(f'prefilter FP={false_positive}; FN={false_negative}')
    report = capsys.readouterr().out
    assert 'FP=' in report and 'FN=' in report
    assert not false_negative
    assert 'resume' not in false_positive
    assert 'course_ad' not in false_positive


def test_universal_analyzer_uses_one_sdk_attempt(monkeypatch):
    from tg_vacancy_bot.llm import mistral
    from tg_vacancy_bot.llm import universal

    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(_payload())))
            ]
        )
    )
    configured = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client = SimpleNamespace(with_options=Mock(return_value=configured))
    monkeypatch.setattr(mistral, '_get_client', lambda: client)
    monkeypatch.setattr(universal, '_claim_daily_call', lambda _limit: True)

    result = asyncio.run(universal.analyze_universal_text('Hiring backend developer'))

    assert result.analysis.is_match
    assert create.await_count == 1
    client.with_options.assert_called_once_with(max_retries=0)
    assert create.await_args.kwargs['response_format'] == universal_response_format(
        SCHEMA_VERSION, PROMPT_VERSION
    )


def test_quota_exhaustion_fails_closed_before_api(monkeypatch):
    from tg_vacancy_bot.llm import universal

    monkeypatch.setattr(universal, '_claim_daily_call', lambda _limit: False)
    with pytest.raises(universal.AnalysisUnavailable, match='daily_limit'):
        asyncio.run(universal.analyze_universal_text('Hiring backend developer'))


def test_training_benefits_do_not_hide_genuine_vacancies():
    assert universal_prefilter(
        'Ищем Go разработчика. Требования: Go, PostgreSQL. Оплачиваем обучение и курсы.'
    )
    assert universal_prefilter(
        'Hiring backend developer. Required Go. Paid courses and webinars provided.'
    )
    assert not universal_prefilter(
        'Записывайтесь на курс Go developer. Вебинар и скидка.'
    )


def test_role_title_vacancy_with_training_benefits_reaches_classifier():
    assert universal_prefilter(
        'Go Developer\nТребования: Go, PostgreSQL\nОплачиваем курсы'
    )
    assert universal_prefilter(
        'Backend engineer. Requirements: Python. Training courses provided.'
    )
    assert not universal_prefilter('Курс QA automation engineer: изучаем Python с нуля')
