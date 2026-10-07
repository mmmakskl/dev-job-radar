"""Deterministic local matching of analyzed vacancies against candidate profiles."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from tg_vacancy_bot.candidate_catalog import PROFILE_CONTRACT
from tg_vacancy_bot.llm.universal import UniversalDecision
from tg_vacancy_bot.models import NOT_SPECIFIED
from tg_vacancy_bot.telegram.candidate_store import CandidateProfile

MatchStatus = Literal["match", "no_match", "review"]


@dataclass(frozen=True)
class ProfileMatch:
    status: MatchStatus
    reason_code: str
    matched_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = re.sub(r"[^\w+#.]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"(?<![\w+#])c\+\+(?![\w+#])", "cpp", value)
    value = re.sub(r"(?<![\w+#])c#(?![\w+#])", "csharp", value)
    value = " ".join(value.split())
    return {
        "golang": "go",
        "c++": "cpp",
        "c#": "csharp",
        "postgres": "postgresql",
        "k8s": "kubernetes",
        "россия": "russia",
        "казахстан": "kazakhstan",
        "беларусь": "belarus",
        "ес": "eu",
        "евросоюз": "eu",
        "european union": "eu",
        "russian": "ru",
        "русский": "ru",
        "english": "en",
        "английский": "en",
        "джуниор": "junior",
        "мидл": "middle",
        "сеньор": "senior",
        "стажер": "intern",
        "стажёр": "intern",
        "начальный": "junior",
        "начинающий": "junior",
        "средний": "middle",
        "опытный": "senior",
        "ведущий": "lead",
    }.get(value, value)


def _values(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [v for item in value if (v := _norm(item)) and v != _norm(NOT_SPECIFIED)]


def _has_any(actual: list[str], expected: list[str]) -> bool:
    return any(a == e for a in actual for e in expected)


def _contains_skill(text: str, skill: str) -> bool:
    """Match whole normalized tokens/phrases, preserving C++ and C# suffixes."""
    return (
        re.search(r"(?<![\w+#])" + re.escape(skill) + r"(?![\w+#])", text) is not None
    )


GRADE_ORDER = {
    grade: index
    for index, grade in enumerate(('intern', 'junior', 'middle', 'senior', 'lead'))
}


def _grade_range_matches(start: str, end: str, expected: list[str]) -> bool | None:
    """Return overlap of selected grade alternatives with an analyzed grade range."""
    lower, upper = _norm(start), _norm(end)
    if lower not in GRADE_ORDER and upper not in GRADE_ORDER:
        return None
    if lower not in GRADE_ORDER:
        lower = upper
    if upper not in GRADE_ORDER:
        upper = lower
    low_rank, high_rank = sorted((GRADE_ORDER[lower], GRADE_ORDER[upper]))
    return any(
        GRADE_ORDER[grade] >= low_rank and GRADE_ORDER[grade] <= high_rank
        for value in expected
        if (grade := _norm(value)) in GRADE_ORDER
    )


