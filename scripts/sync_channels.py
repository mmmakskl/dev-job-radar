"""Synchronise channels from a Telegram dialog folder into TARGET_CHANNELS."""

import argparse
import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.tl import functions

from tg_vacancy_bot import config
from tg_vacancy_bot.channel_sync import (
    ChannelSyncResult,
    FolderChannel,
    build_synced_target_channels,
    find_folder_filter,
    serialize_target_channels,
    update_target_channels_env,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Добавить чаты из Telegram-папки в TARGET_CHANNELS.',
    )
    parser.add_argument(
        '--folder',
        default=config.TELEGRAM_CHANNELS_FOLDER,
        help='Название Telegram-папки (по умолчанию TELEGRAM_CHANNELS_FOLDER).',
    )
    parser.add_argument(
        '--env-file',
        type=Path,
        default=Path('.env'),
        help='Файл dotenv для обновления (по умолчанию .env).',
    )
    parser.add_argument(
        '--report-only',
        action='store_true',
        help='Не менять .env; вывести итоговый TARGET_CHANNELS в stdout.',
    )
    return parser.parse_args()


async def get_folder_filter(client: TelegramClient, folder_name: str) -> object:
    """Return the custom dialog filter selected by its visible title."""
    response = await client(functions.messages.GetDialogFiltersRequest())
    return find_folder_filter(response.filters, folder_name)


def filter_peer_ids(dialog_filter: object, attribute: str) -> set[int]:
    """Convert dialog-filter input peers to Telethon's stable marked IDs."""
    return {utils.get_peer_id(peer) for peer in getattr(dialog_filter, attribute, [])}


def dialog_is_in_folder(
    dialog,
    *,
    included_ids: set[int],
    excluded_ids: set[int],
    include_groups: bool,
    include_broadcasts: bool,
) -> bool:
    """Determine whether a monitorable dialog belongs to a custom filter."""
    if dialog.id in excluded_ids:
        return False
    if dialog.id in included_ids:
        return True
    if include_groups and dialog.is_group:
        return True
    return include_broadcasts and dialog.is_channel and not dialog.is_group


async def get_folder_channels(
    client: TelegramClient, dialog_filter: object
) -> list[FolderChannel]:
    """Get monitorable chats selected by a custom Telegram dialog filter."""
    included_ids = filter_peer_ids(dialog_filter, 'pinned_peers')
    included_ids.update(filter_peer_ids(dialog_filter, 'include_peers'))
    excluded_ids = filter_peer_ids(dialog_filter, 'exclude_peers')
    include_groups = bool(getattr(dialog_filter, 'groups', False))
    include_broadcasts = bool(getattr(dialog_filter, 'broadcasts', False))
    channels = []
    async for dialog in client.iter_dialogs():
        if not (dialog.is_channel or dialog.is_group):
            continue
        if not dialog_is_in_folder(
            dialog,
            included_ids=included_ids,
            excluded_ids=excluded_ids,
            include_groups=include_groups,
            include_broadcasts=include_broadcasts,
        ):
            continue
        channels.append(
            FolderChannel(
                id=dialog.id,
                name=dialog.name,
                username=getattr(dialog.entity, 'username', None),
            )
        )
    return channels


async def resolve_configured_ids(client: TelegramClient) -> set[int]:
    """Resolve configured usernames so they are not duplicated as numeric IDs."""
    resolved_ids = {
        channel for channel in config.TARGET_CHANNELS if isinstance(channel, int)
    }
    for channel in config.TARGET_CHANNELS:
        if isinstance(channel, int):
            continue
        try:
            entity = await client.get_entity(channel)
            resolved_ids.add(utils.get_peer_id(entity))
        except Exception as error:  # An unavailable old source must not block sync.
            logging.warning(
                'Не удалось определить Telegram ID для TARGET_CHANNELS=%s: %s',
                channel,
                error,
            )
    return resolved_ids


async def synchronize_folder(folder_name: str) -> ChannelSyncResult:
    """Read a Telegram folder and calculate the updated source list."""
    config.validate_required_settings(
        require_mistral=False,
        require_google_sheets=False,
    )
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError('Сессия Telegram не авторизована. Выполните make auth.')

        dialog_filter = await get_folder_filter(client, folder_name)
        found_channels = await get_folder_channels(client, dialog_filter)
        resolved_ids = await resolve_configured_ids(client)
        target_channels, added_channels = build_synced_target_channels(
            config.TARGET_CHANNELS,
            found_channels,
            resolved_configured_ids=resolved_ids,
        )
        return ChannelSyncResult(
            folder_name=folder_name,
            found_channels=tuple(found_channels),
            added_channels=added_channels,
            target_channels=target_channels,
        )
    finally:
        await client.disconnect()


def print_result(result: ChannelSyncResult, changed: bool) -> None:
    """Print an operator-friendly result without exposing configuration secrets."""
    print(f'Папка «{result.folder_name}»: {len(result.found_channels)} чатов.')
    if result.added_channels:
        print(f'Добавлено в TARGET_CHANNELS: {len(result.added_channels)}')
        for channel in result.added_channels:
            print(f'  + {channel.name} ({channel.id})')
    else:
        print('Новых чатов нет.')
    print('Файл .env обновлён.' if changed else 'TARGET_CHANNELS уже актуален.')


async def main() -> None:
    args = parse_args()
    result = await synchronize_folder(args.folder)

    if args.report_only:
        print(serialize_target_channels(result.target_channels))
        return

    if not args.env_file.exists():
        raise RuntimeError(f'Файл конфигурации не найден: {args.env_file}')
    changed = update_target_channels_env(args.env_file, result.target_channels)
    print_result(result, changed)


if __name__ == '__main__':
    asyncio.run(main())
