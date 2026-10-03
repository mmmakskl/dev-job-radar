from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.test_llm_schemas import valid_payload
from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.telegram.candidate_card_formatter import CardInput, MatchedProfile
from tg_vacancy_bot.telegram.candidate_delivery import (
    DeliveryItem,
    delivery_key,
    plan_deliveries,
)

NOW = datetime(2026, 10, 4, 9, 30, tzinfo=timezone.utc)


def item(
    vacancy_id: str = "vacancy-1",
    *,
    eligible_at: datetime = NOW - timedelta(hours=1),
    profiles: tuple[MatchedProfile, ...] = (MatchedProfile("go", "Go backend"),),
    title: str = "Senior Go Developer",
    published_at: datetime | str | None = None,
) -> DeliveryItem:
    analysis = validate_analysis_result(valid_payload(title=title))
    assert analysis is not None
    card = CardInput(
        vacancy_id=vacancy_id,
        analysis=analysis,
        published_at=published_at,
        post_link="https://example.com/source",
        matched_profiles=profiles,
    )
    return DeliveryItem(card, eligible_at)


def plan(items, mode="immediate", **kwargs):
    return plan_deliveries(items, recipient_id=123, mode=mode, now=NOW, **kwargs)


def test_manual_and_empty_plans() -> None:
    assert plan([item()], "manual") == ()
    assert plan([]) == ()
    assert plan([], "hourly") == ()


def test_immediate_includes_exact_now_and_orders_by_time_then_id() -> None:
    batches = plan(
        [
            item("later", eligible_at=NOW + timedelta(microseconds=1)),
            item("now", eligible_at=NOW),
            item("b"),
            item("a"),
        ]
    )
    assert [batch.items[0].card.vacancy_id for batch in batches] == ["a", "b", "now"]
    assert all(
        batch.mode == "immediate" and batch.window_end is None for batch in batches
    )
    assert all(len(batch.item_keys) == 1 for batch in batches)


def test_duplicates_keep_first_card_earliest_ready_time_and_all_profiles() -> None:
    first = item(
        profiles=(MatchedProfile("go", "Go backend"), MatchedProfile("go", "Old")),
        title="First title",
        published_at="2026-10-01T01:00:00Z",
    )
    earlier = item(
        eligible_at=NOW - timedelta(days=1),
        profiles=(MatchedProfile("platform", "Platform"), MatchedProfile("go", "New")),
        title="Later title",
        published_at=None,
    )
    merged = plan([first, earlier])[0].items[0]
    assert merged.eligible_at == earlier.eligible_at
    assert merged.card.analysis is first.card.analysis
    assert merged.card.published_at == first.card.published_at
    assert merged.card.post_link == first.card.post_link
    assert merged.card.matched_profiles == (
        MatchedProfile("go", "Go backend"),
        MatchedProfile("platform", "Platform"),
    )
    assert len(first.card.matched_profiles) == 2


def test_hourly_uses_completed_utc_hour_and_excludes_exact_boundary() -> None:
    boundary = NOW.replace(minute=0)
    batch = plan(
        [
            item("boundary", eligible_at=boundary),
            item("before", eligible_at=boundary - timedelta(microseconds=1)),
            item("after", eligible_at=boundary + timedelta(seconds=1)),
            item("backlog", eligible_at=NOW - timedelta(days=7)),
        ],
        "hourly",
        last_hourly_at=boundary - timedelta(hours=1),
    )[0]
    assert batch.window_end == boundary
    assert [entry.card.vacancy_id for entry in batch.items] == ["backlog", "before"]
    assert batch.item_keys == (
        delivery_key(123, "backlog"),
        delivery_key(123, "before"),
    )
    following = plan_deliveries(
        [item("boundary", eligible_at=boundary)],
        recipient_id=123,
        mode="hourly",
        now=boundary + timedelta(hours=1),
        last_hourly_at=boundary,
    )
    assert len(following[0].items) == 1


@pytest.mark.parametrize("delta", [timedelta(), timedelta(hours=1)])
def test_hourly_does_not_repeat_or_go_backwards(delta) -> None:
    assert plan([item()], "hourly", last_hourly_at=NOW.replace(minute=0) + delta) == ()


def test_hourly_does_not_send_empty_windows() -> None:
    assert plan([item(eligible_at=NOW)], "hourly") == ()
    assert (
        plan([item()], "hourly", reserved_keys=[delivery_key(123, "vacancy-1")]) == ()
    )


