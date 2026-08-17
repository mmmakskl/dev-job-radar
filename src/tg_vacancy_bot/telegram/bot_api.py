"""Small async wrapper around the Telegram Bot API used by the candidate UI."""

import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class BotApiError(RuntimeError):
    """A Telegram Bot API request did not complete successfully."""


class TelegramBotApi:
    """Uses Bot API HTTPS requests without introducing a second SDK dependency."""

    def __init__(self, token: str) -> None:
        self._endpoint = f"https://api.telegram.org/bot{token}"

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        payload: dict[str, Any] = {
            'timeout': timeout,
            'allowed_updates': ['message', 'callback_query'],
        }
        if offset is not None:
            payload['offset'] = offset
        result = await self._call('getUpdates', payload, timeout + 10)
        return result if isinstance(result, list) else []

    async def delete_webhook(self) -> None:
        """Ensures that Bot API updates are available to the long-polling worker."""
        await self._call('deleteWebhook', {'drop_pending_updates': False}, 20)

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'text': text,
            'disable_web_page_preview': disable_web_page_preview,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode
        if reply_markup:
            payload['reply_markup'] = reply_markup
        result = await self._call('sendMessage', payload, 20)
        return result if isinstance(result, dict) else {}

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        await self._call(
            'answerCallbackQuery',
            {'callback_query_id': callback_query_id, 'text': text[:200]},
            20,
        )

    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        """Edits one private browser card instead of sending a new message."""
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'disable_web_page_preview': True,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode
        if reply_markup:
            payload['reply_markup'] = reply_markup
        result = await self._call('editMessageText', payload, 20)
        return result if isinstance(result, dict) else {}

    async def _call(self, method: str, payload: dict[str, Any], timeout: int) -> Any:
        return await asyncio.to_thread(self._call_sync, method, payload, timeout)

    def _call_sync(self, method: str, payload: dict[str, Any], timeout: int) -> Any:
        request = Request(
            f'{self._endpoint}/{method}',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode('utf-8'))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BotApiError(f'Bot API request {method} failed') from exc
        if not body.get('ok'):
            raise BotApiError(f'Bot API request {method} was rejected')
        return body.get('result')
