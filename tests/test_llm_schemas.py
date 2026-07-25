import asyncio
from types import SimpleNamespace

import pytest

from tg_vacancy_bot.llm import mistral
from tg_vacancy_bot.llm.schemas import (
    InvalidAnalysisResultError,
    validate_analysis_result,
)
from tg_vacancy_bot.models import NOT_SPECIFIED


def valid_payload(**overrides) -> dict:
    payload = {
        "is_match": True,
        "title": "Senior Go Developer",
        "company": "Acme",
        "grade_from": "Senior",
        "grade_to": "Senior",
        "responsibility_level": None,
        "primary_roles": ["Backend"],
        "specializations": ["Microservices"],
        "product_domain": "FinTech",
        "experience_from": 4,
        "work_format": "Remote",
        "country": None,
        "city": None,
        "hiring_geography": "Worldwide",
        "relocation": "Нет",
        "employment_type": "Full-time",
        "salary_from": 3000,
        "salary_to": 4500,
        "currency": "USD",
        "salary_period": "Month",
        "primary_language": "Go",
        "required_stack": ["golang", "postgres"],
        "preferred_stack": ["k8s"],
        "vacancy_language": "Русский",
        "contact": "@recruiter",
        "apply_link": "https://example.com/apply",
        "summary": "Разработка Go-сервисов.",
        "responsibilities": "Разрабатывать сервисы.",
        "requirements": "Опыт с Go.",
        "additional_conditions": None,
    }
    payload.update(overrides)
    return payload


def test_valid_analysis_result_passes_and_normalizes() -> None:
    result = validate_analysis_result(valid_payload())

    assert result.company == "Acme"
    assert result.required_stack == ["Go", "PostgreSQL"]
    assert result.preferred_stack == ["Kubernetes"]


def test_missing_company_or_contact_uses_not_specified() -> None:
    result = validate_analysis_result(valid_payload(company=None, contact=None))

    assert result.company == NOT_SPECIFIED
    assert result.contact == NOT_SPECIFIED


def test_text_fields_accept_string_lists_from_llm() -> None:
    result = validate_analysis_result(
        valid_payload(
            responsibilities=["Разрабатывать сервисы", "Поддерживать API"],
            requirements=["Go", "PostgreSQL"],
            additional_conditions=[],
        )
    )

    assert result.responsibilities == "Разрабатывать сервисы; Поддерживать API"
    assert result.requirements == "Go; PostgreSQL"
    assert result.additional_conditions == NOT_SPECIFIED


def test_text_fields_accept_simple_objects_from_llm() -> None:
    result = validate_analysis_result(
        valid_payload(
            contact={
                "telegram": "@recruiter",
                "email": "hr@example.com",
                "phones": ["+79990000000", "+79991111111"],
            }
        )
    )

    assert result.contact == (
        "telegram: @recruiter; "
        "email: hr@example.com; "
        "phones: +79990000000, +79991111111"
    )


def test_missing_is_match_is_rejected() -> None:
    data = valid_payload()
    data.pop("is_match")

    with pytest.raises(InvalidAnalysisResultError):
        validate_analysis_result(data)


def test_string_is_match_is_rejected() -> None:
    with pytest.raises(InvalidAnalysisResultError):
        validate_analysis_result(valid_payload(is_match="true"))


def test_additional_field_is_rejected() -> None:
    with pytest.raises(InvalidAnalysisResultError):
        validate_analysis_result(valid_payload(unexpected="value"))


def test_garbage_json_does_not_crash_analyzer(monkeypatch) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{garbage"))]
            )

    completions = FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    delays = []

    async def fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr(mistral, "client", fake_client)
    monkeypatch.setattr(mistral.asyncio, "sleep", fake_sleep)

    result = asyncio.run(mistral.analyze_text("Go vacancy"))

    assert result is None
    assert completions.calls == 2
    assert delays == [1]


def test_invalid_json_schema_uses_bounded_retry(monkeypatch) -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"is_match": "true"}')
                    )
                ]
            )

    completions = FakeCompletions()
    monkeypatch.setattr(
        mistral,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    async def no_delay(_delay: int) -> None:
        return None

    monkeypatch.setattr(mistral.asyncio, "sleep", no_delay)

    assert asyncio.run(mistral.analyze_text("Go vacancy")) is None
    assert completions.calls == 2
