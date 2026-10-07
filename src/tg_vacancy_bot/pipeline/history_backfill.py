"""Catalog-wide Telegram history ingestion without Go publication side effects."""

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from tg_vacancy_bot.llm.universal import (
    AnalysisUnavailable,
    UniversalDecision,
    analyze_universal_text,
)
from tg_vacancy_bot.pipeline.prefilter import universal_prefilter
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.links import build_vacancy_id


@dataclass(frozen=True)
class BackfillResult:
    status: str
    confirmed: bool = False
    reason: str | None = None


async def ingest_registry_history_post(
    registry: VacancyRegistry,
    *,
    text: str,
    post_link: str,
    published_at: datetime,
    channel_name: str,
    analyze: Callable[[str], Awaitable[UniversalDecision]] = analyze_universal_text,
) -> BackfillResult:
    """Analyze a possible vacancy once and retain the full catalog decision."""
    if not universal_prefilter(text):
        return BackfillResult('prefilter_skipped')

    vacancy_id = build_vacancy_id(post_link)
    decision = registry.reusable_decision(vacancy_id, text)
    status = 'reused' if decision is not None else 'analyzed'
    if decision is None:
        try:
            decision = await analyze(text)
        except AnalysisUnavailable as exc:
            if str(exc) == 'daily_limit':
                raise
            registry.ingest(
                vacancy_id=vacancy_id,
                post_link=post_link,
                decision=None,
                raw_text=text,
                channel_name=channel_name,
                published_at=published_at,
                unavailable_reason=str(exc),
            )
            return BackfillResult('unavailable', reason=str(exc))

    entry = registry.ingest(
        vacancy_id=vacancy_id,
        post_link=post_link,
        decision=decision,
        raw_text=text,
        channel_name=channel_name,
        published_at=published_at,
    )
    return BackfillResult(
        status,
        confirmed=entry.eligible_at is not None and entry.decision is not None,
    )
