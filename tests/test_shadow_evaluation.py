"""Synthetic payloads test evaluator wiring, never the quality of Mistral."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tg_vacancy_bot.llm.schemas import EXPECTED_FIELDS
from tg_vacancy_bot.llm.universal import (
    AnalysisUnavailable,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    _validate,
)
from tg_vacancy_bot.shadow_evaluation import evaluate

CASES = json.loads(
    (Path(__file__).parent / 'fixtures/catalog_evaluation.json').read_text()
)


def labeled_payload(case):
    analysis = dict.fromkeys(EXPECTED_FIELDS)
    analysis.update(
        is_match=case['is_vacancy'],
        primary_roles=[],
        specializations=[],
        required_stack=case['required_stack'],
        preferred_stack=case['preferred_stack'],
    )
    return dict(
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        is_vacancy=case['is_vacancy'],
        needs_review=case['needs_review'],
        confidence=90,
        reason_code='synthetic_label',
        analysis=analysis,
        classifications=[
            dict(direction_id=d, specialization_id=s, role_id=r, confidence=90)
            for d, s, r in case['roles']
        ],
    )


def test_offline_default_does_not_claim_model_quality():
    result = asyncio.run(evaluate(CASES))
    assert result['calls'] == result['exports'] == result['notifications'] == 0
    assert result['classification']['measured'] == 0
    assert result['classification']['not_evaluated'] == 30
    assert not result['live_model_quality_measured']
    assert result['prefilter']['fn'] == 0
    assert result['prefilter']['fp'] >= 1  # Ambiguous posts need the next stage.


def test_synthetic_replay_exercises_classification_and_matching_separately():
    predictions = {case['id']: labeled_payload(case) for case in CASES}
    result = asyncio.run(evaluate(CASES, predictions=predictions))
    assert result['classification']['roles_exact'] == 30
    assert result['classification']['review_correct'] == 30
    assert result['classification']['go_projection']['fp'] == 0
    assert result['classification']['go_projection']['fn'] == 0
    assert result['matching']['checks'] > 30
    assert result['matching']['fp'] == result['matching']['fn'] == 0
    assert result['calls'] == 0
    assert not result['live_model_quality_measured']


def test_replay_reports_invalid_and_incorrect_predictions():
    case = CASES[0]
    wrong = labeled_payload(CASES[3])
    result = asyncio.run(
        evaluate([case, CASES[1]], predictions={case['id']: wrong, CASES[1]['id']: {}})
    )
    assert result['classification']['invalid'] == 1
    assert result['matching']['checks'] == 2


def test_live_budget_counts_errors_and_has_no_outputs():
    analyzer = AsyncMock(side_effect=AnalysisUnavailable('daily_limit'))
    result = asyncio.run(evaluate(CASES, analyzer=analyzer, max_calls=2))
    assert analyzer.await_count == result['calls'] == 2
    assert result['classification']['unavailable'] == 2
    assert result['exports'] == result['notifications'] == 0
    assert not result['live_model_quality_measured']


def test_zero_budget_prevents_even_injected_api_call():
    analyzer = AsyncMock(return_value=_validate(labeled_payload(CASES[0])))
    result = asyncio.run(evaluate(CASES, analyzer=analyzer))
    analyzer.assert_not_called()
    assert result['classification']['measured'] == 0


def test_evaluation_limits_rejected():
    with pytest.raises(ValueError):
        asyncio.run(evaluate(CASES, max_examples=0))
    with pytest.raises(ValueError):
        asyncio.run(evaluate(CASES, max_calls=1001))
