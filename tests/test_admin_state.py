import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tg_vacancy_bot.admin.control import (
    ActionConflict,
    claim_action,
    finish_action,
    recover_running_actions,
    request_action,
)
from tg_vacancy_bot.admin.settings import SettingsStore, StaleSettingsError
from tg_vacancy_bot.channel_sync import FolderChannel


def test_legacy_import_is_idempotent_and_survives_env_removal(
    tmp_path, monkeypatch
) -> None:
    legacy_dir = tmp_path / 'admin'
    legacy_dir.mkdir()
    legacy = {
        'revision': 7,
        'telegram': {
            'folder_channels': ['-10020'],
            'managed_sources': [{'identifier': 'managed_jobs'}],
        },
    }
    (legacy_dir / 'settings.json').write_text(json.dumps(legacy), encoding='utf-8')
    monkeypatch.setenv('TARGET_CHANNELS', '@Legacy_Jobs,-10010')

    store = SettingsStore(str(tmp_path))
    assert store.load().revision == 7
    assert store.active_targets() == ['Legacy_Jobs', -10010, -10020, 'managed_jobs']

    monkeypatch.delenv('TARGET_CHANNELS')
    restarted = SettingsStore(str(tmp_path))
    assert restarted.active_targets() == store.active_targets()
    assert (legacy_dir / 'settings.json').read_text(encoding='utf-8') == json.dumps(
        legacy
    )


def test_concurrent_first_migration_imports_legacy_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '@legacy_jobs')

    with ThreadPoolExecutor(max_workers=4) as pool:
        targets = list(
            pool.map(lambda _: SettingsStore(str(tmp_path)).active_targets(), range(4))
        )

    assert targets == [['legacy_jobs']] * 4


def test_username_merges_into_stable_id_and_updates_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    store = SettingsStore(str(tmp_path))
    original = store.upsert_source('Go_Jobs', origin='admin')
    merged = store.upsert_source(
        -100123,
        username='go_jobs',
        title='Go Jobs',
        chat_type='channel',
        origin='discovery',
        verification_status='verified',
    )

    assert merged['id'] == original['id']
    assert merged['origins'] == ['admin', 'discovery']
    assert merged['title'] == 'Go Jobs'
    assert store.active_targets() == [-100123]


def test_unverified_admin_source_cannot_be_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '@legacy_jobs')
    store = SettingsStore(str(tmp_path))
    source = store.upsert_source(
        'pending_jobs',
        origin='admin',
        enabled=False,
        verification_status='unverified',
    )

    with pytest.raises(ValueError, match='успешно проверьте'):
        store.set_source_enabled(source['id'], True)

    settings = store.load().model_dump()
    with pytest.raises(ValueError, match='Непроверенный'):
        store.replace_with_enabled_sources(settings, {source['id']})


def test_folder_reconciliation_removes_only_folder_origin(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    store = SettingsStore(str(tmp_path))
    shared = store.upsert_source(-1001, origin='admin')
    store.sync_folder(
        [
            FolderChannel(-1001, 'Shared', 'shared', 'channel'),
            FolderChannel(-1002, 'Folder only', None, 'group'),
        ]
    )

    counts = store.sync_folder([])

    assert counts['received'] == 0
    assert store.get_source(shared['id'])['origins'] == ['admin']
    assert store.active_targets() == [-1001]


def test_folder_reconciliation_rolls_back_after_database_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    store = SettingsStore(str(tmp_path))
    store.sync_folder([FolderChannel(-1001, 'Known good', 'known_good', 'channel')])
    original_upsert = store._upsert_source_tx
    calls = 0

    def fail_second_upsert(connection, identifier, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError('simulated write failure')
        return original_upsert(connection, identifier, **kwargs)

    monkeypatch.setattr(store, '_upsert_source_tx', fail_second_upsert)
    with pytest.raises(sqlite3.OperationalError):
        store.sync_folder(
            [
                FolderChannel(-1002, 'First new', 'first_new', 'channel'),
                FolderChannel(-1003, 'Second new', 'second_new', 'channel'),
            ]
        )

    assert store.active_targets() == [-1001]


def test_actions_are_claimed_once_conflict_and_recover(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    first = request_action('sync_channels', str(tmp_path))
    assert request_action('sync_channels', str(tmp_path))['id'] == first['id']
    with pytest.raises(ActionConflict):
        request_action('history', str(tmp_path))

    claimed = claim_action(str(tmp_path))
    assert claimed['status'] == 'running'
    assert claim_action(str(tmp_path)) is None
    assert recover_running_actions(str(tmp_path)) == 1
    reclaimed = claim_action(str(tmp_path))
    terminal = finish_action(
        reclaimed['id'],
        succeeded=True,
        data_dir=str(tmp_path),
        counts={'received': 2, 'created': 1, 'updated': 1},
    )
    assert terminal['status'] == 'succeeded'
    assert terminal['received'] == 2


def test_concurrent_duplicate_actions_share_one_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    SettingsStore(str(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as pool:
        actions = list(
            pool.map(lambda _: request_action('sync_channels', str(tmp_path)), range(8))
        )

    assert len({action['id'] for action in actions}) == 1


def test_action_error_is_sanitized(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    requested = request_action('sync_channels', str(tmp_path))
    claim_action(str(tmp_path))

    terminal = finish_action(
        requested['id'],
        succeeded=False,
        data_dir=str(tmp_path),
        error='token=secret-value source=-1001234567890',
    )

    assert 'secret-value' not in terminal['error']
    assert '-1001234567890' not in terminal['error']


def test_stale_settings_revision_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('TARGET_CHANNELS', '')
    store = SettingsStore(str(tmp_path))
    first = store.load()
    store.save(first)
    with pytest.raises(StaleSettingsError):
        store.save(first)
