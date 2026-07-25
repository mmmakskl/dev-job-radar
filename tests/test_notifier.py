import asyncio
from datetime import datetime, timezone

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.models import NOT_SPECIFIED
from tg_vacancy_bot.telegram.notifier import (
    MAX_SUMMARY_LENGTH,
    format_vacancy_notification,
    send_vacancy_notification,
)
from tests.test_llm_schemas import valid_payload


def notification(**overrides) -> str:
    data = validate_analysis_result(valid_payload(**overrides))
    return format_vacancy_notification(
        vacancy_id="jobs_42",
        post_link="https://t.me/jobs/42",
        channel_name="jobs",
        data=data,
        published_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


def test_notification_includes_useful_fields_and_links() -> None:
    message = notification()

    assert "<b>🟢 Senior Go Developer</b>" in message
    assert "<b>Компания:</b> Acme" in message
    assert "<b>Стек:</b> Go, PostgreSQL, Kubernetes" in message
    assert 'href="https://example.com/apply">Откликнуться' in message
    assert 'href="https://t.me/jobs/42">Источник' in message


def test_notification_escapes_html_and_omits_not_specified_fields() -> None:
    message = notification(
        company="<Acme & Co>",
        grade_from=None,
        grade_to=None,
        work_format=None,
        country=None,
        city=None,
        hiring_geography=None,
        salary_from=None,
        salary_to=None,
    )

    assert "&lt;Acme &amp; Co&gt;" in message
    assert "<b>Грейд:</b>" not in message
    assert "<b>Формат:</b>" not in message
    assert "<b>Локация:</b>" not in message
    assert "<b>Зарплата:</b>" not in message
    assert NOT_SPECIFIED not in message


def test_notification_uses_source_when_apply_link_is_not_specified() -> None:
    message = notification(apply_link=None)

    assert message.count('href="https://t.me/jobs/42"') == 2


def test_notification_truncates_long_summary() -> None:
    original_summary = "x" * (MAX_SUMMARY_LENGTH + 100)
    message = notification(summary=original_summary)
    summary = message.split("<b>Кратко:</b>\n", maxsplit=1)[1].split(
        "\n\n<a ",
        maxsplit=1,
    )[0]

    assert len(summary) <= MAX_SUMMARY_LENGTH
    assert summary != original_summary


def test_send_notification_uses_telethon_arguments() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        async def send_message(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))

    client = FakeClient()
    data = validate_analysis_result(valid_payload())

    sent = asyncio.run(
        send_vacancy_notification(
            client=client,
            target="@vacancies",
            vacancy_id="jobs_42",
            post_link="https://t.me/jobs/42",
            channel_name="jobs",
            data=data,
            published_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
    )

    assert sent is True
    assert client.calls[0][0][0] == "@vacancies"
    assert client.calls[0][1] == {"parse_mode": "html", "link_preview": False}


def test_send_notification_returns_false_when_client_fails() -> None:
    class FailingClient:
        async def send_message(self, *_args, **_kwargs) -> None:
            raise RuntimeError("Telegram unavailable")

    sent = asyncio.run(
        send_vacancy_notification(
            client=FailingClient(),
            target="@vacancies",
            vacancy_id="jobs_42",
            post_link="https://t.me/jobs/42",
            channel_name="jobs",
            data=validate_analysis_result(valid_payload()),
            published_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
    )

    assert sent is False
