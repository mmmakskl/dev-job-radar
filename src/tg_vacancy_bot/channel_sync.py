"""Helpers for synchronising Telegram-folder chats into ``TARGET_CHANNELS``."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FolderChannel:
    """A channel or group returned from a Telegram dialog folder."""

    id: int
    name: str
    username: str | None


@dataclass(frozen=True)
class ChannelSyncResult:
    """The result of comparing configured sources with a dialog folder."""

    folder_name: str
    found_channels: tuple[FolderChannel, ...]
    added_channels: tuple[FolderChannel, ...]
    target_channels: tuple[str | int, ...]


def normalize_username(value: str | None) -> str:
    """Normalise an optional Telegram username for case-insensitive matching."""
    return (value or '').strip().lstrip('@').casefold()


def build_synced_target_channels(
    configured_channels: Iterable[str | int],
    folder_channels: Iterable[FolderChannel],
    *,
    resolved_configured_ids: Iterable[int] = (),
) -> tuple[tuple[str | int, ...], tuple[FolderChannel, ...]]:
    """Append missing folder chats while preserving the existing config order.

    Usernames already present in ``TARGET_CHANNELS`` are considered equivalent to
    their matching dialog. ``resolved_configured_ids`` covers the same case when
    a configured username was resolved through Telegram to a stable numeric ID.
    """
    targets = list(configured_channels)
    configured_ids = {value for value in targets if isinstance(value, int)} | set(
        resolved_configured_ids
    )
    configured_usernames = {
        normalize_username(value)
        for value in targets
        if isinstance(value, str) and normalize_username(value)
    }

    added_channels: list[FolderChannel] = []
    seen_folder_ids: set[int] = set()
    for channel in folder_channels:
        if channel.id in seen_folder_ids:
            continue
        seen_folder_ids.add(channel.id)

        if channel.id in configured_ids:
            continue
        if (
            channel.username
            and normalize_username(channel.username) in configured_usernames
        ):
            continue

        targets.append(channel.id)
        configured_ids.add(channel.id)
        added_channels.append(channel)

    return tuple(targets), tuple(added_channels)


def serialize_target_channels(channels: Iterable[str | int]) -> str:
    """Return the comma-separated value expected by ``TARGET_CHANNELS``."""
    return ','.join(
        str(channel).strip() for channel in channels if str(channel).strip()
    )


def replace_env_value(content: str, key: str, value: str) -> str:
    """Replace one dotenv assignment, preserving unrelated text and line endings."""
    assignment = re.compile(
        rf'^(?P<prefix>\s*(?:export\s+)?{re.escape(key)}\s*=)[^\r\n]*(?P<ending>\r?\n)?$'
    )
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = assignment.match(line)
        if match:
            lines[index] = (
                f"{match.group('prefix')}{value}{match.group('ending') or ''}"
            )
            return ''.join(lines)

    if content and not content.endswith(('\n', '\r')):
        content += '\n'
    return f'{content}{key}={value}\n'


def update_target_channels_env(
    env_path: Path, target_channels: Iterable[str | int]
) -> bool:
    """Atomically update ``TARGET_CHANNELS`` and return whether the file changed."""
    target_value = serialize_target_channels(target_channels)
    content = env_path.read_text(encoding='utf-8')
    updated_content = replace_env_value(content, 'TARGET_CHANNELS', target_value)
    if updated_content == content:
        return False

    file_mode = stat.S_IMODE(env_path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=env_path.parent,
        prefix=f'.{env_path.name}.',
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as temporary_file:
            temporary_file.write(updated_content)
        os.chmod(temporary_path, file_mode)
        os.replace(temporary_path, env_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return True
