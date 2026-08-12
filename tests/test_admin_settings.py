import json

import pytest

from tg_vacancy_bot.admin.settings import AdminSettings, SettingsStore


def test_settings_store_is_versioned_and_atomic(tmp_path) -> None:
    store = SettingsStore(str(tmp_path))
    saved = store.save(AdminSettings())

    assert saved.revision == 1
    assert store.load().schema_version == 1
    assert (
        json.loads((tmp_path / 'admin' / 'settings.json').read_text())['revision'] == 1
    )


def test_rejects_numeric_browser_channel() -> None:
    with pytest.raises(ValueError, match='публичным username'):
        AdminSettings.model_validate({'telegram': {'additional_channels': ['-100123']}})


def test_rejects_invalid_runtime_limits() -> None:
    with pytest.raises(ValueError):
        AdminSettings.model_validate({'filters': {'workers': 9}})
