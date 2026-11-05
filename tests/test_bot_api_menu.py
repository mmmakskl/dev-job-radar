"""Telegram menu button uses the bot's default private-chat slot."""

import asyncio
from unittest.mock import AsyncMock

from tg_vacancy_bot.telegram.bot_api import TelegramBotApi


def test_set_chat_menu_button_uses_default_chat_scope():
    api = TelegramBotApi('test-token')
    api._call = AsyncMock(return_value=True)
    button = {
        'type': 'web_app',
        'text': 'Открыть приложение',
        'web_app': {'url': 'https://example.com/miniapp/'},
    }

    asyncio.run(api.set_chat_menu_button(button))

    api._call.assert_awaited_once_with('setChatMenuButton', {'menu_button': button}, 20)
