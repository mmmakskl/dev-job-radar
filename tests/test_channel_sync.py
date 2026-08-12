from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_vacancy_bot.channel_sync import (
    FolderChannel,
    build_synced_target_channels,
    find_folder_filter,
    find_folder_id,
    replace_env_value,
    update_target_channels_env,
)


def test_build_synced_target_channels_preserves_existing_sources() -> None:
    targets, added = build_synced_target_channels(
        ['@existing_source', -100222],
        [
            FolderChannel(-100111, 'Existing', 'existing_source'),
            FolderChannel(-100222, 'Already numeric', 'already_numeric'),
            FolderChannel(-100333, 'New source', 'new_source'),
            FolderChannel(-100333, 'Duplicate dialog', 'new_source'),
        ],
    )

    assert targets == ('@existing_source', -100222, -100333)
    assert added == (FolderChannel(-100333, 'New source', 'new_source'),)


def test_find_folder_id_matches_visible_name_case_insensitively() -> None:
    folder = SimpleNamespace(id=7, title=SimpleNamespace(text='Вакансии'))

    assert find_folder_id([folder], 'вАкАнСиИ') == 7
    assert find_folder_filter([folder], 'Вакансии') is folder


def test_find_folder_id_rejects_missing_folder() -> None:
    with pytest.raises(RuntimeError, match='не найдена'):
        find_folder_id([], 'Вакансии')


def test_build_synced_target_channels_uses_resolved_username_ids() -> None:
    targets, added = build_synced_target_channels(
        ['renamed_source'],
        [FolderChannel(-100111, 'Renamed source', 'another_username')],
        resolved_configured_ids=[-100111],
    )

    assert targets == ('renamed_source',)
    assert added == ()


def test_replace_env_value_preserves_other_variables_and_line_ending() -> None:
    content = 'API_ID=1\r\nTARGET_CHANNELS=old\r\nAPI_HASH=secret\r\n'

    updated = replace_env_value(content, 'TARGET_CHANNELS', '-100123,@jobs')

    assert updated == 'API_ID=1\r\nTARGET_CHANNELS=-100123,@jobs\r\nAPI_HASH=secret\r\n'


def test_replace_env_value_appends_missing_assignment() -> None:
    assert replace_env_value('API_ID=1', 'TARGET_CHANNELS', '-100123') == (
        'API_ID=1\nTARGET_CHANNELS=-100123\n'
    )


def test_update_target_channels_env_is_idempotent(tmp_path: Path) -> None:
    env_path = tmp_path / '.env'
    env_path.write_text('TARGET_CHANNELS=@jobs\nAPI_ID=1\n', encoding='utf-8')

    assert update_target_channels_env(env_path, ['@jobs', -100123]) is True
    assert env_path.read_text(encoding='utf-8') == (
        'TARGET_CHANNELS=@jobs,-100123\nAPI_ID=1\n'
    )
    assert update_target_channels_env(env_path, ['@jobs', -100123]) is False
