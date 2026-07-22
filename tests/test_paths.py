from tg_vacancy_bot.paths import resolve_session_path, resolve_state_path


def test_local_paths_remain_backward_compatible() -> None:
    assert resolve_session_path("my_account") == "my_account"
    assert resolve_state_path() == "data/state.jsonl"


def test_container_paths_use_persistent_data_directory() -> None:
    assert (
        resolve_session_path("my_account", data_dir="/app/data")
        == "/app/data/my_account"
    )
    assert resolve_state_path(data_dir="/app/data") == "/app/data/state.jsonl"


def test_explicit_paths_override_data_directory() -> None:
    assert (
        resolve_session_path(
            "my_account",
            data_dir="/app/data",
            session_path="/custom/account",
        )
        == "/custom/account"
    )
    assert (
        resolve_state_path(
            data_dir="/app/data",
            state_path="/custom/state.jsonl",
        )
        == "/custom/state.jsonl"
    )
