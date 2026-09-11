"""Единая бизнес-логика обработки live и исторических сообщений."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tg_vacancy_bot.models import VacancyAnalysis
from tg_vacancy_bot.pipeline.fingerprints import build_text_hash
from tg_vacancy_bot.pipeline.prefilter import candidate_profile_reasons
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
from tg_vacancy_bot.telegram.links import build_vacancy_id

KeywordFilter = Callable[[str], bool]
AnalyzeText = Callable[[str], Awaitable[VacancyAnalysis | None]]
AppendToSheet = Callable[..., Awaitable[bool]]
NotifyVacancy = Callable[..., Awaitable[bool]]


class EventRecorder(Protocol):
    """Minimal observability interface; it never receives post text."""

    def record_metric(
        self,
        event: str,
        component: str = 'pipeline',
        reason: str | None = None,
    ) -> None: ...


class DedupeState(Protocol):
    """Интерфейс состояния дедупликации для pipeline."""

    def is_duplicate(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> bool: ...

    def mark_exported(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class PersistenceOutcome:
    outcome: str
    saved: bool
    vacancy_id: str
    group_id: str | None = None


class VacancyProcessor:
    """Фильтрует, анализирует и сохраняет подходящие вакансии."""

    def __init__(
        self,
        keyword_filter: KeywordFilter,
        analyze_text: AnalyzeText,
        append_to_sheet: AppendToSheet,
        dedupe_state: DedupeState | None = None,
        notify_vacancy: NotifyVacancy | None = None,
        exclude_keywords: list[str] | None = None,
        event_recorder: EventRecorder | None = None,
        group_store: VacancyGroupStore | None = None,
    ) -> None:
        self.keyword_filter = keyword_filter
        self.analyze_text = analyze_text
        self.append_to_sheet = append_to_sheet
        self.dedupe_state = dedupe_state
        self.notify_vacancy = notify_vacancy
        self.exclude_keywords = [item.casefold() for item in (exclude_keywords or [])]
        self.event_recorder = event_recorder
        self.group_store = group_store
        self.keyword_matches = 0
        self.saved_matches = 0
        self._persistence_lock = asyncio.Lock()

    async def process_message(
        self,
        text: str,
        raw_text: str,
        post_link: str,
        published_at: datetime,
        channel_name: str,
    ) -> bool:
        """Обрабатывает сообщение и возвращает True после успешной записи."""
        self._metric('post_processed')
        if not text:
            self._metric('skipped_invalid', reason='empty_text')
            return False

        text_hash = build_text_hash(raw_text)
        vacancy_id = build_vacancy_id(post_link)
        if self.dedupe_state is not None and self.dedupe_state.is_duplicate(
            post_link, text_hash, vacancy_id
        ):
            return self._skip_duplicate(
                post_link, text_hash, vacancy_id, channel_name, published_at
            )

        claim_owner: str | None = None
        claim = getattr(self.dedupe_state, 'claim', None)
        if callable(claim):
            claim_owner = claim(post_link, text_hash, vacancy_id)
            if claim_owner is None:
                self._metric('skipped_duplicate', reason='duplicate_fingerprint')
                return False

        try:
            # A competing worker may have finished and released its claim after
            # our first check but before this claim was acquired. Re-read the
            # append-only authority while we own the claim.
            if claim_owner is not None and self.dedupe_state.is_duplicate(
                post_link, text_hash, vacancy_id
            ):
                return self._skip_duplicate(
                    post_link, text_hash, vacancy_id, channel_name, published_at
                )
            return await self._process_claimed(
                text,
                raw_text,
                post_link,
                published_at,
                channel_name,
                text_hash,
                vacancy_id,
            )
        finally:
            release = getattr(self.dedupe_state, 'release', None)
            if claim_owner is not None and callable(release):
                release(claim_owner)

    def _skip_duplicate(
        self,
        post_link: str,
        text_hash: str,
        vacancy_id: str,
        channel_name: str,
        published_at: datetime,
    ) -> bool:
        logging.info("Пропуск: дубликат вакансии")
        reason_getter = getattr(self.dedupe_state, 'duplicate_reason', None)
        duplicate_reason = (
            reason_getter(post_link, text_hash, vacancy_id)
            if callable(reason_getter)
            else None
        ) or 'duplicate_fingerprint'
        self._metric('skipped_duplicate', reason=duplicate_reason)
        self._metric('exact_duplicate')
        if self.group_store is not None:
            self.group_store.record_exact_repost(
                vacancy_id=vacancy_id,
                post_link=post_link,
                channel_name=channel_name,
                published_at=published_at,
                text_hash=text_hash,
            )
        return False

    async def _process_claimed(
        self,
        text: str,
        raw_text: str,
        post_link: str,
        published_at: datetime,
        channel_name: str,
        text_hash: str,
        vacancy_id: str,
    ) -> bool:
        if not self.keyword_filter(text):
            self._metric('skipped_not_relevant', reason='include_prefilter')
            return False

        if any(keyword in text.casefold() for keyword in self.exclude_keywords):
            logging.info("Пропуск: исключающее ключевое слово")
            self._metric('skipped_not_relevant', reason='exclude_keywords')
            return False

        self.keyword_matches += 1
        logging.info("Найдено сообщение с ключевыми словами")

        profile_reasons = candidate_profile_reasons(text)
        if profile_reasons:
            logging.info(
                "Пропуск: сообщение похоже на резюме кандидата. Признаки: %s",
                ", ".join(profile_reasons),
            )
            self._metric('skipped_not_relevant', reason='candidate_resume')
            return False

        logging.info("Отправляем на анализ в Mistral...")

        await asyncio.sleep(1.5)
        analysis_result = await self.analyze_text(text)
        if analysis_result is None:
            logging.error("[LLM] Не удалось проанализировать вакансию")
            self._metric('processing_error', 'llm', 'llm_error')
            return False

        if analysis_result.is_match is not True:
            logging.info("Вакансия не подходит по критериям")
            self._metric('skipped_not_relevant', reason='llm_not_match')
            return False

        outcome = await self.persist_analyzed_message(
            raw_text=raw_text,
            post_link=post_link,
            published_at=published_at,
            channel_name=channel_name,
            analysis_result=analysis_result,
            publish=self.notify_vacancy is not None,
        )
        return outcome.saved

    async def persist_analyzed_message(
        self,
        *,
        raw_text: str,
        post_link: str,
        published_at: datetime,
        channel_name: str,
        analysis_result: VacancyAnalysis,
        publish: bool = False,
        strict_delivery: bool = False,
    ) -> PersistenceOutcome:
        """Serialize Sheets/group writes across live and Premium; save before publish."""
        if publish and self.notify_vacancy is None:
            raise ValueError('publisher_not_configured')
        vacancy_id = build_vacancy_id(post_link)
        text_hash = build_text_hash(raw_text)
        async with self._persistence_lock:
            if self.dedupe_state is not None and self.dedupe_state.is_duplicate(
                post_link, text_hash, vacancy_id
            ):
                self._skip_duplicate(
                    post_link, text_hash, vacancy_id, channel_name, published_at
                )
                # Only the exact saved canonical post can be published by a later action.
                group = (
                    self.group_store.preview_publication(
                        vacancy_id=vacancy_id,
                        data=analysis_result,
                        published_at=published_at,
                    )
                    if self.group_store
                    else None
                )
                exact = post_link in getattr(
                    self.dedupe_state, 'exported_links', set()
                ) and (group is None or group.is_canonical)
                if (
                    strict_delivery
                    and exact
                    and vacancy_id
                    not in getattr(self.dedupe_state, 'exported_ids', set())
                ):
                    # A legacy/full-sheet link alone does not prove both sheets completed.
                    return await self._finish_persistence(
                        raw_text,
                        post_link,
                        published_at,
                        channel_name,
                        analysis_result,
                        publish,
                        strict_delivery,
                    )
                if publish and exact:
                    outcome = await self._publish(
                        vacancy_id,
                        post_link,
                        channel_name,
                        analysis_result,
                        published_at,
                        strict_delivery,
                    )
                    return PersistenceOutcome(
                        outcome, True, vacancy_id, group.group_id if group else None
                    )
                return PersistenceOutcome(
                    'saved' if exact else 'duplicate',
                    exact,
                    vacancy_id,
                    group.group_id if group else None,
                )
            return await self._finish_persistence(
                raw_text,
                post_link,
                published_at,
                channel_name,
                analysis_result,
                publish,
                strict_delivery,
            )

    async def _finish_persistence(self, *args) -> PersistenceOutcome:
        # A cancelled to_thread await does not stop the Sheets SDK thread. Keep
        # the shared lock until that write finishes before another job can save.
        task = asyncio.create_task(self._persist_unlocked(*args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _publish(
        self, vacancy_id, post_link, channel_name, data, published_at, strict_delivery
    ) -> str:
        try:
            kwargs = dict(
                vacancy_id=vacancy_id,
                post_link=post_link,
                channel_name=channel_name,
                data=data,
                published_at=published_at,
            )
            if strict_delivery:
                kwargs['strict_delivery'] = True
            notified = await self.notify_vacancy(**kwargs)
            if notified:
                return 'published'
        except Exception:
            pass
        self._metric('processing_error', 'telegram', 'notification_error')
        return 'delivery_uncertain' if strict_delivery else 'publish_failed'

    async def _persist_unlocked(
        self,
        raw_text,
        post_link,
        published_at,
        channel_name,
        analysis_result,
        publish,
        strict_delivery,
    ):
        vacancy_id = build_vacancy_id(post_link)
        text_hash = build_text_hash(raw_text)
        group_decision = None
        if self.group_store is not None:
            group_decision = self.group_store.preview_publication(
                vacancy_id=vacancy_id,
                data=analysis_result,
                published_at=published_at,
                fuzzy=strict_delivery,
            )
            if group_decision.left_separate_candidate:
                self._metric('group_candidate_separate')
            if not group_decision.is_canonical:
                self.group_store.register_publication(
                    vacancy_id=vacancy_id,
                    post_link=post_link,
                    channel_name=channel_name,
                    data=analysis_result,
                    published_at=published_at,
                    text_hash=text_hash,
                    fuzzy=strict_delivery,
                )
                if self.dedupe_state is not None:
                    self.dedupe_state.mark_exported(post_link, text_hash, vacancy_id)
                self._metric('grouped_repost')
                logging.info(
                    'Репост объединён с группой %s: %s',
                    group_decision.group_id,
                    group_decision.merge_reason,
                )
                return PersistenceOutcome(
                    'duplicate', True, vacancy_id, group_decision.group_id
                )

        saved = await self.append_to_sheet(
            vacancy_id=vacancy_id,
            post_link=post_link,
            channel_name=channel_name,
            data=analysis_result,
            raw_text=raw_text,
            published_at=published_at,
        )
        if not saved:
            logging.error("[Google Sheets] Не удалось сохранить вакансию")
            self._metric('processing_error', 'google_sheets', 'export_error')
            return PersistenceOutcome('save_failed', False, vacancy_id)

        if self.group_store is not None:
            self.group_store.register_publication(
                vacancy_id=vacancy_id,
                post_link=post_link,
                channel_name=channel_name,
                data=analysis_result,
                published_at=published_at,
                text_hash=text_hash,
            )
        if self.dedupe_state is not None:
            self.dedupe_state.mark_exported(post_link, text_hash, vacancy_id)
        self.saved_matches += 1
        self._metric('vacancy_saved', 'google_sheets')

        outcome = 'saved'
        if publish and self.notify_vacancy is not None:
            outcome = await self._publish(
                vacancy_id,
                post_link,
                channel_name,
                analysis_result,
                published_at,
                strict_delivery,
            )
        return PersistenceOutcome(
            outcome,
            True,
            vacancy_id,
            group_decision.group_id if group_decision else None,
        )

    def _metric(
        self,
        event: str,
        component: str = 'pipeline',
        reason: str | None = None,
    ) -> None:
        if self.event_recorder is not None:
            if reason is None:
                self.event_recorder.record_metric(event, component)
            else:
                self.event_recorder.record_metric(event, component, reason)