def match_profile(
    decision: UniversalDecision,
    profile: CandidateProfile,
    *,
    source_text: str | None = None,
) -> ProfileMatch:
    """Match one analyzed vacancy to one profile without I/O or confidence thresholds."""
    if decision.needs_review:
        return ProfileMatch(
            "review", "analysis_uncertain", missing_fields=("analysis",)
        )
    if not decision.is_vacancy:
        return ProfileMatch(
            "no_match", "not_a_vacancy", conflicting_fields=("is_vacancy",)
        )
    if profile.preferences.get('profile_contract') == PROFILE_CONTRACT:
        return _match_current(decision, profile, source_text)

    matched: list[str] = []
    missing: list[str] = []
    conflicting: list[str] = []
    directions = {item["direction_id"] for item in decision.classifications}
    if profile.direction_id in directions:
        matched.append("direction")
    elif directions:
        conflicting.append("direction")
    else:
        missing.append("direction")

    analysis = decision.analysis
    preferences = profile.preferences
    geography = _values(preferences.get("geography", []))
    checks = (
        (
            "stacks",
            _values(analysis.required_stack),
            [
                *preferences.get("stacks", []),
                *preferences.get("additional_languages", []),
                *(
                    [preferences["primary_language"]]
                    if preferences.get("primary_language")
                    else []
                ),
            ],
        ),
        ("seniority", _values([analysis.grade_from, analysis.grade_to]), None),
        ("formats", _values(analysis.work_format), None),
        (
            "geography",
            _values([analysis.hiring_geography, analysis.country, analysis.city]),
            (
                []
                if any(
                    value in {"worldwide", "world", "весь мир"} for value in geography
                )
                else None
            ),
        ),
        ("vacancy_languages", _values(analysis.vacancy_language), None),
    )
    for key, actual, explicit_expected in checks:
        expected = _values(
            explicit_expected
            if explicit_expected is not None
            else preferences.get(key, [])
        )
        if not expected:
            continue
        if key == "seniority":
            overlap = _grade_range_matches(
                analysis.grade_from, analysis.grade_to, expected
            )
            if overlap is None:
                missing.append(key)
            elif overlap:
                matched.append(key)
            else:
                conflicting.append(key)
            continue
        if not actual:
            missing.append(key)
        elif _has_any(actual, expected):
            matched.append(key)
        else:
            conflicting.append(key)

    required_skills = _values(preferences.get("required_skills", []))
    vacancy_text = " ".join(
        _values(
            [
                *analysis.required_stack,
                analysis.requirements,
                analysis.responsibilities,
                analysis.summary,
            ]
        )
    )
    for skill in required_skills:
        if _contains_skill(vacancy_text, skill):
            matched.append(f"required_skills:{skill}")
        else:
            # These text fields are analyzer output; absent evidence needs review.
            if not vacancy_text:
                missing.append("required_skills")
            else:
                conflicting.append(f"required_skills:{skill}")

    for skill in _values(preferences.get("excluded_skills", [])):
        if _contains_skill(vacancy_text, skill):
            conflicting.append(f"excluded_skills:{skill}")

    if missing:
        status, reason = "review", "required_data_missing"
    elif conflicting:
        status, reason = "no_match", "criteria_conflict"
    else:
        status, reason = "match", "criteria_match"
    return ProfileMatch(
        status,
        reason,
        tuple(dict.fromkeys(matched)),
        tuple(dict.fromkeys(missing)),
        tuple(dict.fromkeys(conflicting)),
    )


def _match_current(
    decision: UniversalDecision,
    profile: CandidateProfile,
    source_text: str | None,
) -> ProfileMatch:
    """Match one selected role, grade and user text filters."""
    if not any(
        item['direction_id'] == profile.direction_id
        and item['role_id'] == profile.role_id
        for item in decision.classifications
    ):
        return ProfileMatch('no_match', 'role_conflict', conflicting_fields=('role',))

    grades = _values(profile.preferences.get('seniority', []))
    if grades:
        overlap = _grade_range_matches(
            decision.analysis.grade_from, decision.analysis.grade_to, grades
        )
        if overlap is None:
            return ProfileMatch(
                'review', 'grade_missing', missing_fields=('seniority',)
            )
        if not overlap:
            return ProfileMatch(
                'no_match', 'grade_conflict', conflicting_fields=('seniority',)
            )

    skills = _values(profile.preferences.get('required_skills', []))
    excluded = _values(profile.preferences.get('excluded_skills', []))
    if (skills or excluded) and not source_text:
        return ProfileMatch(
            'review', 'source_text_missing', missing_fields=('source_text',)
        )
    normalized_text = _norm(source_text or '')
    if any(_contains_skill(normalized_text, word) for word in excluded):
        return ProfileMatch(
            'no_match', 'excluded_word', conflicting_fields=('excluded_skills',)
        )
    if skills and not any(_contains_skill(normalized_text, skill) for skill in skills):
        return ProfileMatch(
            'no_match', 'skill_missing', conflicting_fields=('required_skills',)
        )
    return ProfileMatch('match', 'criteria_match', matched_fields=('role',))


def match_profiles(
    decision: UniversalDecision, profiles: list[CandidateProfile]
) -> list[ProfileMatch]:
    """Apply one shared analysis locally to each profile."""
    return [match_profile(decision, profile) for profile in profiles]
