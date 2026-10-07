"""Shared vacancy analysis registry in the existing Candidate SQLite database.

Ingestion owns source/analysis writes. Candidate reads reuse that analysis and
cache local matches by immutable profile and matcher versions. Identity is an
explicit source ID or exact URL, never inferred from text or stack similarity.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from tg_vacancy_bot.candidate_catalog import PROFILE_CONTRACT
from tg_vacancy_bot.llm.universal import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    UniversalDecision,
    decision_to_dict,
    decision_from_dict,
)
from tg_vacancy_bot.models import VacancyAnalysis
from tg_vacancy_bot.telegram.candidate_store import (
    CandidateProfile,
    CandidateStore,
    callback_key_for,
)

MATCHER_VERSION = 'profile-match.v5'
REGISTRY_VERSION = 2


def _timestamp(value: datetime | str | None) -> str | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value is None or value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decision_payload(decision: UniversalDecision) -> dict:
    return decision_to_dict(decision)


def decision_from_payload(payload: dict) -> UniversalDecision:
    return decision_from_dict(payload)


@dataclass(frozen=True)
class RegistryVacancy:
    vacancy_id: str
    post_link: str
    published_at: str | None
    discovered_at: str
    eligible_at: str | None
    decision: UniversalDecision | None
    analysis_id: int | None
    unavailable_reason: str | None
    raw_text: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class RegistryMatch:
    vacancy_id: str
    analysis: VacancyAnalysis
    decision: UniversalDecision
    published_at: str | None
    post_link: str
    eligible_at: str
    matched_profiles: tuple[CandidateProfile, ...]
    callback_key: str


class VacancyRegistry:
    """Additive, versioned registry; connections use WAL and short transactions."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.candidates = CandidateStore(path)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        return self.candidates._connect()

    def _migrate(self) -> None:
        with self._connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS registry_schema_versions (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS registry_vacancies (
                    vacancy_id TEXT PRIMARY KEY,
                    post_link TEXT NOT NULL,
                    published_at TEXT,
                    discovered_at TEXT NOT NULL,
                    eligible_at TEXT,
                    latest_analysis_id INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS registry_aliases (
                    alias_vacancy_id TEXT PRIMARY KEY REFERENCES registry_vacancies(vacancy_id),
                    canonical_vacancy_id TEXT NOT NULL REFERENCES registry_vacancies(vacancy_id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS registry_sources (
                    source_type TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    vacancy_id TEXT NOT NULL REFERENCES registry_vacancies(vacancy_id),
                    post_link TEXT NOT NULL,
                    published_at TEXT,
                    discovered_at TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(source_type, external_id)
                );
                CREATE INDEX IF NOT EXISTS registry_source_link
                    ON registry_sources(post_link);
                CREATE TABLE IF NOT EXISTS registry_analyses (
                    analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vacancy_id TEXT NOT NULL REFERENCES registry_vacancies(vacancy_id),
                    payload_hash TEXT NOT NULL,
                    decision_json TEXT,
                    status TEXT NOT NULL,
                    confidence INTEGER,
                    reason_code TEXT NOT NULL,
                    needs_review INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(vacancy_id, payload_hash)
                );
                CREATE TABLE IF NOT EXISTS registry_classifications (
                    analysis_id INTEGER NOT NULL REFERENCES registry_analyses(analysis_id),
                    direction_id TEXT NOT NULL,
                    specialization_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(analysis_id,direction_id,specialization_id,role_id)
                );
                CREATE TABLE IF NOT EXISTS registry_profile_matches (
                    analysis_id INTEGER NOT NULL REFERENCES registry_analyses(analysis_id),
                    profile_id TEXT NOT NULL,
                    profile_version INTEGER NOT NULL,
                    matcher_version TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(analysis_id,profile_id,profile_version,matcher_version)
                );
                CREATE TABLE IF NOT EXISTS registry_projections (
                    vacancy_id TEXT NOT NULL REFERENCES registry_vacancies(vacancy_id),
                    projection TEXT NOT NULL,
                    state TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(vacancy_id,projection)
                );
                CREATE TABLE IF NOT EXISTS registry_dedupe_keys (
                    kind TEXT NOT NULL CHECK(kind IN ('link','id','hash')),
                    key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    PRIMARY KEY(kind,key)
                );
            ''')
            self.candidates._add_column_if_missing(
                c, 'registry_analyses', 'raw_text', 'TEXT'
            )
            c.execute(
                'INSERT OR IGNORE INTO registry_schema_versions VALUES (?,?)',
                (REGISTRY_VERSION, _now()),
            )

    def ingest(
        self,
        *,
        vacancy_id: str,
        post_link: str,
        decision: UniversalDecision | None,
        source_type: str = 'telegram',
        external_id: str | None = None,
        published_at: datetime | str | None = None,
        discovered_at: datetime | str | None = None,
        eligible_at: datetime | str | None = None,
        raw_text: str = '',
        channel_name: str = '',
        unavailable_reason: str | None = None,
        eligible: bool = True,
        proven_canonical_id: str | None = None,
    ) -> RegistryVacancy:
        """Persist admitted source evidence. Preview must never call this method."""
        external_id = external_id or vacancy_id
        now = _now()
        published = _timestamp(published_at)
        discovered = _timestamp(discovered_at) or now
        confirmed = bool(
            decision
            and decision.is_vacancy
            and not decision.needs_review
            and decision.confidence >= 90
            and any(item['confidence'] >= 90 for item in decision.classifications)
        )
        ready = (_timestamp(eligible_at) or now) if eligible and confirmed else None
        payload = decision_payload(decision) if decision else None
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        reason = (
            decision.reason_code
            if decision
            else (unavailable_reason or 'legacy_no_analysis')
        )
        digest = hashlib.sha256(
            (
                serialized + reason + hashlib.sha256(raw_text.encode()).hexdigest()
            ).encode()
        ).hexdigest()
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            # Exact identity is safe across Premium/live imports. Text hashes are
            # audit evidence only; the established pipeline retains TTL dedupe.
            known = c.execute(
                '''SELECT vacancy_id FROM registry_sources
                   WHERE (source_type=? AND external_id=?) OR (post_link=? AND post_link!='')
                   ORDER BY discovered_at LIMIT 1''',
                (source_type, external_id, post_link),
            ).fetchone()
            previous_id = known['vacancy_id'] if known else None
            if proven_canonical_id is not None:
                vacancy_id = proven_canonical_id
            elif known:
                vacancy_id = known['vacancy_id']
            alias = c.execute(
                'SELECT canonical_vacancy_id FROM registry_aliases WHERE alias_vacancy_id=?',
                (vacancy_id,),
            ).fetchone()
            if alias:
                vacancy_id = alias['canonical_vacancy_id']
            c.execute(
                '''INSERT INTO registry_vacancies
                   (vacancy_id,post_link,published_at,discovered_at,eligible_at,updated_at)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(vacancy_id) DO UPDATE SET
                   published_at=COALESCE(registry_vacancies.published_at,excluded.published_at),
                   eligible_at=COALESCE(registry_vacancies.eligible_at,excluded.eligible_at),
                   updated_at=excluded.updated_at''',
                (vacancy_id, post_link, published, discovered, ready, now),
            )
            if proven_canonical_id and previous_id and previous_id != vacancy_id:
                # A new proven group identity may supersede an earlier review row.
                # Keep cards, callbacks, analyses and private history intact.
                c.execute(
                    'INSERT OR REPLACE INTO registry_aliases VALUES (?,?,?)',
                    (previous_id, vacancy_id, now),
                )
                c.execute(
                    'UPDATE registry_aliases SET canonical_vacancy_id=? WHERE canonical_vacancy_id=?',
                    (vacancy_id, previous_id),
                )
                c.execute(
                    'UPDATE registry_sources SET vacancy_id=? WHERE vacancy_id=?',
                    (vacancy_id, previous_id),
                )
                c.execute(
                    'UPDATE registry_vacancies SET eligible_at=NULL WHERE vacancy_id=?',
                    (previous_id,),
                )
                c.execute(
                    'UPDATE vacancies SET go_visible=0 WHERE vacancy_id=?',
                    (previous_id,),
                )
                if c.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='candidate_personal_deliveries'"
                ).fetchone():
                    # Reserved has no network side effect. In-flight/uncertain/sent
                    # history stays untouched and blocks canonical redelivery.
                    c.execute(
                        "UPDATE candidate_personal_deliveries SET state='pending',token=NULL WHERE vacancy_id=? AND state='reserved'",
                        (previous_id,),
                    )
                    c.execute(
                        """UPDATE candidate_personal_deliveries AS canonical
                        SET state='pending',token=NULL
                        WHERE vacancy_id=? AND state='reserved' AND EXISTS (
                            SELECT 1 FROM candidate_personal_deliveries prior
                            JOIN registry_aliases a ON a.alias_vacancy_id=prior.vacancy_id
                            WHERE a.canonical_vacancy_id=canonical.vacancy_id
                            AND prior.telegram_user_id=canonical.telegram_user_id
                            AND prior.state!='pending')""",
                        (vacancy_id,),
                    )
            c.execute(
                '''INSERT INTO registry_sources
                   (source_type,external_id,vacancy_id,post_link,published_at,discovered_at,channel_name,text_hash)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_type,external_id) DO UPDATE SET
                   vacancy_id=excluded.vacancy_id,
                   published_at=COALESCE(excluded.published_at,registry_sources.published_at),
                   channel_name=excluded.channel_name,text_hash=excluded.text_hash''',
                (
                    source_type,
                    external_id,
                    vacancy_id,
                    post_link,
                    published,
                    discovered,
                    channel_name,
                    hashlib.sha256(raw_text.encode()).hexdigest(),
                ),
            )
            status = (
                'unavailable'
                if decision is None
                else ('review' if decision.needs_review else 'available')
            )
            c.execute(
                '''INSERT OR IGNORE INTO registry_analyses
                   (vacancy_id,payload_hash,decision_json,status,confidence,reason_code,needs_review,
                   schema_version,prompt_version,created_at,raw_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    vacancy_id,
                    digest,
                    serialized if decision else None,
                    status,
                    decision.confidence if decision else None,
                    reason,
                    int(decision.needs_review) if decision else 1,
                    SCHEMA_VERSION,
                    decision.prompt_version if decision else PROMPT_VERSION,
                    now,
                    raw_text or None,
                ),
            )
            analysis_id = c.execute(
                'SELECT analysis_id FROM registry_analyses WHERE vacancy_id=? AND payload_hash=?',
                (vacancy_id, digest),
            ).fetchone()[0]
            # Re-importing a legacy record must not erase a later full analysis.
            if decision is not None or unavailable_reason != 'legacy_no_analysis':
                c.execute(
                    'UPDATE registry_vacancies SET latest_analysis_id=? WHERE vacancy_id=?',
                    (analysis_id, vacancy_id),
                )
            else:
                c.execute(
                    'UPDATE registry_vacancies SET latest_analysis_id=COALESCE(latest_analysis_id,?) WHERE vacancy_id=?',
                    (analysis_id, vacancy_id),
                )
            if decision:
                for item in decision.classifications:
                    accepted = (
                        decision.is_vacancy
                        and not decision.needs_review
                        and decision.confidence >= 90
                        and item['confidence'] >= 90
                    )
                    c.execute(
                        'INSERT OR IGNORE INTO registry_classifications VALUES (?,?,?,?,?,?)',
                        (
                            analysis_id,
                            item['direction_id'],
                            item['specialization_id'],
                            item['role_id'],
                            item['confidence'],
                            'accepted' if accepted else 'review',
                        ),
                    )
        registered = self.get(vacancy_id)
        if decision and decision.is_vacancy:
            a = decision.analysis
            self.candidates.register_vacancy(
                vacancy_id=vacancy_id,
                title=a.title,
                company=a.company,
                summary=a.summary,
                post_link=registered.post_link,
                apply_link=a.apply_link,
                published_at=registered.published_at,
                go_visible=False,
            )
        return registered

    def reusable_decision(
        self, vacancy_id: str, raw_text: str
    ) -> UniversalDecision | None:
        """Reuse only the current version for unchanged source text."""
        digest = hashlib.sha256(raw_text.encode()).hexdigest()
        with self._connect() as c:
            row = c.execute(
                """SELECT a.decision_json FROM registry_vacancies v
                JOIN registry_analyses a ON a.analysis_id=v.latest_analysis_id
                WHERE v.vacancy_id=? AND a.schema_version=? AND a.prompt_version=?
                AND EXISTS (SELECT 1 FROM registry_sources s WHERE s.vacancy_id=v.vacancy_id AND s.text_hash=?)""",
                (vacancy_id, SCHEMA_VERSION, PROMPT_VERSION, digest),
            ).fetchone()
        return (
            decision_from_payload(json.loads(row['decision_json']))
            if row and row['decision_json']
            else None
        )

    def get(self, vacancy_id: str) -> RegistryVacancy | None:
        with self._connect() as c:
            row = c.execute(
                '''SELECT v.*,a.decision_json,a.reason_code,a.raw_text,a.prompt_version FROM registry_vacancies v
                LEFT JOIN registry_analyses a ON a.analysis_id=v.latest_analysis_id
                WHERE v.vacancy_id=?''',
                (vacancy_id,),
            ).fetchone()
        return self._vacancy(row) if row else None

    @staticmethod
    def _vacancy(row: sqlite3.Row) -> RegistryVacancy:
        decision = (
            decision_from_payload(json.loads(row['decision_json']))
            if row['decision_json']
            else None
        )
        return RegistryVacancy(
            row['vacancy_id'],
            row['post_link'],
            row['published_at'],
            row['discovered_at'],
            row['eligible_at'],
            decision,
            row['latest_analysis_id'],
            row['reason_code'] if decision is None else None,
            row['raw_text'],
            row['prompt_version'],
        )

    def list_vacancies(self, *, eligible_only: bool = True) -> list[RegistryVacancy]:
        with self._connect() as c:
            rows = c.execute(
                '''SELECT v.*,a.decision_json,a.reason_code,a.raw_text,a.prompt_version FROM registry_vacancies v
                LEFT JOIN registry_analyses a ON a.analysis_id=v.latest_analysis_id'''
                + (' WHERE v.eligible_at IS NOT NULL' if eligible_only else '')
                + ' ORDER BY COALESCE(v.published_at,v.discovered_at) DESC,v.vacancy_id'
            ).fetchall()
        return [self._vacancy(row) for row in rows]

    def list_personal_matches(self, telegram_user_id: int) -> list[RegistryMatch]:
        """Owner-scoped confirmed matches, recomputed locally after profile edits."""
        from tg_vacancy_bot.profile_matcher import ProfileMatch, match_profile

        profiles = [
            p for p in self.candidates.list_profiles(telegram_user_id) if p.is_active
        ]
        # A legacy owner can have several active rows. Preserve them during
        # migration and suppress personal results until the owner explicitly picks.
        if len(profiles) != 1:
            return []
        result = []
        for vacancy in self.list_vacancies():
            decision = vacancy.decision
            if decision is None:
                continue
            matched = []
            for profile in profiles:
                current_contract = (
                    profile.preferences.get('profile_contract') == PROFILE_CONTRACT
                )
                if current_contract and (
                    vacancy.prompt_version != PROMPT_VERSION or not vacancy.raw_text
                ):
                    continue
                with self._connect() as c:
                    row = c.execute(
                        '''SELECT status FROM registry_profile_matches WHERE
                        analysis_id=? AND profile_id=? AND profile_version=? AND matcher_version=?''',
                        (
                            vacancy.analysis_id,
                            profile.profile_id,
                            profile.version,
                            MATCHER_VERSION,
                        ),
                    ).fetchone()
                if row:
                    status = row['status']
                else:
                    classification = next(
                        (
                            i
                            for i in decision.classifications
                            if i['direction_id'] == profile.direction_id
                            and (
                                not current_contract or i['role_id'] == profile.role_id
                            )
                        ),
                        None,
                    )
                    match = match_profile(
                        decision, profile, source_text=vacancy.raw_text
                    )
                    if match.status == 'match' and (
                        decision.confidence < 90
                        or classification is None
                        or classification['confidence'] < 90
                    ):
                        match = ProfileMatch('review', 'classification_uncertain')
                    status = match.status
                    with self._connect() as c:
                        c.execute(
                            '''INSERT OR IGNORE INTO registry_profile_matches
                            VALUES (?,?,?,?,?,?,?,?,?)''',
                            (
                                vacancy.analysis_id,
                                profile.profile_id,
                                profile.version,
                                MATCHER_VERSION,
                                telegram_user_id,
                                status,
                                match.reason_code,
                                json.dumps(asdict(match)),
                                _now(),
                            ),
                        )
                if status == 'match':
                    matched.append(profile)
            if matched:
                with self._connect() as c:
                    card = c.execute(
                        'SELECT callback_key FROM vacancies WHERE vacancy_id=?',
                        (vacancy.vacancy_id,),
                    ).fetchone()
                result.append(
                    RegistryMatch(
                        vacancy.vacancy_id,
                        decision.analysis,
                        decision,
                        vacancy.published_at,
                        vacancy.post_link,
                        vacancy.eligible_at,
                        tuple(sorted(matched, key=lambda p: p.profile_id)),
                        (
                            card['callback_key']
                            if card
                            else callback_key_for(vacancy.vacancy_id)
                        ),
                    )
                )
        return result

    def set_projection(
        self, vacancy_id: str, projection: str, state: str, **metadata
    ) -> None:
        with self._connect() as c:
            c.execute(
                '''INSERT INTO registry_projections
                (vacancy_id,projection,state,metadata_json,updated_at) VALUES (?,?,?,?,?)
                ON CONFLICT(vacancy_id,projection) DO UPDATE SET state=excluded.state,
                metadata_json=excluded.metadata_json,updated_at=excluded.updated_at''',
                (vacancy_id, projection, state, json.dumps(metadata), _now()),
            )
