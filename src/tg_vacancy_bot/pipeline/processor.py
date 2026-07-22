"""Единая бизнес-логика обработки live и исторических сообщений."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from tg_vacancy_bot.models import VacancyAnalysis
from tg_vacancy_bot.pipeline.fingerprints import build_text_hash
from tg_vacancy_bot.telegram.links import build_vacancy_id


KeywordFilter = Callable[[str], bool]
AnalyzeText = Callable[[str], Awaitable[VacancyAnalysis | None]]
AppendToSheet = Callable[..., Awaitable[bool]]


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


class VacancyProcessor:
    """Фильтрует, анализирует и сохраняет подходящие вакансии."""

    def __init__(
        self,
        keyword_filter: KeywordFilter,
        analyze_text: AnalyzeText,
        append_to_sheet: AppendToSheet,
        dedupe_state: DedupeState | None = None,
    ) -> None:
        self.keyword_filter = keyword_filter
        self.analyze_text = analyze_text
        self.append_to_sheet = append_to_sheet
        self.dedupe_state = dedupe_state
        self.keyword_matches = 0
        self.saved_matches = 0

    async def process_message(
        self,
        text: str,
        raw_text: str,
        post_link: str,
        published_at: datetime,
        channel_name: str,
    ) -> bool:
        """Обрабатывает сообщение и возвращает True после успешной записи."""
        if not text:
            return False

        text_hash = build_text_hash(raw_text)
        vacancy_id = build_vacancy_id(post_link)
        if (
            self.dedupe_state is not None
            and self.dedupe_state.is_duplicate(post_link, text_hash, vacancy_id)
        ):
            logging.info("Пропуск: дубликат вакансии (%s)", post_link)
            return False

        if not self.keyword_filter(text):
            return False

        self.keyword_matches += 1
        logging.info("Найдено сообщение с ключевыми словами: %s", post_link)
        logging.debug("Текст: %s...", text[:200])
        logging.info("Отправляем на анализ в Mistral...")

        await asyncio.sleep(1.5)
        analysis_result = await self.analyze_text(text)
        if analysis_result is None:
            logging.error("Не удалось проанализировать вакансию")
            return False

        if analysis_result.is_match is not True:
            logging.info("Вакансия не подходит по критериям")
            return False

        logging.info(
            "Релевантная вакансия: %s | %s",
            analysis_result.company,
            analysis_result.title,
        )
        logging.info("Стек: %s", ", ".join(analysis_result.required_stack))

        saved = await self.append_to_sheet(
            vacancy_id=vacancy_id,
            post_link=post_link,
            channel_name=channel_name,
            data=analysis_result,
            raw_text=raw_text,
            published_at=published_at,
        )
        if not saved:
            return False

        if self.dedupe_state is not None:
            self.dedupe_state.mark_exported(post_link, text_hash, vacancy_id)
        self.saved_matches += 1
        return True
