"""Safe public-source validation through the existing Telethon integration."""

from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

from tg_vacancy_bot import config


async def verify_public_source(identifier: str) -> None:
    """Ensure a public username resolves to a channel/group before it is saved.

    The function deliberately does not return entity data or numeric IDs. A
    Telegram/session/network failure is converted into a neutral operator error
    by the API caller and never creates a valid managed source.
    """
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError('Telegram-сессия не авторизована')
        entity = await client.get_entity(identifier)
        if not isinstance(entity, (Channel, Chat)):
            raise RuntimeError('Username не принадлежит каналу или группе')
    finally:
        if client.is_connected():
            await client.disconnect()
