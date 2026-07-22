"""Структурированная модель и детерминированная нормализация вакансии."""

import re
from dataclasses import dataclass
from typing import Any


NOT_SPECIFIED = "Не указано"
GRADES = ("Intern", "Junior", "Middle", "Senior", "Staff", "Lead", "Head")
ROLES = {
    "Backend",
    "Full-stack",
    "DevOps",
    "SRE",
    "Platform",
    "Infrastructure",
    "Data",
    "QA",
    "Mobile",
    "Embedded",
    "Engineering Management",
    "Другое",
}
SPECIALIZATIONS = {
    "Highload",
    "Distributed Systems",
    "Microservices",
    "Cloud",
    "Infrastructure",
    "DevTools",
    "Security",
    "Blockchain",
    "AI/ML",
    "Data Engineering",
    "API/Integrations",
    "Bots",
    "Networking",
    "Embedded",
    "Другое",
}
PRODUCT_DOMAINS = {
    "FinTech",
    "E-commerce",
    "Retail",
    "EdTech",
    "MedTech",
    "AdTech",
    "GameDev",
    "Telecom",
    "Media",
    "Web3/Crypto",
    "AI",
    "Cloud",
    "Cybersecurity",
    "Logistics",
    "Banking",
    "SaaS",
    "Gambling/iGaming",
    "Другое",
    NOT_SPECIFIED,
}
WORK_FORMATS = {"Remote", "Hybrid", "Office", NOT_SPECIFIED}
RELOCATIONS = {"Да", "Нет", "Возможна", NOT_SPECIFIED}
EMPLOYMENT_TYPES = {
    "Full-time",
    "Part-time",
    "Contract",
    "Internship",
    NOT_SPECIFIED,
}
SALARY_PERIODS = {"Hour", "Month", "Year", NOT_SPECIFIED}

_GRADE_PATTERNS = {
    "intern": "Intern",
    "internship": "Intern",
    "стажер": "Intern",
    "стажёр": "Intern",
    "junior": "Junior",
    "джуниор": "Junior",
    "middle": "Middle",
    "мидл": "Middle",
    "senior": "Senior",
    "сеньор": "Senior",
    "staff": "Staff",
    "lead": "Lead",
    "head": "Head",
}
_STACK_ALIASES = {
    "go": "Go",
    "golang": "Go",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "rest api": "REST",
    "restful": "REST",
    "rest": "REST",
    "gitlab-ci": "GitLab CI",
    "gitlab ci": "GitLab CI",
    "ci/cd": "CI/CD",
    "grpc": "gRPC",
}


def normalize_grade_range(value: str | None) -> tuple[str, str, str]:
    """Нормализует обозначение грейда и уровень ответственности."""
    if not value:
        return NOT_SPECIFIED, NOT_SPECIFIED, NOT_SPECIFIED

    lowered = value.lower()
    responsibility = NOT_SPECIFIED
    if re.search(r"\bteam[\s-]*lead\b", lowered):
        return "Lead", "Lead", "Team Lead"
    is_tech_lead = bool(re.search(r"\btech(?:nical)?[\s-]*lead\b", lowered))
    if is_tech_lead:
        responsibility = "Tech Lead"

    found: list[str] = []
    for token in re.findall(r"[a-zа-яё]+", lowered):
        grade = _GRADE_PATTERNS.get(token)
        if is_tech_lead and token == "lead":
            continue
        if grade and grade not in found:
            found.append(grade)

    if not found:
        return NOT_SPECIFIED, NOT_SPECIFIED, responsibility
    if len(found) == 1:
        return found[0], found[0], responsibility
    return found[0], found[-1], responsibility


def normalize_stack(values: list[str] | str | None) -> list[str]:
    """Нормализует названия технологий, сохраняя порядок без дублей."""
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r"[,;]", values)

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        canonical = _STACK_ALIASES.get(cleaned.lower(), cleaned)
        key = canonical.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(canonical)
    return normalized


def normalize_choice(value: str | None, allowed: set[str]) -> str:
    """Оставляет только разрешённое категориальное значение."""
    if not value:
        return NOT_SPECIFIED
    for item in allowed:
        if value.casefold() == item.casefold():
            return item
    return NOT_SPECIFIED


def normalize_choices(values: list[str] | str | None, allowed: set[str]) -> list[str]:
    """Нормализует список категорий и заменяет неизвестные на «Другое»."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    result: list[str] = []
    for value in values:
        if not value.strip():
            continue
        normalized = next(
            (item for item in allowed if value.casefold() == item.casefold()),
            "Другое",
        )
        if normalized not in result:
            result.append(normalized)
    return result


@dataclass(frozen=True)
class VacancyAnalysis:
    """Только проверенные поля, извлечённые LLM из текста вакансии."""

    is_match: bool
    title: str
    company: str
    grade_from: str
    grade_to: str
    responsibility_level: str
    primary_roles: list[str]
    specializations: list[str]
    product_domain: str
    experience_from: float | int | None
    work_format: str
    country: str
    city: str
    hiring_geography: str
    relocation: str
    employment_type: str
    salary_from: float | int | None
    salary_to: float | int | None
    currency: str
    salary_period: str
    primary_language: str
    required_stack: list[str]
    preferred_stack: list[str]
    vacancy_language: str
    contact: str
    apply_link: str
    summary: str
    responsibilities: str
    requirements: str
    additional_conditions: str

    def as_log_context(self) -> dict[str, Any]:
        """Возвращает безопасный минимум для диагностического лога."""
        return {
            "is_match": self.is_match,
            "title": self.title,
            "company": self.company,
        }
