"""Deterministic local matching of analyzed vacancies against candidate profiles."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

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


def match_profile(
    decision: UniversalDecision, profile: CandidateProfile
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

    matched: list[str] = []
    missing: list[str] = []
    conflicting: list[str] = []
    roles = {
        (
            item["direction_id"],
            item["specialization_id"],
            (
                "backend_developer"
                if item["role_id"] == "api_developer"
                else item["role_id"]
            ),
        )
        for item in decision.classifications
    }
    profile_role = (
        "backend_developer" if profile.role_id == "api_developer" else profile.role_id
    )
    path = (profile.direction_id, profile.specialization_id, profile_role)
    if path in roles:
        matched.append("role")
    elif roles:
        conflicting.append("role")
    else:
        missing.append("role")

    analysis = decision.analysis
    preferences = profile.preferences
    geography = _values(preferences.get("geography", []))
    checks = (
        (
            "stacks",
            [*(_values(analysis.required_stack)), *(_values(analysis.preferred_stack))],
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


def match_profiles(
    decision: UniversalDecision, profiles: list[CandidateProfile]
) -> list[ProfileMatch]:
    """Apply one shared analysis locally to each profile."""
    return [match_profile(decision, profile) for profile in profiles]
