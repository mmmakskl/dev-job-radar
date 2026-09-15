from dataclasses import replace

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.telegram.vacancy_channel_formatter import (
    SourcePresentation,
    VacancyChannelFormatter,
)
from tests.test_llm_schemas import valid_payload


def format_card(**overrides: object) -> str:
    return VacancyChannelFormatter().format(
        data=validate_analysis_result(valid_payload(**overrides)),
        source=SourcePresentation("Telegram", "https://t.me/jobs/42"),
    )


def test_formats_a_complete_compact_card_without_a_repeated_grade() -> None:
    message = format_card()

    assert message.splitlines() == [
        "<b>🟢 Senior Go Developer — Acme</b>",
        "💰 3000–4500 USD/month",
        "🌍 Remote · Worldwide",
        "🧭 Full-time",
        "🛠 Go · PostgreSQL · Kubernetes",
        "Разработка Go-сервисов.",
        "#golang #senior #remote #worldwide",
        'via <a href="https://t.me/jobs/42">Telegram</a>',
    ]


def test_omits_a_grade_range_already_spelled_with_another_separator() -> None:
    message = format_card(
        title='Middle/Senior Go Developer', grade_from='Middle', grade_to='Senior'
    )

    assert '🧭 Full-time' in message
    assert '🧭 Middle–Senior' not in message


def test_uses_fallback_title_and_omits_unknown_optional_fields() -> None:
    message = format_card(
        title=None,
        company=None,
        salary_from=None,
        salary_to=None,
        work_format=None,
        country=None,
        city=None,
        hiring_geography=None,
        grade_from=None,
        grade_to=None,
        employment_type=None,
        required_stack=[],
        preferred_stack=[],
        summary=None,
    )

    assert message.splitlines() == [
        "<b>🟢 Go Developer</b>",
        "#golang",
        'via <a href="https://t.me/jobs/42">Telegram</a>',
    ]
    assert "Не указано" not in message
    assert "\n\n" not in message


def test_truncates_summary_at_a_word_boundary_and_limits_stack_and_tags() -> None:
    summary = "слово " * 100
    data = replace(
        validate_analysis_result(
            valid_payload(
                title="Go Engineer",
                grade_from="Middle",
                grade_to="Senior",
                work_format="Hybrid",
                city="New York",
                country="USA",
                hiring_geography=None,
                required_stack=[
                    "Go",
                    "PostgreSQL",
                    "Redis",
                    "Kafka",
                    "Docker",
                    "Linux",
                ],
                preferred_stack=["Go", "Kubernetes", "Terraform"],
            )
        ),
        summary=summary,
    )
    message = VacancyChannelFormatter().format(
        data=data,
        source=SourcePresentation("Telegram", "https://t.me/jobs/42"),
    )
    lines = message.splitlines()
    description = lines[5]

    assert description.endswith("…")
    assert len(description) <= 400
    assert "слово " not in description[-7:]
    assert lines[4] == "🛠 Go · PostgreSQL · Redis · Kafka · Docker · Linux"
    assert lines[-2].split() == [
        "#golang",
        "#middle_senior",
        "#hybrid",
        "#new_york_usa",
    ]


def test_escapes_html_text_and_attributes() -> None:
    message = VacancyChannelFormatter().format(
        data=validate_analysis_result(
            valid_payload(
                title='<Senior "Go">',
                company='A & B',
                summary='Use <html> & "quotes"',
                required_stack=['Go<script>'],
            )
        ),
        source=SourcePresentation('<Telegram & Co>', 'https://example.com/?a=1&b="2"'),
    )

    assert '<b>🟢 &lt;Senior &quot;Go&quot;&gt; — A &amp; B</b>' in message
    assert '🛠 Go&lt;script&gt;' in message
    assert 'Use &lt;html&gt; &amp; &quot;quotes&quot;' in message
    assert (
        'via <a href="https://example.com/?a=1&amp;b=&quot;2&quot;">'
        '&lt;Telegram &amp; Co&gt;</a>'
    ) in message


def test_supports_threads_and_ats_sources_without_a_url() -> None:
    formatter = VacancyChannelFormatter()
    data = validate_analysis_result(valid_payload())

    threads = formatter.format(data=data, source=SourcePresentation("Threads"))
    greenhouse = formatter.format(
        data=data,
        source=SourcePresentation("Greenhouse", "https://boards.greenhouse.io/acme"),
    )

    assert threads.endswith("via Threads")
    assert greenhouse.endswith(
        'via <a href="https://boards.greenhouse.io/acme">Greenhouse</a>'
    )


def test_keeps_a_long_source_url_intact() -> None:
    source_url = 'https://boards.greenhouse.io/acme/jobs/' + 'a' * 180

    message = VacancyChannelFormatter().format(
        data=validate_analysis_result(valid_payload()),
        source=SourcePresentation('Greenhouse', source_url),
    )

    assert f'href="{source_url}"' in message
    assert '…' not in message.split('href="', maxsplit=1)[1].split('"', maxsplit=1)[0]


def test_never_exceeds_the_bot_api_message_limit_with_hostile_text() -> None:
    message = VacancyChannelFormatter().format(
        data=validate_analysis_result(
            valid_payload(
                title='"' * 500,
                company='&' * 500,
                city='<>' * 500,
                summary='"' * 500,
                required_stack=['<' * 500] * 6,
            )
        ),
        source=SourcePresentation('&' * 500, 'https://example.com/?x=' + '&' * 500),
    )

    assert len(message) < 4096


def test_oversized_source_url_keeps_a_short_summary_when_using_plain_source() -> None:
    message = VacancyChannelFormatter().format(
        data=validate_analysis_result(valid_payload(summary='Короткое описание.')),
        source=SourcePresentation('Greenhouse', 'https://example.com/' + 'a' * 4000),
    )

    assert 'Короткое описание.' in message
    assert message.endswith('via Greenhouse')
    assert len(message) < 4096
