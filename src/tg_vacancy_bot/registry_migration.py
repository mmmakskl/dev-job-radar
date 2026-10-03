"""Repeatable offline imports and consistent SQLite backups for the registry."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from tg_vacancy_bot.registry import VacancyRegistry, decision_from_payload
from tg_vacancy_bot.telegram.links import build_vacancy_id


def backup_database(source: str, destination: str) -> None:
    """Use SQLite's online backup API for a consistent snapshot including WAL."""
    if Path(destination).exists():
        raise ValueError('backup_destination_exists')
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)


def _rows(path: str | None, table: str, where: str = '') -> list[dict]:
    if not path or not Path(path).exists():
        return []
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        if not c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            return []
        return [dict(row) for row in c.execute(f'SELECT * FROM {table} {where}')]


def _decision(raw: str | None):
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        payload = payload.get('universal_decision') or payload
        if 'classifications' in payload:
            return decision_from_payload(payload)
    except (ValueError, KeyError, TypeError):
        pass
    return None


def import_legacy(
    candidate_path: str,
    *,
    premium_path: str | None = None,
    search_path: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Import only admitted data; dry-run migrates an isolated SQLite snapshot."""
    with tempfile.TemporaryDirectory(prefix='candidate-registry-import-') as temp:
        target = candidate_path
        if dry_run:
            target = str(Path(temp) / 'candidate.sqlite3')
            if Path(candidate_path).exists():
                backup_database(candidate_path, target)
        cards = _rows(candidate_path, 'vacancies')
        premium = _rows(
            premium_path,
            'premium_search_results',
            "WHERE status IN ('saved','published')",
        )
        runs = _rows(premium_path, 'premium_search_runs')
        owners = {run['search_run_id']: run.get('owner', 'admin') for run in runs}
        if owners:
            premium = [
                item
                for item in premium
                if not owners.get(
                    item['search_run_id'], 'candidate:unknown'
                ).startswith('candidate:')
            ]
        search = _rows(
            search_path,
            'search_items',
            "WHERE publication_status IN ('saved','published')",
        )
        search = [item for item in search if item.get('scope', 'shared') == 'shared']
        registry = VacancyRegistry(target)
        before = len(registry.list_vacancies(eligible_only=False))
        admitted_cards = 0
        for card in cards:
            # Personal archive cards are not a shared source admission.
            if (
                not card.get('go_visible', True)
                and registry.get(card['vacancy_id']) is None
            ):
                continue
            admitted_cards += 1
            registry.ingest(
                vacancy_id=card['vacancy_id'],
                post_link=card['post_link'],
                decision=None,
                source_type='legacy_candidate',
                external_id=card['vacancy_id'],
                published_at=card['published_at'],
                discovered_at=card['created_at'],
                eligible_at=card['created_at'],
                unavailable_reason='legacy_no_analysis',
            )
            registry.set_projection(
                card['vacancy_id'],
                'go_channel',
                card['delivery_state'],
                channel_message_id=card['channel_message_id'],
            )
        for item in premium:
            link = item['post_link']
            if not link:
                continue
            registry.ingest(
                vacancy_id=item['vacancy_id'] or build_vacancy_id(link),
                post_link=link,
                source_type='premium',
                external_id=item['vacancy_id'] or build_vacancy_id(link),
                decision=_decision(item['analysis_json']),
                published_at=item['published_at'],
                discovered_at=item['found_at'],
                eligible_at=item['updated_at'],
                channel_name=item['channel_name'] or '',
                raw_text=item['raw_text'] or '',
                unavailable_reason='legacy_no_analysis',
            )
        for item in search:
            link = item['permalink']
            if not link:
                continue
            registry.ingest(
                vacancy_id=item['vacancy_id'] or build_vacancy_id(link),
                post_link=link,
                source_type=item['source'],
                external_id=item['external_id'],
                decision=_decision(
                    item.get('universal_decision_json') or item['analysis_json']
                ),
                published_at=item['timestamp'],
                discovered_at=item['created_at'],
                eligible_at=item['updated_at'],
                raw_text=item['text'] or '',
                unavailable_reason='legacy_no_analysis',
            )
        with registry._connect() as c:
            counts = {
                table: c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in (
                    'registry_vacancies',
                    'registry_sources',
                    'registry_analyses',
                    'candidate_profiles',
                    'candidate_profile_versions',
                    'user_saved_vacancies',
                    'vacancies',
                )
            }
            integrity = c.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys = len(c.execute('PRAGMA foreign_key_check').fetchall())
        return {
            'dry_run': dry_run,
            'candidate_cards': len(cards),
            'candidate_admitted': admitted_cards,
            'premium_admitted': len(premium),
            'search_admitted': len(search),
            'new_registry_vacancies': counts['registry_vacancies'] - before,
            'counts': counts,
            'integrity_check': integrity,
            'foreign_key_violations': foreign_keys,
        }
