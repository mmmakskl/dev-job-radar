from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser

import pytest

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.models import NOT_SPECIFIED
from tg_vacancy_bot.telegram.candidate_card_formatter import (
    CardInput,
    MatchedProfile,
    RenderedMessage,
    format_card,
    format_hourly_digest,
)
from tests.test_llm_schemas import valid_payload

NOW = datetime.fromisoformat("2026-10-04T12:30:00+03:00")
END = datetime.fromisoformat("2026-10-04T09:00:00+00:00")


def card(**changes) -> CardInput:
    return replace(
        CardInput(
            "telegram:example:1",
            validate_analysis_result(
                valid_payload(
                    city="Москва",
                    hiring_geography=None,
                    summary="Разработка backend-сервисов и API.",
                )
            ),
            "2026-10-04T10:15:00+03:00",
            "https://t.me/example/1",
            (
                MatchedProfile("go", "Go backend"),
                MatchedProfile("platform", "Platform"),
            ),
        ),
        **changes,
    )


class TelegramHTML(HTMLParser):
    """Check complete, balanced Telegram tags without accepting injected markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "a"}
        if tag == "a":
            assert len(attrs) == 1 and attrs[0][0] == "href"
            self.links.append(attrs[0][1])
        else:
            assert not attrs
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def check_html(text: str) -> TelegramHTML:
    assert len(text.encode("utf-16-le")) // 2 <= 3500
    parser = TelegramHTML()
    parser.feed(text)
    parser.close()
    assert not parser.stack
    return parser


def test_actual_card_example() -> None:
    text = format_card(card(), now=NOW)
    assert text == (
        "<b>Senior Go Developer — Acme</b>\n"
        "📍 Москва · Remote\n"
        "🎓 Senior\n"
        "🛠 Go · PostgreSQL · Kubernetes\n"
        "Разработка backend-сервисов и API.\n"
        "🎯 Профили: Go backend, Platform\n"
        "🕒 Опубликовано 2 часа назад · 04.10.2026 10:15 (UTC+03:00)\n"
        '<a href="https://example.com/apply">Откликнуться ↗</a>'
    )
    check_html(text)


@pytest.mark.parametrize(
    "published",
    [
        None,
        "broken",
        "2026-99-04T10:00:00Z",
        "2026-10-04",
        "2026-10-04T10:00:00",
        datetime(2026, 10, 4),
    ],
)
def test_absent_bad_and_naive_dates_never_use_processing_time(published) -> None:
    text = format_card(card(published_at=published), now=NOW)
    assert "Дата публикации не указана" in text
    assert "04.10.2026" not in text


def test_iso_z_and_aware_datetime_are_equivalent() -> None:
    text = format_card(card(published_at="2026-10-04T07:15:00Z"), now=NOW)
    assert text == format_card(
        card(published_at=datetime(2026, 10, 4, 7, 15, tzinfo=timezone.utc)), now=NOW
    )
    assert "2 часа назад · 04.10.2026 10:15 (UTC+03:00)" in text


@pytest.mark.parametrize(
    ("delta", "age"),
    [
        (timedelta(seconds=59), "только что"),
        (timedelta(minutes=1), "1 минуту назад"),
        (timedelta(minutes=2), "2 минуты назад"),
        (timedelta(minutes=11), "11 минут назад"),
        (timedelta(minutes=21), "21 минуту назад"),
        (timedelta(hours=1), "1 час назад"),
        (timedelta(hours=5), "5 часов назад"),
        (timedelta(days=1), "1 день назад"),
        (timedelta(days=2), "2 дня назад"),
        (timedelta(days=11), "11 дней назад"),
        (timedelta(days=21), "21 день назад"),
    ],
)
def test_russian_relative_age(delta, age) -> None:
    assert age in format_card(card(published_at=NOW - delta), now=NOW)


def test_future_has_only_exact_date() -> None:
    text = format_card(card(published_at=NOW + timedelta(days=1)), now=NOW)
    assert "Опубликовано 05.10.2026 12:30 (UTC+03:00)" in text
    assert "назад" not in text and "только что" not in text


def test_timezone_crosses_date_and_handles_fractional_offset() -> None:
    text = format_card(
        card(published_at="2026-10-04T00:15:00Z"),
        now=NOW,
        timezone="America/Los_Angeles",
    )
    assert "03.10.2026 17:15 (UTC-07:00)" in text
    text = format_card(
        card(published_at="2026-10-04T00:15:00Z"), now=NOW, timezone="Asia/Kathmandu"
    )
    assert "04.10.2026 06:00 (UTC+05:45)" in text


@pytest.mark.parametrize(
    ("published", "now", "exact"),
    [
        (
            "2026-03-29T00:30:00Z",
            "2026-03-29T03:30:00+02:00",
            "29.03.2026 01:30 (UTC+01:00)",
        ),
        (
            "2026-10-25T00:30:00Z",
            "2026-10-25T02:30:00+01:00",
            "25.10.2026 02:30 (UTC+02:00)",
        ),
    ],
)
def test_dst_age_is_elapsed_utc_time(published, now, exact) -> None:
    text = format_card(
        card(published_at=published),
        now=datetime.fromisoformat(now),
        timezone="Europe/Berlin",
    )
    assert "1 час назад" in text and exact in text


def test_now_and_timezone_must_be_valid_even_for_empty_digest() -> None:
    for render in (
        lambda **kwargs: format_card(card(), **kwargs),
        lambda **kwargs: format_hourly_digest([], window_end=END, **kwargs),
    ):
        with pytest.raises(ValueError, match="now"):
            render(now=datetime(2026, 10, 4))
        with pytest.raises(ValueError, match="IANA"):
            render(now=NOW, timezone="Missing/Zone")
    with pytest.raises(ValueError, match="window_end"):
        format_hourly_digest([], now=NOW, window_end=datetime(2026, 10, 4))


def test_unknown_fields_omitted_and_title_falls_back() -> None:
    analysis = replace(
        card().analysis,
        title=NOT_SPECIFIED,
        company="",
        country=NOT_SPECIFIED,
        city="",
        hiring_geography="",
        work_format=NOT_SPECIFIED,
        grade_from=NOT_SPECIFIED,
        grade_to=NOT_SPECIFIED,
        required_stack=[],
        preferred_stack=[],
        summary=NOT_SPECIFIED,
        apply_link=NOT_SPECIFIED,
    )
    text = format_card(
        card(analysis=analysis, post_link=None, matched_profiles=()), now=NOW
    )
    assert text.startswith("<b>Вакансия</b>\n🕒 ")
    assert NOT_SPECIFIED not in text


def test_required_stack_first_six_unique_and_profiles_deduplicated() -> None:
    analysis = replace(
        card().analysis,
        required_stack=["Go", "postgres", "Go", "Redis"],
        preferred_stack=["PostgreSQL", "Kubernetes", "Kafka", "Docker", "Linux"],
    )
    profiles = (
        MatchedProfile("go", "Go backend"),
        MatchedProfile("go", "Old name"),
        MatchedProfile("p", "Platform"),
        MatchedProfile("p2", "Platform"),
    )
    text = format_card(card(analysis=analysis, matched_profiles=profiles), now=NOW)
    assert "🛠 Go · PostgreSQL · Redis · Kubernetes · Kafka · Docker\n" in text
    assert "Linux" not in text
    assert text.count("Go backend") == 1 and text.count("Platform") == 1
    assert "Old name" not in text


def test_html_injection_and_url_query_are_escaped() -> None:
    analysis = replace(
        card().analysis,
        title='<script>"&',
        company="<b>Injected</b>",
        summary="<i>text & data</i>",
        apply_link="https://example.com/apply?a=1&b=2",
    )
    text = format_card(
        card(
            analysis=analysis, matched_profiles=(MatchedProfile("p", "<a>profile</a>"),)
        ),
        now=NOW,
    )
    assert "&lt;script&gt;&quot;&amp;" in text
    assert "&lt;a&gt;profile&lt;/a&gt;" in text
    assert "?a=1&amp;b=2" in text
    assert check_html(text).links == ["https://example.com/apply?a=1&b=2"]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "tg://user?id=1",
        "//example.com",
        "http://",
        'https://example.com/" onclick="x',
        "https://example.com/a b",
        "https://example.com:bad/a",
        "https://user:password@example.com/a",
        "https://example.com/%wrong",
        "https://[invalid]/a",
        "https://bad_host/a",
        "https://example.com\\@evil.com/a",
    ],
)
def test_invalid_links_use_source(url) -> None:
    text = format_card(card(analysis=replace(card().analysis, apply_link=url)), now=NOW)
    assert "Исходная вакансия" in text
    assert check_html(text).links == ["https://t.me/example/1"]


def test_very_long_url_falls_back_or_is_omitted_without_cutting() -> None:
    long_url = "https://example.com/" + "x" * 5000
    source = "https://t.me/example/1"
    analysis = replace(card().analysis, apply_link=long_url)
    assert check_html(format_card(card(analysis=analysis), now=NOW)).links == [source]
    assert (
        check_html(
            format_card(card(analysis=analysis, post_link=long_url), now=NOW)
        ).links
        == []
    )


def test_extreme_unicode_and_entities_fit_and_summary_has_240_characters() -> None:
    huge = '😀<&"' * 2000
    analysis = replace(
        card().analysis,
        title=huge,
        company=huge,
        city=huge,
        country=huge,
        hiring_geography=huge,
        work_format=huge,
        grade_from=huge,
        grade_to=huge,
        required_stack=[huge],
        preferred_stack=[],
        summary="😀" * 400,
        apply_link="https://example.com/" + "&" * 300,
    )
    text = format_card(
        card(analysis=analysis, matched_profiles=(MatchedProfile("p", huge),)), now=NOW
    )
    check_html(text)
    summary = next(line for line in text.splitlines() if line.startswith("😀"))
    assert len(unescape(summary)) == 240
    assert unescape(summary).endswith("…")


def test_input_containers_are_frozen_and_snapshot_sequences() -> None:
    profiles = [MatchedProfile("p", "Profile")]
    value = card(matched_profiles=profiles)
    profiles.clear()
    assert len(value.matched_profiles) == 1
    for value in (
        value,
        MatchedProfile("p", "Profile"),
        RenderedMessage("text", ["v"]),
    ):
        with pytest.raises(FrozenInstanceError):
            value.extra = "change"


def test_empty_and_duplicate_digest() -> None:
    assert format_hourly_digest([], now=NOW, window_end=END) == ()
    duplicate = card(matched_profiles=(MatchedProfile("extra", "Data"),))
    pages = format_hourly_digest([card(), duplicate], now=NOW, window_end=END)
    assert len(pages) == 1
    assert pages[0].vacancy_ids == (card().vacancy_id,)
    assert "Уникальных вакансий: 1" in pages[0].text
    assert "Go backend, Platform, Data" in pages[0].text
    assert (
        "04.10.2026 11:00 (UTC+03:00) — 04.10.2026 12:00 (UTC+03:00)" in pages[0].text
    )
    assert "10:15" in pages[0].text
    assert "Разработка backend" not in pages[0].text
    check_html(pages[0].text)


def test_multiple_pages_preserve_whole_records_ids_and_links() -> None:
    cards = [
        card(
            vacancy_id=f"v:{i}",
            analysis=replace(
                card().analysis,
                title=f"Vacancy {i} " + "😀<&" * 100,
                apply_link=f"https://example.com/{i}?x=1&y=2",
            ),
        )
        for i in range(30)
    ]
    pages = format_hourly_digest(cards, now=NOW, window_end=END)
    assert len(pages) > 1
    assert tuple(vid for page in pages for vid in page.vacancy_ids) == tuple(
        c.vacancy_id for c in cards
    )
    for page in pages:
        assert page.vacancy_ids
        assert "Уникальных вакансий: 30" in page.text
        assert len(check_html(page.text).links) == len(page.vacancy_ids)
        for vid in page.vacancy_ids:
            assert f"Vacancy {vid.split(':')[1]} " in page.text


def test_digest_period_displays_both_offsets_across_dst() -> None:
    pages = format_hourly_digest(
        [card()],
        now=NOW,
        window_end=datetime.fromisoformat("2026-10-25T01:00:00Z"),
        timezone="Europe/Berlin",
    )
    assert (
        "25.10.2026 02:00 (UTC+02:00) — 25.10.2026 02:00 (UTC+01:00)" in pages[0].text
    )
