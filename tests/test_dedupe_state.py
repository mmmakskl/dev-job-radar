import json
import logging
from datetime import datetime, timedelta, timezone

from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState


def _event(post_link: str, text_hash: str, created_at: datetime) -> str:
    return json.dumps(
        {
            "event": "exported",
            "post_link": post_link,
            "text_hash": text_hash,
            "created_at": created_at.isoformat(),
        }
    )


def test_duplicate_by_link_is_permanent(tmp_path) -> None:
    state_path = tmp_path / "state.jsonl"
    old_date = datetime.now(timezone.utc) - timedelta(days=365)
    state_path.write_text(
        _event("https://t.me/jobs/1", "old-hash", old_date) + "\n",
        encoding="utf-8",
    )

    state = JsonlDedupeState(state_path, ttl_days=30)

    assert state.is_duplicate("https://t.me/jobs/1", "another-hash")


def test_duplicate_by_text_hash_inside_ttl(tmp_path) -> None:
    state_path = tmp_path / "state.jsonl"
    recent_date = datetime.now(timezone.utc) - timedelta(days=1)
    state_path.write_text(
        _event("https://t.me/jobs/1", "recent-hash", recent_date) + "\n",
        encoding="utf-8",
    )

    state = JsonlDedupeState(state_path, ttl_days=30)

    assert state.is_duplicate("https://t.me/other/2", "recent-hash")


def test_text_hash_older_than_ttl_does_not_block(tmp_path) -> None:
    state_path = tmp_path / "state.jsonl"
    old_date = datetime.now(timezone.utc) - timedelta(days=31)
    state_path.write_text(
        _event("https://t.me/jobs/1", "old-hash", old_date) + "\n",
        encoding="utf-8",
    )

    state = JsonlDedupeState(state_path, ttl_days=30)

    assert not state.is_duplicate("https://t.me/other/2", "old-hash")
    assert "old-hash" not in state.recent_text_hashes


def test_broken_jsonl_line_is_skipped(tmp_path, caplog) -> None:
    state_path = tmp_path / "state.jsonl"
    recent_date = datetime.now(timezone.utc)
    state_path.write_text(
        "not-json\n"
        + _event("https://t.me/jobs/1", "valid-hash", recent_date)
        + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        state = JsonlDedupeState(state_path, ttl_days=30)

    assert state.is_duplicate("https://t.me/jobs/1", "different-hash")
    assert "Пропуск битой строки JSONL" in caplog.text


def test_mark_exported_appends_line_and_updates_sets(tmp_path) -> None:
    state_path = tmp_path / "nested" / "state.jsonl"
    state = JsonlDedupeState(state_path, ttl_days=30)

    state.mark_exported("https://t.me/jobs/123", "text-hash")

    assert "https://t.me/jobs/123" in state.exported_links
    assert "text-hash" in state.recent_text_hashes
    saved_event = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved_event["event"] == "exported"
    assert saved_event["post_link"] == "https://t.me/jobs/123"
    assert saved_event["text_hash"] == "text-hash"
    assert datetime.fromisoformat(saved_event["created_at"]).tzinfo is not None


def test_duplicate_by_stable_vacancy_id(tmp_path) -> None:
    state = JsonlDedupeState(tmp_path / "state.jsonl", ttl_days=30)
    state.mark_exported(
        "https://t.me/RemoteGeekJob/40909",
        "hash",
        "remotegeekjob_40909",
    )

    assert state.is_duplicate(
        "https://t.me/other/1",
        "different-hash",
        "remotegeekjob_40909",
    )
