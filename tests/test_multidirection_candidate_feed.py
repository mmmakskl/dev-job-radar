"""Offline route checks for Go and catalog-backed personal vacancy tracks."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tests.test_candidate_bot import FakeBotApi, message
from tests.test_universal_analysis import _payload
from tg_vacancy_bot.llm.universal import _validate
from tg_vacancy_bot.pipeline.prefilter import universal_prefilter
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_delivery_worker import (
    CandidateDeliveryWorker,
    PersonalDeliveryStore,
)
from tg_vacancy_bot.telegram.candidate_store import CandidateStore

TRACKS = (
    ("go", "development", "backend", "backend_developer", "go", "Go"),
    (
        "frontend",
        "development",
        "frontend",
        "frontend_developer",
        "react",
        "React",
    ),
    ("qa", "qa", "qa_engineer", "qa_engineer", "api", "API"),
    (
        "hr",
        "hr_recruiting",
        "recruiting",
        "it_recruiter",
        "sourcing",
        "Sourcing",
    ),
    (
        "onec",
        "development",
        "onec_development",
        "onec_developer",
        "1c_enterprise",
        "1C:Предприятие",
    ),
)


def decision_for(direction, specialization, role, stack, title):
    payload = _payload()
    payload["confidence"] = 97
    payload["classifications"] = [
        {
            "direction_id": direction,
            "specialization_id": specialization,
            "role_id": role,
            "confidence": 97,
        }
    ]
    payload["analysis"].update(
        title=title,
        summary=f"Ищем специалиста. Основной инструмент: {stack}.",
        required_stack=[stack],
        preferred_stack=[],
        requirements=f"Опыт работы с {stack} обязателен.",
        is_match=True,
    )
    return _validate(payload)


def ingest(registry, vacancy_id, decision):
    return registry.ingest(
        vacancy_id=vacancy_id,
        post_link=f"https://t.me/jobs/{vacancy_id}",
        decision=decision,
        published_at="2026-10-04T08:00:00+00:00",
        eligible_at="2026-10-04T08:01:00+00:00",
        raw_text=decision.analysis.summary,
        channel_name="jobs",
    )


@pytest.mark.parametrize(
    "track,direction,specialization,role,stack,label",
    TRACKS,
    ids=[x[0] for x in TRACKS],
)
def test_personal_feed_and_delivery_are_scoped_to_catalog_track(
    tmp_path, track, direction, specialization, role, stack, label
):
    store = CandidateStore(str(tmp_path / f"{track}.sqlite3"))
    registry = VacancyRegistry(store.path)
    active = store.create_profile(
        1,
        name=track,
        direction_id=direction,
        specialization_id=specialization,
        role_id=role,
        preferences={
            "profile_contract": "catalog-v3",
            "required_skills": [stack.lower()],
            "delivery_mode": "immediate" if track == "go" else "manual",
        },
    )
    own = decision_for(direction, specialization, role, stack, f"{label} role")
    foreign = (
        decision_for(
            "development", "frontend", "frontend_developer", "React", "Frontend role"
        )
        if track == "go"
        else decision_for(
            "development", "backend", "backend_developer", "Go", "Go backend role"
        )
    )
    ingest(registry, "own", own)
    ingest(registry, "foreign", foreign)

    matches = registry.list_personal_matches(1)
    assert [match.vacancy_id for match in matches] == ["own"]
    assert matches[0].matched_profiles[0].profile_id == active.profile_id

    api = FakeBotApi()
    bot = CandidateBot(api, store, {1}, registry=registry)
    asyncio.run(bot.handle_update(message(1, "/start")))
    buttons = api.messages[-1][2]["reply_markup"]["keyboard"]
    assert buttons[0] == ["Новые · 24 часа", "Ранее · за 7 дней"]
    assert buttons[1] == [
        "Сохранённые",
        "Без даты",
    ]
    asyncio.run(bot.handle_update(message(1, "Для меня · активный профиль")))
    assert "1 / 1" in api.messages[-1][1]
    assert f"{label} role" in api.messages[-1][1]
    assert foreign.analysis.title not in api.messages[-1][1]

    delivery_store = PersonalDeliveryStore(store.path)
    worker = CandidateDeliveryWorker(
        api,
        registry,
        delivery_store,
        allowed_user_ids={1},
        enabled=True,
    )
    asyncio.run(worker.tick(datetime(2026, 10, 4, 12, tzinfo=timezone.utc)))
    with delivery_store.connect() as connection:
        rows = connection.execute(
            "SELECT vacancy_id,state FROM candidate_personal_deliveries"
        ).fetchall()
    assert [(row["vacancy_id"], row["state"]) for row in rows] == (
        [("own", "sent")] if track == "go" else []
    )
    assert store.get_active_profile(1).profile_id == active.profile_id


def test_switching_active_profile_changes_feed_without_leaking_previous_track(tmp_path):
    store = CandidateStore(str(tmp_path / "switch.sqlite3"))
    registry = VacancyRegistry(store.path)
    go = store.create_profile(
        1,
        name="Go",
        direction_id="development",
        specialization_id="",
        role_id="",
        preferences={"stacks": ["go"]},
    )
    hr = store.create_profile(
        1,
        name="HR",
        direction_id="hr_recruiting",
        specialization_id="",
        role_id="",
        preferences={"stacks": ["sourcing"]},
        is_active=False,
    )
    ingest(
        registry,
        "go-job",
        decision_for("development", "backend", "backend_developer", "Go", "Go vacancy"),
    )
    ingest(
        registry,
        "hr-job",
        decision_for(
            "hr_recruiting", "recruiting", "it_recruiter", "Sourcing", "HR vacancy"
        ),
    )
    assert [match.vacancy_id for match in registry.list_personal_matches(1)] == [
        "go-job"
    ]

    store.activate_profile(1, hr.profile_id)
    assert [match.vacancy_id for match in registry.list_personal_matches(1)] == [
        "hr-job"
    ]
    assert store.get_profile(1, go.profile_id).is_active is False
    assert store.get_active_profile(1).profile_id == hr.profile_id


def test_universal_ingestion_keeps_non_go_analysis_out_of_go_projection(tmp_path):
    store = CandidateStore(str(tmp_path / "ingestion.sqlite3"))
    registry = VacancyRegistry(store.path)
    profile = store.create_profile(
        1,
        name="Frontend",
        direction_id="development",
        specialization_id="",
        role_id="",
        preferences={"stacks": ["react"]},
    )
    analyzed = decision_for(
        "development", "frontend", "frontend_developer", "React", "Frontend vacancy"
    )
    append_to_sheet = AsyncMock(return_value=True)
    processor = VacancyProcessor(
        keyword_filter=universal_prefilter,
        analyze_text=AsyncMock(return_value=analyzed),
        append_to_sheet=append_to_sheet,
        registry=registry,
    )
    result = asyncio.run(
        processor.process_message(
            text="Вакансия frontend-разработчика. Требуется React.",
            raw_text="Вакансия frontend-разработчика. Требуется React.",
            post_link="https://t.me/jobs/frontend-1",
            published_at=datetime(2026, 10, 4, 8, tzinfo=timezone.utc),
            channel_name="frontend-jobs",
        )
    )
    assert result is True
    append_to_sheet.assert_not_awaited()
    matches = registry.list_personal_matches(1)
    assert len(matches) == 1
    assert matches[0].matched_profiles[0].profile_id == profile.profile_id
    assert registry.candidates.get_vacancy(matches[0].callback_key) is not None
    assert registry.candidates.list_for_user(1, "new") == []


def test_go_only_registry_shows_empty_frontend_feed_instead_of_go_fallback(tmp_path):
    store = CandidateStore(str(tmp_path / "go-only.sqlite3"))
    registry = VacancyRegistry(store.path)
    store.create_profile(
        1,
        name="Frontend",
        direction_id="development",
        specialization_id="",
        role_id="",
        preferences={"stacks": ["react"]},
    )
    go = decision_for(
        "development", "backend", "backend_developer", "Go", "Go-only vacancy"
    )
    ingest(registry, "go-only", go)

    assert registry.list_personal_matches(1) == []
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1}, registry=registry)
    asyncio.run(bot.handle_update(message(1, "Для меня · активный профиль")))
    assert len(api.messages) == 1
    assert "Подтверждённых совпадений" in api.messages[0][1]
    assert "Go-only vacancy" not in api.messages[0][1]
