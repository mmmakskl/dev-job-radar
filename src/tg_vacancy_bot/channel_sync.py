"""Telegram-folder discovery plus deprecated legacy `.env` helpers."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from telethon import utils
from telethon.tl import functions


@dataclass(frozen=True)
class FolderChannel:
    """A channel or group returned from a Telegram dialog folder."""

    id: int
    name: str
    username: str | None
    chat_type: str = 'unknown'


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


def find_folder_filter(dialog_filters: Iterable[object], folder_name: str) -> object:
    """Return a custom Telegram dialog filter by its visible title."""
    expected_name = folder_name.strip().casefold()
    matching_filters = []
    for dialog_filter in dialog_filters:
        title = getattr(getattr(dialog_filter, 'title', None), 'text', '')
        folder_id = getattr(dialog_filter, 'id', None)
        if folder_id is not None and title.strip().casefold() == expected_name:
            matching_filters.append(dialog_filter)

    if not matching_filters:
        raise RuntimeError(
            f'Папка Telegram «{folder_name}» не найдена. '
            'Создайте её и добавьте в неё каналы или группы.'
        )
    if len(matching_filters) > 1:
        raise RuntimeError(
            f'Найдено несколько Telegram-папок с названием «{folder_name}». '
            'Переименуйте одну из них.'
        )
    return matching_filters[0]


def find_folder_id(dialog_filters: Iterable[object], folder_name: str) -> int:
    """Return the numeric ID of a custom Telegram dialog filter."""
    return int(getattr(find_folder_filter(dialog_filters, folder_name), 'id'))


def build_synced_target_channels(
    configured_channels: Iterable[str | int],
    folder_channels: Iterable[FolderChannel],
    *,
    resolved_configured_ids: Iterable[int] = (),
) -> tuple[tuple[str | int, ...], tuple[FolderChannel, ...]]:
    """Append missing folder chats while preserving the existing config order.

    Usernames already present in a legacy target list are considered equivalent
    to their matching dialog. ``resolved_configured_ids`` covers the same case
    when a configured username resolves to a stable numeric ID.
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


async def fetch_folder_channels(client, folder_name: str) -> list[FolderChannel]:
    """Fetch and validate the complete folder before any persistent mutation."""
    response = await client(functions.messages.GetDialogFiltersRequest())
    dialog_filter = find_folder_filter(response.filters, folder_name)
    included_ids = {
        utils.get_peer_id(peer)
        for name in ('pinned_peers', 'include_peers')
        for peer in getattr(dialog_filter, name, [])
    }
    excluded_ids = {
        utils.get_peer_id(peer) for peer in getattr(dialog_filter, 'exclude_peers', [])
    }
    include_groups = bool(getattr(dialog_filter, 'groups', False))
    include_broadcasts = bool(getattr(dialog_filter, 'broadcasts', False))
    channels: list[FolderChannel] = []
    async for dialog in client.iter_dialogs():
        if not (dialog.is_channel or dialog.is_group) or dialog.id in excluded_ids:
            continue
        included = dialog.id in included_ids
        included = included or (include_groups and dialog.is_group)
        included = included or (
            include_broadcasts and dialog.is_channel and not dialog.is_group
        )
        if included:
            channels.append(
                FolderChannel(
                    id=dialog.id,
                    name=dialog.name,
                    username=getattr(dialog.entity, 'username', None),
                    chat_type='group' if dialog.is_group else 'channel',
                )
            )
    return channels


# Deprecated compatibility helpers. Production workflows never call these;
# SQLite is the sole live source store.
def serialize_target_channels(channels: Iterable[str | int]) -> str:
    return ','.join(
        str(channel).strip() for channel in channels if str(channel).strip()
    )


def replace_env_value(content: str, key: str, value: str) -> str:
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
    content = env_path.read_text(encoding='utf-8')
    updated = replace_env_value(
        content, 'TARGET_CHANNELS', serialize_target_channels(target_channels)
    )
    if updated == content:
        return False
    descriptor, temporary_name = tempfile.mkstemp(dir=env_path.parent, text=True)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(updated)
        os.chmod(temporary_path, stat.S_IMODE(env_path.stat().st_mode))
        os.replace(temporary_path, env_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return True
