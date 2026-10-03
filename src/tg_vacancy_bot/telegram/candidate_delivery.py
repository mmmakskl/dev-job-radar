"""Pure delivery planning for confirmed matches; persistence belongs to callers."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable
from zoneinfo import ZoneInfo

from tg_vacancy_bot.telegram.candidate_card_formatter import CardInput


@dataclass(frozen=True)
class DeliveryItem:
    """A confirmed match and the time it became ready for delivery."""

    card: CardInput
    eligible_at: datetime


@dataclass(frozen=True)
class DeliveryBatch:
    """One planned send; item keys must be reserved atomically before sending."""

    key: str
    mode: str
    window_end: datetime | None
    items: tuple[DeliveryItem, ...]
    item_keys: tuple[str, ...]


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _key(kind: str, parts: list[object]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"candidate-{kind}:v1:{digest}"


def delivery_key(recipient_id: str | int, vacancy_id: str) -> str:
    """Stable identity across delivery modes, profiles and card revisions."""
    return _key("delivery", ["v1", str(recipient_id), vacancy_id])


def _merge_items(items: Iterable[DeliveryItem]) -> tuple[DeliveryItem, ...]:
    merged: dict[str, DeliveryItem] = {}
    for item in items:
        _utc(item.eligible_at, "eligible_at")
        vacancy_id = item.card.vacancy_id
        current = merged.get(vacancy_id)
        profiles = list(current.card.matched_profiles if current else ())
        seen = {profile.profile_id for profile in profiles}
        for profile in item.card.matched_profiles:
            if profile.profile_id not in seen:
                profiles.append(profile)
                seen.add(profile.profile_id)
        first_card = current.card if current else item.card
        earliest = item.eligible_at
        if current and _utc(current.eligible_at, "eligible_at") <= _utc(
            earliest, "eligible_at"
        ):
            earliest = current.eligible_at
        merged[vacancy_id] = DeliveryItem(
            replace(first_card, matched_profiles=tuple(profiles)), earliest
        )
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (
                _utc(item.eligible_at, "eligible_at"),
                item.card.vacancy_id,
            ),
        )
    )


def _batch(
    recipient_id: str | int,
    mode: str,
    window_end: datetime | None,
    items: tuple[DeliveryItem, ...],
) -> DeliveryBatch:
    item_keys = tuple(
        delivery_key(recipient_id, item.card.vacancy_id) for item in items
    )
    key = _key(
        "batch",
        [
            "v1",
            str(recipient_id),
            mode,
            window_end.isoformat() if window_end else None,
            list(item_keys),
        ],
    )
    return DeliveryBatch(key, mode, window_end, items, item_keys)


def plan_deliveries(
    items: Iterable[DeliveryItem],
    *,
    recipient_id: str | int,
    mode: str,
    now: datetime,
    last_hourly_at: datetime | None = None,
    delivered_keys: Iterable[str] = (),
    reserved_keys: Iterable[str] = (),
    profile_timezone: str = "UTC",
) -> tuple[DeliveryBatch, ...]:
    """Plan automatic sends without changing delivery state.

    The caller supplies only confirmed matches for active profiles. Before sending,
    atomically reserve the item keys; confirm keys only for successfully sent pages.
    Publication timestamps never affect eligibility.
    """
    if mode not in {"manual", "immediate", "hourly"}:
        raise ValueError(f"Unknown delivery mode: {mode}")
    now_utc = _utc(now, "now")
    last_hourly_utc = (
        _utc(last_hourly_at, "last_hourly_at") if last_hourly_at is not None else None
    )
    merged = _merge_items(items)
    if mode == "manual":
        return ()
    excluded = set(delivered_keys) | set(reserved_keys)
    available = tuple(
        item
        for item in merged
        if delivery_key(recipient_id, item.card.vacancy_id) not in excluded
    )
    if mode == "immediate":
        return tuple(
            _batch(recipient_id, mode, None, (item,))
            for item in available
            if _utc(item.eligible_at, "eligible_at") <= now_utc
        )
    window_end = (
        now_utc.astimezone(ZoneInfo(profile_timezone))
        .replace(minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    if last_hourly_utc is not None and window_end <= last_hourly_utc:
        return ()
    ready = tuple(
        item for item in available if _utc(item.eligible_at, "eligible_at") < window_end
    )
    return (_batch(recipient_id, mode, window_end, ready),) if ready else ()