def test_delivered_and_reserved_item_keys_survive_mode_and_profile_changes() -> None:
    first_batch = plan([item()])[0]
    revised = item(
        title="Changed title",
        profiles=(MatchedProfile("platform", "Platform"),),
        published_at=NOW,
    )
    for mode in ("immediate", "hourly"):
        assert plan([revised], mode, delivered_keys=first_batch.item_keys) == ()
        assert plan([revised], mode, reserved_keys=first_batch.item_keys) == ()
    assert (
        len(plan_deliveries([revised], recipient_id=456, mode="hourly", now=NOW)) == 1
    )


def test_exclusions_and_deduplication_preserve_remaining_batch_keys() -> None:
    batch = plan(
        [item("a"), item("a"), item("b"), item("c")],
        "hourly",
        delivered_keys=(delivery_key(123, "a"),),
        reserved_keys=(delivery_key(123, "b"),),
    )[0]
    assert [entry.card.vacancy_id for entry in batch.items] == ["c"]
    assert batch.item_keys == (delivery_key(123, "c"),)


def test_keys_are_versioned_structured_and_recipient_specific() -> None:
    key = delivery_key(123, "vacancy-1")
    assert key.startswith("candidate-delivery:v1:")
    assert len(key.rsplit(":", 1)[1]) == 64
    assert key == delivery_key("123", "vacancy-1")
    assert key != delivery_key(124, "vacancy-1")
    assert delivery_key("a:b", "c") != delivery_key("a", "b:c")
    assert delivery_key("юзер", "работа") == delivery_key("юзер", "работа")


def test_batch_identity_depends_on_mode_hour_and_ordered_item_keys() -> None:
    items = [item("b"), item("a")]
    hourly = plan(items, "hourly")[0]
    assert hourly.key == plan(list(reversed(items)), "hourly")[0].key
    assert hourly.key != plan([item("a")], "hourly")[0].key
    assert hourly.key != plan([item("a")])[0].key
    next_hour = plan_deliveries(
        items, recipient_id=123, mode="hourly", now=NOW + timedelta(hours=1)
    )[0]
    assert next_hour.key != hourly.key


def test_publication_time_has_no_effect_on_delivery() -> None:
    ready = item(published_at=NOW + timedelta(days=100))
    unpublished = item(published_at="damaged")
    assert plan([ready])[0].item_keys == plan([unpublished])[0].item_keys
    assert (
        plan(
            [
                item(
                    eligible_at=NOW + timedelta(days=1),
                    published_at="2000-01-01T00:00:00Z",
                )
            ]
        )
        == ()
    )


def test_timezone_offsets_day_rollover_and_dst_use_absolute_instants() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1)
    earlier_fold = datetime(2026, 10, 25, 2, 45, tzinfo=berlin, fold=0)
    batch = plan_deliveries(
        [item(eligible_at=earlier_fold)],
        recipient_id=123,
        mode="hourly",
        now=now,
        last_hourly_at=datetime(2026, 10, 25, 2, tzinfo=berlin, fold=0),
    )[0]
    assert batch.window_end == datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
    assert len(batch.items) == 1
    midnight = datetime(2026, 10, 5, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert plan_deliveries(
        [item(eligible_at=midnight - timedelta(hours=1))],
        recipient_id=123,
        mode="hourly",
        now=midnight,
    )[0].window_end == datetime(2026, 10, 4, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("field", ["now", "eligible_at", "last_hourly_at"])
def test_naive_timestamps_are_rejected(field) -> None:
    naive = NOW.replace(tzinfo=None)
    kwargs = dict(recipient_id=123, mode="immediate", now=NOW)
    entries = [item()]
    if field == "eligible_at":
        entries = [item(eligible_at=naive)]
    else:
        kwargs[field] = naive
    with pytest.raises(ValueError, match=field):
        plan_deliveries(entries, **kwargs)


def test_unknown_mode_is_rejected_and_records_are_frozen() -> None:
    with pytest.raises(ValueError, match="mode"):
        plan([item()], "weekly")
    entry = item()
    batch = plan([entry])[0]
    with pytest.raises(FrozenInstanceError):
        entry.eligible_at = NOW
    with pytest.raises(FrozenInstanceError):
        batch.mode = "manual"
    assert replace(entry, eligible_at=NOW).eligible_at == NOW
