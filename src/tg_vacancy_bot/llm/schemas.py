"""Строгая проверка и нормализация структурированных ответов Mistral."""

import logging
from typing import Any

from tg_vacancy_bot.models import (
    EMPLOYMENT_TYPES,
    NOT_SPECIFIED,
    PRODUCT_DOMAINS,
    RELOCATIONS,
    ROLES,
    SALARY_PERIODS,
    SPECIALIZATIONS,
    WORK_FORMATS,
    VacancyAnalysis,
    normalize_choice,
    normalize_choices,
    normalize_grade_range,
    normalize_stack,
)


class InvalidAnalysisResultError(ValueError):
    """Ответ является JSON, но не соответствует схеме вакансии."""


EXPECTED_FIELDS = {
    "is_match",
    "title",
    "company",
    "grade_from",
    "grade_to",
    "responsibility_level",
    "primary_roles",
    "specializations",
    "product_domain",
    "experience_from",
    "work_format",
    "country",
    "city",
    "hiring_geography",
    "relocation",
    "employment_type",
    "salary_from",
    "salary_to",
    "currency",
    "salary_period",
    "primary_language",
    "required_stack",
    "preferred_stack",
    "vacancy_language",
    "contact",
    "apply_link",
    "summary",
    "responsibilities",
    "requirements",
    "additional_conditions",
}


def _invalid(message: str) -> None:
    logging.warning("[MISTRAL] Невалидная схема ответа: %s", message)
    raise InvalidAnalysisResultError(message)


def _text(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if value is None or value == "":
        return NOT_SPECIFIED
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        text = "; ".join(item.strip() for item in value if item.strip())
        return text or NOT_SPECIFIED
    if not isinstance(value, str):
        _invalid(f"{name} должно быть строкой, массивом строк или null")
    return value.strip() or NOT_SPECIFIED


def _number(data: dict[str, Any], name: str) -> float | int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(f"{name} должно быть числом или null")
    return value


def _string_list(data: dict[str, Any], name: str) -> list[str] | str | None:
    value = data.get(name)
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _invalid(f"{name} должно быть массивом строк, строкой или null")
    return value


def validate_analysis_result(data: dict) -> VacancyAnalysis | None:
    """Возвращает проверенную модель; для нерелевантной вакансии тоже модель."""
    if not isinstance(data, dict):
        _invalid("корневое значение должно быть JSON-объектом")

    extra_fields = set(data) - EXPECTED_FIELDS
    if extra_fields:
        _invalid("неожиданные поля: " + ", ".join(sorted(extra_fields)))

    is_match = data.get("is_match")
    if not isinstance(is_match, bool):
        _invalid("is_match отсутствует или не является bool")

    raw_grade = " / ".join(
        value
        for value in (data.get("grade_from"), data.get("grade_to"))
        if isinstance(value, str) and value.strip()
    )
    grade_from, grade_to, inferred_responsibility = normalize_grade_range(raw_grade)
    responsibility = _text(data, "responsibility_level")
    if inferred_responsibility != NOT_SPECIFIED:
        responsibility = inferred_responsibility

    primary_roles = normalize_choices(
        _string_list(data, "primary_roles"),
        ROLES,
    )
    specializations = normalize_choices(
        _string_list(data, "specializations"),
        SPECIALIZATIONS,
    )
    primary_language = _text(data, "primary_language")
    if primary_language != NOT_SPECIFIED:
        primary_language = normalize_stack([primary_language])[0]

    return VacancyAnalysis(
        is_match=is_match,
        title=_text(data, "title"),
        company=_text(data, "company"),
        grade_from=grade_from,
        grade_to=grade_to,
        responsibility_level=responsibility,
        primary_roles=primary_roles,
        specializations=specializations,
        product_domain=normalize_choice(_text(data, "product_domain"), PRODUCT_DOMAINS),
        experience_from=_number(data, "experience_from"),
        work_format=normalize_choice(_text(data, "work_format"), WORK_FORMATS),
        country=_text(data, "country"),
        city=_text(data, "city"),
        hiring_geography=_text(data, "hiring_geography"),
        relocation=normalize_choice(_text(data, "relocation"), RELOCATIONS),
        employment_type=normalize_choice(
            _text(data, "employment_type"),
            EMPLOYMENT_TYPES,
        ),
        salary_from=_number(data, "salary_from"),
        salary_to=_number(data, "salary_to"),
        currency=_text(data, "currency").upper()
        if data.get("currency")
        else NOT_SPECIFIED,
        salary_period=normalize_choice(
            _text(data, "salary_period"),
            SALARY_PERIODS,
        ),
        primary_language=primary_language,
        required_stack=normalize_stack(_string_list(data, "required_stack")),
        preferred_stack=normalize_stack(_string_list(data, "preferred_stack")),
        vacancy_language=_text(data, "vacancy_language"),
        contact=_text(data, "contact"),
        apply_link=_text(data, "apply_link"),
        summary=_text(data, "summary")[:250],
        responsibilities=_text(data, "responsibilities"),
        requirements=_text(data, "requirements"),
        additional_conditions=_text(data, "additional_conditions"),
    )
