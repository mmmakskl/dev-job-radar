import asyncio
from datetime import datetime, timezone

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tests.test_llm_schemas import valid_payload


class DedupeStateSpy:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str, str | None]] = []

    def is_duplicate(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> bool:
        return False

    def mark_exported(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> None:
        self.marked.append((post_link, text_hash, vacancy_id))


def test_failed_sheet_export_is_not_marked(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    async def failed_export(**_kwargs) -> bool:
        return False

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    state = DedupeStateSpy()
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=failed_export,
        dedupe_state=state,
    )

    saved = asyncio.run(
        processor.process_message(
            "Go vacancy",
            "Go vacancy",
            "https://t.me/jobs/42",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            "jobs",
        )
    )

    assert not saved
    assert state.marked == []


def test_processor_passes_metadata_and_marks_stable_id(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    received = {}

    async def successful_export(**kwargs) -> bool:
        received.update(kwargs)
        return True

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    state = DedupeStateSpy()
    published_at = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=successful_export,
        dedupe_state=state,
    )

    saved = asyncio.run(
        processor.process_message(
            "Go vacancy",
            "Raw Go vacancy",
            "https://t.me/RemoteGeekJob/40909",
            published_at,
            "RemoteGeekJob",
        )
    )

    assert saved
    assert received["vacancy_id"] == "remotegeekjob_40909"
    assert received["published_at"] is published_at
    assert received["channel_name"] == "RemoteGeekJob"
    assert state.marked[0][2] == "remotegeekjob_40909"

