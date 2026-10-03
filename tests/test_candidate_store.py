import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tg_vacancy_bot.candidate_catalog import resolve_alias, validate_profile_path
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


def vacancy(store: CandidateStore, vacancy_id: str = 'jobs_42'):
    return store.register_vacancy(
        vacancy_id=vacancy_id,
        title='Senior Go Developer',
        company='Acme',
        summary='Go и PostgreSQL',
        post_link='https://t.me/jobs/42',
        apply_link='https://example.com/apply',
        published_at='2026-08-13T12:00:00+00:00',
    )


def test_migration_preserves_vacancies_delivery_and_legacy_actions(tmp_path):
    path = tmp_path / 'candidate.sqlite3'
    store = CandidateStore(str(path))
    vacancy(store)
    with sqlite3.connect(path) as db:
        db.execute(
            '''CREATE TABLE user_vacancy_actions (
            telegram_user_id INTEGER, vacancy_id TEXT, status TEXT, personal_note TEXT,
            created_at TEXT, updated_at TEXT, PRIMARY KEY(telegram_user_id,vacancy_id))'''
        )
        db.execute('CREATE TABLE vacancy_reports (id INTEGER)')
        db.execute('CREATE TABLE candidate_browser_sessions (token TEXT)')
        db.execute(
            'INSERT INTO user_vacancy_actions VALUES (1, ?, ?, ?, ?, ?)',
            ('jobs_42', 'saved', 'private note', 'created', 'saved-time'),
        )
        db.execute(
            'INSERT INTO user_vacancy_actions VALUES (2, ?, ?, ?, ?, ?)',
            ('jobs_42', 'applied', 'remove', 'created', 'applied-time'),
        )
        db.execute(
            "UPDATE vacancies SET delivery_state='published', channel_message_id=88"
        )
    migrated = CandidateStore(str(path))
    CandidateStore(str(path))
    assert [v.vacancy_id for v in migrated.list_for_user(1, 'saved')] == ['jobs_42']
    assert migrated.list_for_user(2, 'saved') == []
    assert migrated.list_for_user(1, 'new') == []
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT saved_at FROM user_saved_vacancies').fetchone() == (
            'saved-time',
        )
        assert db.execute(
            'SELECT delivery_state, channel_message_id FROM vacancies'
        ).fetchone() == ('published', 88)
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            'user_vacancy_actions',
            'vacancy_reports',
            'candidate_browser_sessions',
        } <= tables
        assert db.execute('SELECT COUNT(*) FROM user_vacancy_actions').fetchone() == (
            2,
        )


def test_profiles_versions_are_user_scoped_and_deletion_removes_history(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    first = store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'stacks': ['go'], 'vacancy_languages': ['ru', 'en']},
    )
    second = store.create_profile(
        1,
        name='QA',
        direction_id='qa',
        specialization_id='manual_qa',
        role_id='manual_qa_engineer',
    )
    assert {p.profile_id for p in store.list_profiles(1)} == {
        first.profile_id,
        second.profile_id,
    }
    assert store.list_profiles(2) == []
    changed = store.update_profile(
        1, first.profile_id, name='Go backend', is_active=False
    )
    assert changed.version == 2 and changed.is_active is False
    versions = store.get_profile_versions(1, first.profile_id)
    assert len(versions) == 2
    assert versions[0]['snapshot']['name'] == 'Go'
    assert versions[1]['snapshot']['name'] == 'Go backend'
    assert store.get_profile_versions(2, first.profile_id) == []
    assert store.delete_profile(1, first.profile_id)
    assert store.get_profile_versions(1, first.profile_id) == []


def test_profile_optional_fields_path_stacks_and_timezone_validation(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    profile = store.create_profile(
        4,
        name='Designer',
        direction_id='design',
        specialization_id='product_design',
        role_id='product_designer',
    )
    assert profile.preferences['stacks'] == []
    assert profile.preferences['vacancy_languages'] == []
    assert profile.preferences['timezone'] == 'Europe/Moscow'
    with pytest.raises(ValueError):
        validate_profile_path('qa', 'manual_qa', 'manual_qa_engineer', ['go'])
    with pytest.raises(ValueError):
        store.create_profile(
            4,
            name='Broken',
            direction_id='qa',
            specialization_id='automation_qa',
            role_id='manual_qa_engineer',
        )
    with pytest.raises(ValueError):
        store.create_profile(
            4,
            name='Broken',
            direction_id='development',
            specialization_id='backend',
            role_id='backend_developer',
            preferences={'timezone': 'Mars/Olympus'},
        )


def test_catalog_resolves_russian_and_english_aliases():
    assert resolve_alias('бэкенд') == ('specialization', 'backend')
    assert resolve_alias('Backend') == ('specialization', 'backend')
    assert resolve_alias('инженер данных') == ('role', 'data_engineer')
    assert resolve_alias('digital marketer') == ('role', 'digital_marketer')


def test_additive_migration_defaults_drafts_and_template_invalidation(tmp_path):
    import json
    from tg_vacancy_bot.premium_search.templates import list_templates

    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    profile = store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'stacks': ['go']},
    )
    card = vacancy(store)
    store.save_vacancy(1, card.callback_key)
    store.mark_channel_published(card.vacancy_id, 99)
    with sqlite3.connect(store.path) as db:
        prefs = json.loads(
            db.execute('SELECT preferences_json FROM candidate_profiles').fetchone()[0]
        )
        prefs.pop('delivery_mode')
        db.execute(
            'UPDATE candidate_profiles SET preferences_json=?', (json.dumps(prefs),)
        )
        snapshot = json.loads(
            db.execute(
                'SELECT snapshot_json FROM candidate_profile_versions'
            ).fetchone()[0]
        )
        snapshot['preferences'].pop('delivery_mode')
        raw = json.dumps(snapshot)
        db.execute('UPDATE candidate_profile_versions SET snapshot_json=?', (raw,))
        db.execute('DROP TABLE candidate_profile_drafts')
        db.execute('PRAGMA user_version=2')
    for _ in range(2):
        store = CandidateStore(store.path)
    assert (
        store.get_profile(1, profile.profile_id).preferences['delivery_mode']
        == 'manual'
    )
    assert store.get_profile(2, profile.profile_id) is None
    assert store.get_profile_versions(1, profile.profile_id)[0]['snapshot'] == snapshot
    assert store.list_for_user(1, 'saved')[0] == card
    assert store.channel_delivery_state(card.vacancy_id) == 'published'
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute(
                'SELECT snapshot_json FROM candidate_profile_versions'
            ).fetchone()[0]
            == raw
        )
        assert (
            db.execute('SELECT channel_message_id FROM vacancies').fetchone()[0] == 99
        )
    stack_template = next(t for t in list_templates(profile) if t.kind == 'stack')
    chosen = store.update_profile(
        1,
        profile.profile_id,
        preferences={
            **profile.preferences,
            'delivery_mode': 'immediate',
            'premium_template_id': stack_template.id,
        },
    )
    changed = store.update_profile(
        1, profile.profile_id, preferences={**chosen.preferences, 'stacks': []}
    )
    assert changed.preferences['delivery_mode'] == 'immediate'
    assert 'premium_template_id' not in changed.preferences
    with pytest.raises(ValueError):
        store.update_profile(
            1, profile.profile_id, preferences={'delivery_mode': 'invalid'}
        )
    assert store.get_profile(1, profile.profile_id) == changed


