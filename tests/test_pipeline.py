from tg_vacancy_bot.pipeline.fingerprints import build_text_hash
from tg_vacancy_bot.pipeline.prefilter import contains_keywords


def test_keyword_filter_matches_go() -> None:
    assert contains_keywords("Ищем Go-разработчика", ["go", "golang"])


def test_keyword_filter_matches_golang() -> None:
    assert contains_keywords("Senior GOLANG developer", ["go", "golang"])


def test_keyword_filter_avoids_partial_word_match() -> None:
    assert not contains_keywords("Опыт работы с Django", ["go"])


def test_fingerprint_ignores_case_whitespace_and_telegram_link() -> None:
    first = "  Вакансия  Go\nразработчика https://t.me/jobs/42 "
    second = "вакансия go   разработчика"

    assert build_text_hash(first) == build_text_hash(second)

