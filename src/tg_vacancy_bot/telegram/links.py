"""Формирование ссылок на сообщения Telegram."""

import re
from urllib.parse import urlparse

from telethon.tl.types import Channel, Message


def get_event_message_link(event) -> str:
    """Сформировать ссылку на сообщение из события Telethon.

    During catch-up Telethon can deliver an update before its chat entity is
    cached.  ``event.chat_id`` remains available in that case.
    """
    chat = event.chat
    msg_id = event.message.id

    if isinstance(chat, Channel) and chat.username:
        return f"https://t.me/{chat.username}/{msg_id}"

    chat_id = getattr(chat, "id", None) or getattr(event, "chat_id", None)
    if chat_id is None:
        raise ValueError("Невозможно определить Telegram-чат для ссылки на сообщение")
    chat_id = str(chat_id).replace('-100', '')
    return f"https://t.me/c/{chat_id}/{msg_id}"


def get_message_link(message: Message) -> str:
    """Сформировать ссылку на историческое сообщение Telethon."""
    if message.chat.username:
        return f"https://t.me/{message.chat.username}/{message.id}"

    chat_id = str(message.chat_id).replace('-100', '')
    return f"https://t.me/c/{chat_id}/{message.id}"


def build_vacancy_id(post_link: str) -> str:
    """Строит стабильный ID из Telegram-канала и ID сообщения."""
    parts = [part for part in urlparse(post_link).path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"Невозможно построить ID из Telegram-ссылки: {post_link}")
    if parts[0] == "c" and len(parts) >= 3:
        source, message_id = f"c_{parts[1]}", parts[2]
    else:
        source, message_id = parts[-2], parts[-1]
    safe_source = re.sub(r"[^a-zA-Z0-9_]+", "_", source).strip("_").lower()
    safe_message_id = re.sub(r"\D", "", message_id)
    if not safe_source or not safe_message_id:
        raise ValueError(f"Невозможно построить ID из Telegram-ссылки: {post_link}")
    return f"{safe_source}_{safe_message_id}"


def get_event_channel_name(event) -> str:
    """Возвращает username, title или ID канала live-события."""
    chat = event.chat
    return (
        getattr(chat, "username", None)
        or getattr(chat, "title", None)
        or str(getattr(chat, "id", "Не указано"))
    )


def get_message_channel_name(message: Message) -> str:
    """Возвращает username, title или ID канала исторического сообщения."""
    chat = message.chat
    return (
        getattr(chat, "username", None)
        or getattr(chat, "title", None)
        or str(message.chat_id)
    )
