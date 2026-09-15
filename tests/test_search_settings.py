"""Search configuration remains safe to import without live API credentials."""

import os

import pytest

from tg_vacancy_bot.search.settings import (
    SearchSettings,
    search_database_path,
    source_capabilities,
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for key in os.environ:
        if key.startswith('THREADS_') or key == 'PREMIUM_GLOBAL_SEARCH_ENABLED':
            monkeypatch.delenv(key)


def threads_capability(settings=None):
    return next(row for row in source_capabilities(settings) if row['key'] == 'threads')


def test_threads_disabled_by_default_without_token():
    settings = SearchSettings.from_env()
    assert not settings.threads_enabled
    assert not threads_capability(settings)['enabled']
    assert threads_capability(settings)['reason'] == 'disabled'
    assert next(row for row in source_capabilities() if row['key'] == 'telegram')[
        'enabled'
    ]


def test_enabled_without_token_is_unavailable(monkeypatch):
    monkeypatch.setenv('THREADS_ENABLED', 'true')
    settings = SearchSettings.from_env()
    assert settings.threads_enabled
    assert not threads_capability(settings)['enabled']
    assert threads_capability(settings)['reason'] == 'token_not_configured'


def test_token_is_not_in_repr_or_capability_payload(monkeypatch):
    monkeypatch.setenv('THREADS_ENABLED', 'true')
    monkeypatch.setenv('THREADS_ACCESS_TOKEN', '  synthetic-secret-token  ')
    settings = SearchSettings.from_env()
    assert settings.token == 'synthetic-secret-token'
    assert 'synthetic-secret-token' not in repr(settings)
    assert 'synthetic-secret-token' not in repr(source_capabilities(settings))
    assert threads_capability(settings)['enabled']


@pytest.mark.parametrize(
    'key,value',
    [
        ('THREADS_SEARCH_LIMIT', 'not-a-number'),
        ('THREADS_QUERY_VARIANTS_LIMIT', '0'),
        ('THREADS_MAX_PAGES_PER_QUERY', '100'),
        ('THREADS_TIMEOUT_SECONDS', '-1'),
        ('THREADS_MAX_ATTEMPTS', ''),
        ('THREADS_COLLECTION_INTERVAL_HOURS', '-1'),
        ('THREADS_MAX_REQUESTS_PER_DAY', '2201'),
    ],
)
def test_bad_numeric_config_disables_threads_without_crashing(monkeypatch, key, value):
    monkeypatch.setenv('THREADS_ENABLED', 'true')
    monkeypatch.setenv('THREADS_ACCESS_TOKEN', 'synthetic-token')
    monkeypatch.setenv(key, value)
    settings = SearchSettings.from_env()
    assert settings.configuration_error == 'invalid_threads_configuration'
    assert not threads_capability(settings)['enabled']
    assert threads_capability(settings)['reason'] == 'invalid_threads_configuration'


def test_valid_limits_and_six_hour_schedule(monkeypatch):
    settings = SearchSettings.from_env()
    assert settings.interval_hours == 6
    monkeypatch.setenv('THREADS_MAX_REQUESTS_PER_DAY', '100')
    monkeypatch.setenv('THREADS_COLLECTION_INTERVAL_HOURS', '0')
    settings = SearchSettings.from_env()
    assert settings.daily_budget == 100
    assert settings.interval_hours == 0
    assert settings.configuration_error is None


def test_database_path_respects_explicit_data_dir_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'env'))
    assert search_database_path() == str(tmp_path / 'env' / 'admin' / 'admin.sqlite3')
    assert search_database_path(str(tmp_path / 'explicit')) == str(
        tmp_path / 'explicit' / 'admin' / 'admin.sqlite3'
    )
