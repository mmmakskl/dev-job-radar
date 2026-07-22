from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.models import NOT_SPECIFIED, normalize_grade_range, normalize_stack

from tests.test_llm_schemas import valid_payload


def test_middle_plus_senior_grade() -> None:
    assert normalize_grade_range("Middle+ / Senior")[:2] == ("Middle", "Senior")


def test_junior_plus_middle_grade() -> None:
    assert normalize_grade_range("Junior+ / Middle")[:2] == ("Junior", "Middle")


def test_staff_engineer_is_not_lead() -> None:
    assert normalize_grade_range("Staff Engineer")[:2] == ("Staff", "Staff")


def test_team_lead_and_tech_lead_responsibility() -> None:
    assert normalize_grade_range("Team Lead") == ("Lead", "Lead", "Team Lead")
    assert normalize_grade_range("Tech Lead") == (
        NOT_SPECIFIED,
        NOT_SPECIFIED,
        "Tech Lead",
    )


def test_remote_worldwide_and_hybrid_city() -> None:
    remote = validate_analysis_result(
        valid_payload(work_format="Remote", hiring_geography="Worldwide")
    )
    hybrid = validate_analysis_result(
        valid_payload(work_format="Hybrid", city="Berlin", country="Germany")
    )

    assert (remote.work_format, remote.hiring_geography) == ("Remote", "Worldwide")
    assert (hybrid.work_format, hybrid.city, hybrid.country) == (
        "Hybrid",
        "Berlin",
        "Germany",
    )


def test_salary_absent_range_and_hourly() -> None:
    absent = validate_analysis_result(
        valid_payload(
            salary_from=None,
            salary_to=None,
            currency=None,
            salary_period=None,
        )
    )
    ranged = validate_analysis_result(valid_payload(salary_from=3000, salary_to=4500))
    hourly = validate_analysis_result(
        valid_payload(
            salary_from=40,
            salary_to=None,
            currency="USD",
            salary_period="Hour",
        )
    )

    assert absent.salary_from is None and absent.currency == NOT_SPECIFIED
    assert (ranged.salary_from, ranged.salary_to) == (3000, 4500)
    assert (hourly.salary_from, hourly.salary_period) == (40, "Hour")


def test_required_and_preferred_stack_are_separate_and_normalized() -> None:
    result = validate_analysis_result(
        valid_payload(
            required_stack=["Golang", "postgresql", "REST API", "ci/cd"],
            preferred_stack=["k8s", "grpc"],
        )
    )

    assert result.required_stack == ["Go", "PostgreSQL", "REST", "CI/CD"]
    assert result.preferred_stack == ["Kubernetes", "gRPC"]
    assert normalize_stack(["golang", "postgres", "k8s", "RESTful", "gitlab-ci"]) == [
        "Go",
        "PostgreSQL",
        "Kubernetes",
        "REST",
        "GitLab CI",
    ]

