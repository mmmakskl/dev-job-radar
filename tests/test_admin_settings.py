import json

import pytest

from tg_vacancy_bot.admin.settings import (
    SCHEMA_VERSION,
    AdminSettings,
    SettingsStore,
    normalize_public_source,
)


def test_settings_store_is_versioned_and_atomic(tmp_path) -> None:
    store = SettingsStore(str(tmp_path))
    saved = store.save(AdminSettings())

    assert saved.revision == 1
    assert store.load().schema_version == SCHEMA_VERSION
    assert (
        json.loads((tmp_path / 'admin' / 'settings.json').read_text())['revision'] == 1
    )


def test_rejects_numeric_browser_channel() -> None:
    with pytest.raises(ValueError, match='публичным username'):
        AdminSettings.model_validate({'telegram': {'additional_channels': ['-100123']}})


def test_rejects_invalid_runtime_limits() -> None:
    with pytest.raises(ValueError):
        AdminSettings.model_validate({'filters': {'workers': 9}})


def test_source_url_is_normalized_and_prompt_rejects_credentials() -> None:
    assert normalize_public_source('https://t.me/Go_Jobs/') == 'Go_Jobs'
    settings = AdminSettings.model_validate(
        {'telegram': {'managed_sources': [{'identifier': '@go_jobs'}]}}
    )
    assert settings.telegram.managed_sources[0].identifier == 'go_jobs'
    with pytest.raises(ValueError, match='секреты'):
        AdminSettings.model_validate(
            {
                'mistral': {
                    'vacancy_instructions': 'Используй api_key=super-secret-token-value'
                }
            }
        )
