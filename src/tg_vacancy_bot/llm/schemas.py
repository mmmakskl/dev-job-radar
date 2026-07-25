"""Строгая проверка и нормализация структурированных ответов Mistral."""

import json
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
_PREVIEW_LIMIT = 240
_MISSING = object()


def _type_name(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        item_types = sorted({_type_name(item) for item in value})
        return f"list[{', '.join(item_types)}]" if item_types else "list[empty]"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _preview(value: Any) -> str:
    if value is _MISSING:
        return "<missing>"

    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = repr(value)

    if len(rendered) > _PREVIEW_LIMIT:
        return rendered[: _PREVIEW_LIMIT - 3] + "..."
    return rendered


def _invalid(
    message: str,
    *,
    field: str | None = None,
    expected: str | None = None,
    actual: Any = _MISSING,
) -> None:
    details = []
    if field:
        details.append(f"field={field}")
    if expected:
        details.append(f"expected={expected}")
    if actual is not _MISSING:
        details.append(f"actual_type={_type_name(actual)}")
        details.append(f"actual_preview={_preview(actual)}")

    detail_text = "; ".join(details)
    log_message = f"{message}; {detail_text}" if detail_text else message
    logging.warning("[MISTRAL] Невалидная схема ответа: %s", log_message)
    raise InvalidAnalysisResultError(log_message)


def _text_fragment(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_text_fragment(item) for item in value]
        return ", ".join(part for part in parts if part) or None
    return None


def _text_from_mapping(value: dict[str, Any]) -> str:
    parts = []
    for key, item in value.items():
        fragment = _text_fragment(item)
        if fragment:
            parts.append(f"{key}: {fragment}")
    return "; ".join(parts) or NOT_SPECIFIED


def _text(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if value is None or value == "":
        return NOT_SPECIFIED
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        text = "; ".join(item.strip() for item in value if item.strip())
        return text or NOT_SPECIFIED
    if isinstance(value, dict):
        return _text_from_mapping(value)
    if not isinstance(value, str):
        _invalid(
            f"{name} должно быть строкой, массивом строк, объектом или null",
            field=name,
            expected="str | list[str] | object | null",
            actual=value,
        )
    return value.strip() or NOT_SPECIFIED


def _number(data: dict[str, Any], name: str) -> float | int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(
            f"{name} должно быть числом или null",
            field=name,
            expected="number | null",
            actual=value,
        )
    return value


def _string_list(data: dict[str, Any], name: str) -> list[str] | str | None:
    value = data.get(name)
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _invalid(
            f"{name} должно быть массивом строк, строкой или null",
            field=name,
            expected="list[str] | str | null",
            actual=value,
        )
    return value


def validate_analysis_result(data: dict) -> VacancyAnalysis | None:
    """Возвращает проверенную модель; для нерелевантной вакансии тоже модель."""
    if not isinstance(data, dict):
        _invalid(
            "корневое значение должно быть JSON-объектом",
            expected="object",
            actual=data,
        )

    extra_fields = set(data) - EXPECTED_FIELDS
    if extra_fields:
        _invalid(
            "неожиданные поля: " + ", ".join(sorted(extra_fields)),
            expected="только поля строгой схемы",
            actual={field: data.get(field) for field in sorted(extra_fields)},
        )

    is_match = data.get("is_match")
    if not isinstance(is_match, bool):
        _invalid(
            "is_match отсутствует или не является bool",
            field="is_match",
            expected="bool",
            actual=data.get("is_match", _MISSING),
        )

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
