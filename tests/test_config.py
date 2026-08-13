import pytest

from tg_vacancy_bot import config


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_parse_bool_env_accepts_enabled_values(value: str) -> None:
    assert config.parse_bool_env(value) is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "anything"])
def test_parse_bool_env_defaults_to_false(value: str | None) -> None:
    assert config.parse_bool_env(value) is False


def test_parse_telegram_target_preserves_username_and_converts_numeric_id() -> None:
    assert config.parse_telegram_target("@vacancies") == "@vacancies"
    assert config.parse_telegram_target("-1001234567890") == -1001234567890


def test_parse_telegram_user_ids_accepts_only_numeric_allowlist() -> None:
    assert config.parse_telegram_user_ids('42, 100,42') == {42, 100}
    with pytest.raises(ValueError, match='CANDIDATE_BOT_ALLOWED_USER_IDS'):
        config.parse_telegram_user_ids('42,@candidate')


def test_validation_requires_notification_target_only_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_ID", 1)
    monkeypatch.setattr(config, "API_HASH", "hash")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")
    monkeypatch.setattr(config, "GOOGLE_SHEET_URL", "https://example.com/sheet")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_TARGET", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_ENABLED", False)

    config.validate_required_settings()

    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_ENABLED", True)
    with pytest.raises(RuntimeError, match="TELEGRAM_NOTIFY_TARGET"):
        config.validate_required_settings()
