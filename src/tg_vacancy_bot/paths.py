"""Разрешение локальных и production-путей без побочных эффектов."""

from pathlib import Path


def resolve_session_path(
    session_name: str,
    *,
    data_dir: str | None = None,
    session_path: str | None = None,
) -> str:
    """Возвращает Telethon session name/path без суффикса .session."""
    if session_path:
        return session_path
    if data_dir:
        return str(Path(data_dir) / session_name)
    return session_name


def resolve_state_path(
    *,
    data_dir: str | None = None,
    state_path: str | None = None,
) -> str:
    """Сохраняет старый локальный default и поддерживает /app/data в Docker."""
    if state_path:
        return state_path
    if data_dir:
        return str(Path(data_dir) / "state.jsonl")
    return "data/state.jsonl"
