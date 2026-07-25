from tg_vacancy_bot.pipeline.fingerprints import build_text_hash
from tg_vacancy_bot.pipeline.prefilter import (
    candidate_profile_reasons,
    contains_keywords,
    looks_like_candidate_profile,
)


def test_keyword_filter_matches_go() -> None:
    assert contains_keywords("Ищем Go-разработчика", ["go", "golang"])


def test_keyword_filter_matches_golang() -> None:
    assert contains_keywords("Senior GOLANG developer", ["go", "golang"])


def test_keyword_filter_avoids_partial_word_match() -> None:
    assert not contains_keywords("Опыт работы с Django", ["go"])


def test_candidate_profile_filter_matches_resume_post() -> None:
    text = """
    Senior Golang Developer
    Опыт работы: 8 лет
    Мой стек: Go, PostgreSQL, Kafka, Kubernetes
    Зарплатные ожидания: 5000 USD
    Контакт: @candidate
    """

    assert looks_like_candidate_profile(text)
    assert "опыт работы" in candidate_profile_reasons(text)


def test_candidate_profile_filter_keeps_regular_vacancy_with_resume_instruction() -> None:
    text = """
    Вакансия: Senior Go Developer
    Мы ищем backend-разработчика в продуктовую команду.
    Требования: Go, PostgreSQL, Kafka.
    Отправляйте резюме рекрутеру.
    """

    assert not looks_like_candidate_profile(text)


def test_fingerprint_ignores_case_whitespace_and_telegram_link() -> None:
    first = "  Вакансия  Go\nразработчика https://t.me/jobs/42 "
    second = "вакансия go   разработчика"

    assert build_text_hash(first) == build_text_hash(second)
