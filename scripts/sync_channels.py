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


async def get_folder_id(client: TelegramClient, folder_name: str) -> int:
    """Return a custom Telegram folder ID by its visible title."""
    expected_name = folder_name.strip().casefold()
    matching_ids = []
    filters = await client(functions.messages.GetDialogFiltersRequest())
    for dialog_filter in filters:
        title = getattr(getattr(dialog_filter, 'title', None), 'text', '')
        folder_id = getattr(dialog_filter, 'id', None)
        if folder_id is not None and title.strip().casefold() == expected_name:
            matching_ids.append(folder_id)

    if not matching_ids:
        raise RuntimeError(
            f'Папка Telegram «{folder_name}» не найдена. '
            'Создайте её и добавьте в неё каналы или группы.'
        )
    if len(matching_ids) > 1:
        raise RuntimeError(
            f'Найдено несколько Telegram-папок с названием «{folder_name}». '
            'Переименуйте одну из них.'
        )
    return matching_ids[0]


async def get_folder_channels(
    client: TelegramClient, folder_id: int
) -> list[FolderChannel]:
    """Get monitorable groups and channels from one custom dialog folder."""
    channels = []
    async for dialog in client.iter_dialogs(folder=folder_id):
        if not (dialog.is_channel or dialog.is_group):
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

        folder_id = await get_folder_id(client, folder_name)
        found_channels = await get_folder_channels(client, folder_id)
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
