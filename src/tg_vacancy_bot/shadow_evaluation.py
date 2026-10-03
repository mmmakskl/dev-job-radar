"""Bounded classification evaluation; deliberately has no projection or sender API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Awaitable, Callable

from tg_vacancy_bot.llm.universal import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    AnalysisUnavailable,
    UniversalDecision,
    _validate,
    go_projection_accepted,
)
from tg_vacancy_bot.pipeline.prefilter import universal_prefilter
from tg_vacancy_bot.profile_matcher import match_profile
from tg_vacancy_bot.telegram.candidate_store import CandidateProfile

Analyzer = Callable[[str], Awaitable[UniversalDecision]]


def _confusion(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(expected and actual for expected, actual in pairs)
    fp = sum(not expected and actual for expected, actual in pairs)
    fn = sum(expected and not actual for expected, actual in pairs)
    tn = sum(not expected and not actual for expected, actual in pairs)
    return {
        'tp': tp,
        'fp': fp,
        'fn': fn,
        'tn': tn,
        'precision': tp / (tp + fp) if tp + fp else None,
        'recall': tp / (tp + fn) if tp + fn else None,
    }


def decision_payload(decision: UniversalDecision) -> dict:
    """Encode only the public versioned contract for a reproducible replay."""
    payload = asdict(decision)
    payload['classifications'] = list(payload['classifications'])
    return {
        'schema_version': SCHEMA_VERSION,
        'prompt_version': PROMPT_VERSION,
        **payload,
    }


async def evaluate(
    cases: list[dict],
    *,
    predictions: dict[str, dict] | None = None,
    analyzer: Analyzer | None = None,
    max_examples: int = 30,
    max_calls: int = 0,
) -> dict:
    """Score prefilter independently; classify only replayed or explicitly live input.

    A caller that supplies an analyzer must also grant a positive run budget.
    The production analyzer separately claims the shared UTC-day Mistral budget.
    No database containing vacancies or deliveries is opened by this module.
    """
    if not 1 <= max_examples <= 1000 or not 0 <= max_calls <= 1000:
        raise ValueError(
            'Evaluation limits must be bounded to 1..1000 examples, 0..1000 calls'
        )
    if analyzer is not None and predictions is not None:
        raise ValueError('Choose predictions or a live analyzer')
    selected = cases[:max_examples]
    ids = [case['id'] for case in selected]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate corpus IDs')
    prefilter_pairs, vacancy_pairs, matching_pairs, go_pairs = [], [], [], []
    exact_roles, role_total, review_correct, measured = 0, 0, 0, 0
    calls, unavailable, missing, invalid = 0, 0, 0, 0
    rows = []
    for case in selected:
        gate = universal_prefilter(case['text'])
        prefilter_pairs.append((bool(case['is_vacancy']), gate))
        decision = None
        row = {'id': case['id'], 'prefilter': gate}
        if predictions is not None and case['id'] in predictions:
            try:
                decision = _validate(predictions[case['id']])
            except (ValueError, TypeError, KeyError):
                invalid += 1
                row['analysis'] = 'invalid_schema'
        elif analyzer is not None and gate and calls < max_calls:
            calls += 1
            try:
                decision = await analyzer(case['text'])
            except AnalysisUnavailable:
                unavailable += 1
                row['analysis'] = 'unavailable'
        else:
            missing += 1
            row['analysis'] = 'not_evaluated'
        if decision is not None:
            measured += 1
            row['analysis'] = 'review' if decision.needs_review else 'available'
            vacancy_pairs.append((case['is_vacancy'], decision.is_vacancy))
            go_pairs.append((case['expected_go'], go_projection_accepted(decision)))
            actual_roles = {
                (item['direction_id'], item['specialization_id'], item['role_id'])
                for item in decision.classifications
            }
            expected_roles = {tuple(role) for role in case['roles']}
            exact_roles += actual_roles == expected_roles
            role_total += 1
            review_correct += decision.needs_review == case['needs_review']
            row['roles_exact'] = actual_roles == expected_roles
            for index, expectation in enumerate(case.get('profiles', [])):
                role = expectation['role']
                profile = CandidateProfile(
                    f'eval-{index}',
                    0,
                    'evaluation',
                    *role,
                    expectation.get('preferences', {}),
                    True,
                    1,
                )
                result = match_profile(decision, profile)
                matching_pairs.append(
                    (expectation['status'] == 'match', result.status == 'match')
                )
                row.setdefault('matches', []).append(
                    {
                        'expected': expectation['status'],
                        'actual': result.status,
                    }
                )
        rows.append(row)
    return {
        'schema_version': SCHEMA_VERSION,
        'prompt_version': PROMPT_VERSION,
        'mode': (
            'live_shadow'
            if analyzer is not None
            else ('prediction_replay' if predictions is not None else 'prefilter_only')
        ),
        'live_model_quality_measured': analyzer is not None and measured > 0,
        'examples': len(selected),
        'calls': calls,
        'exports': 0,
        'notifications': 0,
        'prefilter': _confusion(prefilter_pairs),
        'classification': {
            'measured': measured,
            'not_evaluated': missing,
            'unavailable': unavailable,
            'invalid': invalid,
            'vacancy': _confusion(vacancy_pairs),
            'go_projection': _confusion(go_pairs),
            'roles_exact': exact_roles,
            'roles_total': role_total,
            'review_correct': review_correct,
        },
        'matching': {**_confusion(matching_pairs), 'checks': len(matching_pairs)},
        'cases': rows,
    }