def test_draft_atomic_confirmation_and_conflict(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    values = dict(
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={},
        is_active=True,
    )
    draft = {'token': 'one', 'revision': 0, 'step': 'preview', 'values': values}
    assert store.put_profile_draft(1, draft)
    assert not store.put_profile_draft(1, {**draft, 'token': 'replacement'})
    assert store.finish_profile_draft(2, 'one', 0, save=True) is None
    assert store.finish_profile_draft(1, 'wrong', 0, save=True) is None
    profile = store.finish_profile_draft(1, 'one', 0, save=True)
    assert profile.version == 1
    assert store.finish_profile_draft(1, 'one', 0, save=True) is None
    assert len(store.list_profiles(1)) == 1
    edit = {
        **draft,
        'token': 'edit',
        'profile_id': profile.profile_id,
        'profile_version': 1,
    }
    assert store.put_profile_draft(1, edit)
    store.update_profile(1, profile.profile_id, name='Changed elsewhere')
    assert store.finish_profile_draft(1, 'edit', 0, save=True) is None
    assert store.get_profile_draft(1) == edit
    assert store.finish_profile_draft(1, 'edit', 0, save=False)
    assert store.get_profile(1, profile.profile_id).name == 'Changed elsewhere'


def test_failed_confirmation_keeps_draft_and_no_partial_profile(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    draft = {
        'token': 'invalid',
        'revision': 0,
        'step': 'preview',
        'values': {
            'name': 'Broken',
            'direction_id': 'qa',
            'specialization_id': 'manual_qa',
            'role_id': 'manual_qa_engineer',
            'preferences': {'stacks': ['go']},
        },
    }
    assert store.put_profile_draft(1, draft)
    with pytest.raises(ValueError):
        store.finish_profile_draft(1, 'invalid', 0, save=True)
    assert store.get_profile_draft(1) == draft
    assert store.list_profiles(1) == []
    assert not store.put_profile_draft(1, {**draft, 'revision': 2}, 1)
    assert not store.put_profile_draft(1, {**draft, 'token': 'forged'}, 0)


def test_published_date_windows_boundaries_unknown_dates_and_saved_archive(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    dates = {
        'exact_now': now,
        'inside_24h': now - timedelta(hours=23, seconds=59),
        'exact_24h': now - timedelta(hours=24),
        'exact_7d': now - timedelta(days=7),
        'beyond_7d': now - timedelta(days=7, seconds=1),
        'saved_old': now - timedelta(days=40),
        'future': now + timedelta(seconds=1),
    }
    items = {}
    for vacancy_id, published in dates.items():
        items[vacancy_id] = store.register_vacancy(
            vacancy_id=vacancy_id,
            title=vacancy_id,
            company=None,
            summary=None,
            post_link=f'https://t.me/jobs/{vacancy_id}',
            apply_link=None,
            published_at=published.isoformat(),
        )
    items['unknown'] = store.register_vacancy(
        vacancy_id='unknown',
        title='unknown',
        company=None,
        summary=None,
        post_link='https://t.me/jobs/unknown',
        apply_link=None,
        published_at=None,
    )
    store.save_vacancy(1, items['saved_old'].callback_key)
    assert {v.vacancy_id for v in store.list_for_user(1, 'new', now=now)} == {
        'exact_now',
        'inside_24h',
        'exact_24h',
    }
    assert {
        v.vacancy_id for v in store.list_for_user(1, 'older', now=now, older_days=7)
    } == {'exact_7d'}
    assert [v.vacancy_id for v in store.list_for_user(1, 'undated', now=now)] == [
        'unknown'
    ]
    assert [v.vacancy_id for v in store.list_for_user(1, 'saved', now=now)] == [
        'saved_old'
    ]
    max_days = store.max_older_days()
    assert max_days >= 30
    assert store.set_older_days(1, min(14, max_days))
    assert not store.set_older_days(1, store.max_older_days() + 1)
