import asyncio
from datetime import datetime, timezone

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
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


def test_failed_sheet_export_is_not_marked(monkeypatch, tmp_path) -> None:
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
        group_store=VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), 14),
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
    assert processor.group_store.list_groups() == []


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


def test_processor_skips_candidate_profile_before_llm(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        raise AssertionError("candidate profile should not be sent to LLM")

    async def successful_export(**_kwargs) -> bool:
        raise AssertionError("candidate profile should not be exported")

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    state = DedupeStateSpy()
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=successful_export,
        dedupe_state=state,
    )

    saved = asyncio.run(
        processor.process_message(
            "Senior Golang Developer\nОпыт работы: 8 лет\nМой стек: Go, Kafka",
            "Senior Golang Developer\nОпыт работы: 8 лет\nМой стек: Go, Kafka",
            "https://t.me/myjobit/125461",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            "myjobit",
        )
    )

    assert not saved
    assert state.marked == []
    assert processor.keyword_matches == 1
    assert processor.saved_matches == 0


def test_processor_notifies_after_successful_sheet_export(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    calls = []

    async def successful_export(**_kwargs) -> bool:
        calls.append("sheet")
        return True

    async def notify_vacancy(**kwargs) -> bool:
        calls.append("notify")
        assert kwargs["vacancy_id"] == "jobs_42"
        return True

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=successful_export,
        notify_vacancy=notify_vacancy,
    )

    assert asyncio.run(
        processor.process_message(
            "Go vacancy",
            "Go vacancy",
            "https://t.me/jobs/42",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            "jobs",
        )
    )
    assert calls == ["sheet", "notify"]


def test_processor_does_not_notify_when_sheet_export_fails(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    async def failed_export(**_kwargs) -> bool:
        return False

    async def notify_vacancy(**_kwargs) -> bool:
        raise AssertionError("notifier must not be called")

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=failed_export,
        notify_vacancy=notify_vacancy,
    )

    assert not asyncio.run(
        processor.process_message(
            "Go vacancy",
            "Go vacancy",
            "https://t.me/jobs/42",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            "jobs",
        )
    )


def test_processor_marks_export_when_notifier_fails(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    async def successful_export(**_kwargs) -> bool:
        return True

    async def failing_notifier(**_kwargs) -> bool:
        raise RuntimeError("notification failed")

    class Recorder:
        def __init__(self) -> None:
            self.events = []

        def record_metric(self, event, component='pipeline', reason=None) -> None:
            self.events.append((event, component, reason))

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    state = DedupeStateSpy()
    recorder = Recorder()
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=successful_export,
        dedupe_state=state,
        notify_vacancy=failing_notifier,
        event_recorder=recorder,
    )

    assert asyncio.run(
        processor.process_message(
            "Go vacancy",
            "Go vacancy",
            "https://t.me/jobs/42",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            "jobs",
        )
    )
    assert len(state.marked) == 1
    assert ('processing_error', 'telegram', 'notification_error') in recorder.events


def test_processor_succeeds_when_notifier_returns_false(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    async def successful_export(**_kwargs) -> bool:
        return True

    async def unsuccessful_notifier(**_kwargs) -> bool:
        return False

    class Recorder:
        def __init__(self) -> None:
            self.events = []

        def record_metric(self, event, component='pipeline', reason=None) -> None:
            self.events.append((event, component, reason))

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    recorder = Recorder()
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=successful_export,
        notify_vacancy=unsuccessful_notifier,
        event_recorder=recorder,
    )

    assert asyncio.run(
        processor.process_message(
            "Go vacancy",
            "Go vacancy",
            "https://t.me/jobs/42",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            "jobs",
        )
    )
    assert ('processing_error', 'telegram', 'notification_error') in recorder.events


def test_grouped_repost_skips_second_sheet_row_and_notification(
    monkeypatch, tmp_path
) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    async def analyze_text(_text: str):
        return validate_analysis_result(valid_payload())

    exports = []
    notifications = []

    async def export(**kwargs) -> bool:
        exports.append(kwargs['vacancy_id'])
        return True

    async def notify(**kwargs) -> bool:
        notifications.append(kwargs['vacancy_id'])
        return True

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=export,
        notify_vacancy=notify,
        group_store=VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), 14),
    )
    published = datetime(2026, 7, 22, tzinfo=timezone.utc)

    assert asyncio.run(
        processor.process_message(
            'Go vacancy one',
            'Go vacancy one',
            'https://t.me/jobs_one/1',
            published,
            'jobs_one',
        )
    )
    assert asyncio.run(
        processor.process_message(
            'Go vacancy two',
            'Go vacancy two',
            'https://t.me/jobs_two/2',
            published,
            'jobs_two',
        )
    )
    assert exports == ['jobs_one_1']
    assert notifications == ['jobs_one_1']


def test_exact_dedupe_still_blocks_llm_and_records_second_source(
    monkeypatch, tmp_path
) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    analyzed = 0
    exports = []

    async def analyze_text(_text: str):
        nonlocal analyzed
        analyzed += 1
        return validate_analysis_result(valid_payload())

    async def export(**kwargs) -> bool:
        exports.append(kwargs['vacancy_id'])
        return True

    monkeypatch.setattr("tg_vacancy_bot.pipeline.processor.asyncio.sleep", no_delay)
    groups = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), 14)
    processor = VacancyProcessor(
        keyword_filter=lambda _text: True,
        analyze_text=analyze_text,
        append_to_sheet=export,
        dedupe_state=JsonlDedupeState(tmp_path / 'state.jsonl', 30),
        group_store=groups,
    )
    published = datetime(2026, 7, 22, tzinfo=timezone.utc)

    assert asyncio.run(
        processor.process_message(
            'same Go vacancy',
            'same Go vacancy',
            'https://t.me/jobs_one/1',
            published,
            'jobs_one',
        )
    )
    assert not asyncio.run(
        processor.process_message(
            'same Go vacancy',
            'same Go vacancy',
            'https://t.me/jobs_two/2',
            published,
            'jobs_two',
        )
    )
    assert analyzed == 1
    assert exports == ['jobs_one_1']
    group = groups.list_groups()[0]
    assert len(groups.get_group(group['group_id'])['publications']) == 2
