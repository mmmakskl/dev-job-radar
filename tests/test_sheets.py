from datetime import datetime, timezone

import pytest

from tg_vacancy_bot import config
from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.storage import sheets
from tests.test_llm_schemas import valid_payload


def test_full_and_short_rows(monkeypatch) -> None:
    monkeypatch.setattr(config, "OUTPUT_TIMEZONE", "Europe/Moscow")
    data = validate_analysis_result(valid_payload())
    published_at = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)
    added_at = datetime(2026, 7, 22, 16, 45, tzinfo=timezone.utc)

    full = sheets.build_full_row(
        vacancy_id="jobs_123",
        post_link="https://t.me/jobs/123",
        channel_name="jobs",
        data=data,
        raw_text="Full vacancy",
        published_at=published_at,
        added_at=added_at,
    )
    short = sheets.build_short_row(
        post_link="https://t.me/jobs/123",
        data=data,
        published_at=published_at,
    )

    assert len(sheets.FULL_HEADERS) == len(full) == 37
    assert len(sheets.SHORT_HEADERS) == len(short) == 13
    assert full[0] == "jobs_123"
    assert full[1:3] == ["2026-07-22 18:30", "2026-07-22 19:45"]
    assert full[18:20] == [3000, 4500]
    assert short[3] == "Senior"
    assert short[8] == "3000–4500 USD/Month"
    assert short[11] == "https://example.com/apply"
    assert "Full vacancy" not in short


def test_partial_sheet_failure_is_recovered(monkeypatch) -> None:
    class FakeWorksheet:
        def __init__(self, title: str, fail_once: bool = False) -> None:
            self.title = title
            self.rows = []
            self.notes = {}
            self.fail_once = fail_once

        def col_values(self, _column: int):
            return [sheets.FULL_HEADERS[0]] + [row[0] for row in self.rows]

        def get_notes(self, grid_range: str):
            return [[note] for note in self.notes.values()]

        def append_rows(self, rows, **_kwargs):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("temporary short-sheet failure")
            self.rows.extend(rows)
            row_number = len(self.rows) + 1
            return {
                "updates": {
                    "updatedRange": f"'{self.title}'!A{row_number}:M{row_number}"
                }
            }

        def insert_note(self, cell: str, note: str):
            self.notes[cell] = note

        def get_all_values(self):
            return [["header"]] + self.rows

    full_sheet = FakeWorksheet(config.GOOGLE_SHEET_FULL_TITLE)
    short_sheet = FakeWorksheet(config.GOOGLE_SHEET_SHORT_TITLE, fail_once=True)
    spreadsheet = object()
    client = type("Client", (), {"open_by_url": lambda self, _url: spreadsheet})()

    monkeypatch.setattr(sheets.gspread, "service_account", lambda **_kwargs: client)
    monkeypatch.setattr(
        sheets,
        "_get_or_create_worksheet",
        lambda _book, title, _headers, full: (
            full_sheet if title == config.GOOGLE_SHEET_FULL_TITLE else short_sheet
        ),
    )
    data = validate_analysis_result(valid_payload())
    kwargs = {
        "vacancy_id": "jobs_123",
        "post_link": "https://t.me/jobs/123",
        "channel_name": "jobs",
        "data": data,
        "raw_text": "Go vacancy",
        "published_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
    }

    with pytest.raises(RuntimeError):
        sheets._export_to_sheets_sync(**kwargs)

    assert len(full_sheet.rows) == 1
    assert len(short_sheet.rows) == 0
    assert sheets._export_to_sheets_sync(**kwargs)
    assert len(full_sheet.rows) == 1
    assert len(short_sheet.rows) == 1
    assert "vacancy_id:jobs_123" in short_sheet.notes.values()
