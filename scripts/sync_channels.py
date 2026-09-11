"""Synchronise a Telegram dialog folder into durable admin state."""

import argparse
import asyncio

from telethon import TelegramClient

from tg_vacancy_bot import config
from tg_vacancy_bot.admin.settings import SettingsStore
from tg_vacancy_bot.channel_sync import fetch_folder_channels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Синхронизировать Telegram-папку.')
    parser.add_argument('--folder', default=config.TELEGRAM_CHANNELS_FOLDER)
    return parser.parse_args()


async def synchronize_folder(folder_name: str) -> dict[str, int]:
    config.validate_required_settings(
        require_mistral=False,
        require_google_sheets=False,
        require_sources=False,
    )
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError('Сессия Telegram не авторизована. Выполните make auth.')
        channels = await fetch_folder_channels(client, folder_name)
        return SettingsStore(config.DATA_DIR).sync_folder(channels)
    finally:
        await client.disconnect()


async def main() -> None:
    args = parse_args()
    counts = await synchronize_folder(args.folder)
    print(
        f"Папка «{args.folder}»: {counts['received']} чатов; "
        f"добавлено {counts['created']}, обновлено {counts['updated']}."
    )


if __name__ == '__main__':
    asyncio.run(main())
