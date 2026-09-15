import asyncio
from datetime import datetime, timezone

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.telegram.notifier import (
    format_vacancy_notification,
    send_vacancy_notification,
)
from tests.test_llm_schemas import valid_payload


def notification(**overrides: object) -> str:
    return format_vacancy_notification(
        vacancy_id="jobs_42",
        post_link="https://t.me/jobs/42",
        channel_name="jobs",
        data=validate_analysis_result(valid_payload(**overrides)),
        published_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


def test_notification_keeps_legacy_signature_and_presents_telegram_source() -> None:
    message = notification()

    assert "<b>🟢 Senior Go Developer — Acme</b>" in message
    assert 'via <a href="https://t.me/jobs/42">Telegram</a>' in message
    assert "#golang" in message


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
