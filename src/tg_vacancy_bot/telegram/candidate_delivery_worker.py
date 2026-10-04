"""Durable personal delivery on the existing Bot API worker and Candidate SQLite."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from tg_vacancy_bot.telegram.bot_api import BotApiRejected
from tg_vacancy_bot.telegram.candidate_card_formatter import (
    CardInput,
    MatchedProfile,
    RenderedMessage,
    format_card,
    format_hourly_digest,
)
from tg_vacancy_bot.telegram.candidate_delivery import (
    DeliveryItem,
    delivery_key,
    plan_deliveries,
)


def personal_card(match) -> CardInput:
    return CardInput(
        match.vacancy_id,
        match.analysis,
        match.published_at,
        match.post_link,
        tuple(MatchedProfile(p.profile_id, p.name) for p in match.matched_profiles),
    )


def determining_profile(match):
    """Manual neither initiates nor blocks delivery; ties use stable profile ID."""
    automatic = [
        p
        for p in match.matched_profiles
        if p.is_active
        and p.direction_id != 'onec'
        and p.preferences.get('delivery_mode') in {'immediate', 'hourly'}
    ]
    return min(
        automatic,
        key=lambda p: (p.preferences['delivery_mode'] != 'immediate', p.profile_id),
        default=None,
    )


class PersonalDeliveryStore:
    """One durable reservation per owner/vacancy, independent of profile and mode."""

    def __init__(self, path: str):
        self.path = path
        with self.connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute(
                '''CREATE TABLE IF NOT EXISTS candidate_personal_deliveries (
                telegram_user_id INTEGER NOT NULL, vacancy_id TEXT NOT NULL,
                state TEXT NOT NULL, token TEXT, updated_at TEXT NOT NULL,
                message_id INTEGER,
                PRIMARY KEY(telegram_user_id, vacancy_id)
            )'''
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def excluded_keys(self, user_id: int, now: datetime) -> set[str]:
        with self.connect() as connection:
            # A reservation has no network side effect. Sending/unknown never expire.
            connection.execute(
                "UPDATE candidate_personal_deliveries SET state='pending',token=NULL "
                "WHERE state='reserved' AND updated_at<?",
                ((now - timedelta(minutes=5)).isoformat(),),
            )
            excluded = {
                row['vacancy_id']
                for row in connection.execute(
                    'SELECT vacancy_id FROM candidate_personal_deliveries '
                    "WHERE telegram_user_id=? AND state!='pending'",
                    (user_id,),
                )
            }
            if self._has_aliases(connection):
                for row in connection.execute('SELECT * FROM registry_aliases'):
                    if row['alias_vacancy_id'] in excluded:
                        excluded.add(row['canonical_vacancy_id'])
            return {delivery_key(user_id, vacancy_id) for vacancy_id in excluded}

    @staticmethod
    def _has_aliases(connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='registry_aliases'"
            ).fetchone()
            is not None
        )

    def reserve(
        self,
        user_id: int,
        ids: tuple[str, ...],
        now: datetime,
        *,
        expected_profiles: dict | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        from tg_vacancy_bot.registry import MATCHER_VERSION

        token, claimed = uuid.uuid4().hex, []
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for vacancy_id in ids:
                if self._has_aliases(connection):
                    if connection.execute(
                        'SELECT 1 FROM registry_aliases WHERE alias_vacancy_id=?',
                        (vacancy_id,),
                    ).fetchone():
                        continue
                    if connection.execute(
                        """SELECT 1 FROM candidate_personal_deliveries d
                        JOIN registry_aliases a ON a.alias_vacancy_id=d.vacancy_id
                        WHERE a.canonical_vacancy_id=? AND d.telegram_user_id=? AND d.state!='pending'""",
                        (vacancy_id, user_id),
                    ).fetchone():
                        continue
                if expected_profiles is not None:
                    profiles = expected_profiles.get(vacancy_id, ())
                    current = all(
                        connection.execute(
                            'SELECT 1 FROM candidate_profiles p '
                            'JOIN registry_profile_matches m ON m.profile_id=p.profile_id AND m.profile_version=p.current_version '
                            'JOIN registry_vacancies v ON v.latest_analysis_id=m.analysis_id '
                            "WHERE p.telegram_user_id=? AND p.profile_id=? AND p.current_version=? AND p.is_active=1 "
                            "AND v.vacancy_id=? AND v.eligible_at IS NOT NULL AND m.status='match' AND m.matcher_version=?",
                            (
                                user_id,
                                profile.profile_id,
                                profile.version,
                                vacancy_id,
                                MATCHER_VERSION,
                            ),
                        ).fetchone()
                        is not None
                        for profile in profiles
                    )
                    if not profiles or not current:
                        continue
                connection.execute(
                    'INSERT OR IGNORE INTO candidate_personal_deliveries '
                    '(telegram_user_id,vacancy_id,state,updated_at) VALUES(?,?,?,?)',
                    (user_id, vacancy_id, 'pending', now.isoformat()),
                )
                if connection.execute(
                    "UPDATE candidate_personal_deliveries SET state='reserved',token=?,updated_at=? "
                    "WHERE telegram_user_id=? AND vacancy_id=? AND state='pending'",
                    (token, now.isoformat(), user_id, vacancy_id),
                ).rowcount:
                    claimed.append(vacancy_id)
        return token, tuple(claimed)

    def transition(
        self, token: str, before: str, after: str, *, message_id: int | None = None
    ) -> bool:
        with self.connect() as connection:
            return bool(
                connection.execute(
                    'UPDATE candidate_personal_deliveries SET state=?,message_id=?,updated_at=? '
                    'WHERE token=? AND state=?',
                    (
                        after,
                        message_id,
                        datetime.now(timezone.utc).isoformat(),
                        token,
                        before,
                    ),
                ).rowcount
            )


class CandidateDeliveryWorker:
    """Revalidate current local matches immediately before every page reservation."""

    def __init__(
        self,
        api,
        registry,
        store: PersonalDeliveryStore,
        *,
        allowed_user_ids: set[int],
        enabled: bool = False,
    ):
        self.api, self.registry, self.store = api, registry, store
        self.allowed_user_ids, self.enabled = allowed_user_ids, enabled

    async def run(self, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            try:
                await self.tick()
            except Exception:
                logging.exception('Ошибка персональной доставки')
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

    async def tick(self, now: datetime | None = None) -> None:
        if not self.enabled:
            return
        now = now or datetime.now(timezone.utc)
        for user_id in sorted(self.allowed_user_ids):
            matches = await asyncio.to_thread(
                self.registry.list_personal_matches, user_id
            )
            groups: dict[tuple[str, str], list[DeliveryItem]] = {}
            for match in matches:
                profile = determining_profile(match)
                if profile is None:
                    continue
                key = (
                    profile.preferences['delivery_mode'],
                    profile.preferences.get('timezone', 'Europe/Moscow'),
                )
                eligible = datetime.fromisoformat(
                    match.eligible_at.replace('Z', '+00:00')
                )
                groups.setdefault(key, []).append(
                    DeliveryItem(personal_card(match), eligible)
                )
            excluded = self.store.excluded_keys(user_id, now)
            for (mode, zone), items in groups.items():
                for batch in plan_deliveries(
                    items,
                    recipient_id=user_id,
                    mode=mode,
                    now=now,
                    reserved_keys=excluded,
                    profile_timezone=zone,
                ):
                    cards = tuple(item.card for item in batch.items)
                    pages = self._pages(cards, mode, zone, now, batch.window_end)
                    for page in pages:
                        if not self.enabled or user_id not in self.allowed_user_ids:
                            return
                        # A profile can change while previous pages are being sent.
                        current = await asyncio.to_thread(
                            self.registry.list_personal_matches, user_id
                        )
                        valid = []
                        expected_profiles = {}
                        for match in current:
                            profile = determining_profile(match)
                            if (
                                match.vacancy_id not in page.vacancy_ids
                                or profile is None
                            ):
                                continue
                            if (
                                profile.preferences['delivery_mode'],
                                profile.preferences.get('timezone', 'Europe/Moscow'),
                            ) == (mode, zone):
                                valid.append(personal_card(match))
                                expected_profiles[match.vacancy_id] = (
                                    match.matched_profiles
                                )
                        # Reserve each rendered page separately so a crash cannot strand
                        # pages which have never reached the Bot API.
                        for fresh in self._pages(
                            tuple(valid), mode, zone, now, batch.window_end
                        ):
                            token, ids = self.store.reserve(
                                user_id,
                                fresh.vacancy_ids,
                                now,
                                expected_profiles=expected_profiles,
                            )
                            if not ids:
                                continue
                            claimed = tuple(
                                card for card in valid if card.vacancy_id in ids
                            )
                            rendered = self._pages(
                                claimed, mode, zone, now, batch.window_end
                            )[0]
                            if not self.store.transition(token, 'reserved', 'sending'):
                                continue
                            try:
                                result = await self.api.send_message(
                                    user_id, rendered.text, parse_mode='HTML'
                                )
                            except BotApiRejected:
                                self.store.transition(token, 'sending', 'pending')
                                return
                            except BaseException:
                                self.store.transition(token, 'sending', 'unknown')
                                raise
                            else:
                                message_id = result.get('message_id')
                                self.store.transition(
                                    token,
                                    'sending',
                                    (
                                        'sent'
                                        if isinstance(message_id, int)
                                        else 'unknown'
                                    ),
                                    message_id=message_id,
                                )

    @staticmethod
    def _pages(cards, mode, zone, now, end):
        if mode == 'hourly':
            return format_hourly_digest(cards, now=now, window_end=end, timezone=zone)
        return tuple(
            RenderedMessage(
                format_card(card, now=now, timezone=zone), (card.vacancy_id,)
            )
            for card in cards
        )
