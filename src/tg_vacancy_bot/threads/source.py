"""Official keyword search; errors never include credentials or response bodies."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx


class ThreadsError(Exception):
    def __init__(self, reason: str, retry_after: float | None = None):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


@dataclass(frozen=True)
class ThreadsPost:
    external_id: str
    text: str
    username: str | None
    permalink: str | None
    timestamp: datetime
    raw_data: dict
    source: str = 'threads'


@dataclass(frozen=True)
class ThreadsPage:
    posts: list[ThreadsPost]
    after: str | None = None
    invalid_count: int = 0


def normalize_permalink(value: object) -> str | None:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme != 'https'
            or url.hostname
            not in {'threads.net', 'www.threads.net', 'threads.com', 'www.threads.com'}
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or not url.path.startswith('/@')
            or '/post/' not in url.path
        ):
            return None
        return urlunsplit(('https', 'www.threads.com', url.path.rstrip('/'), '', ''))
    except ValueError:
        return None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0, min(float(value), 86400))
    except ValueError:
        try:
            return max(
                0,
                (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds(),
            )
        except (ValueError, TypeError, OverflowError):
            return None


class ThreadsSource:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 20,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
        before_request: Callable[[], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if not token or not 1 <= max_attempts <= 5:
            raise ValueError('invalid_threads_configuration')
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned = client is None
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._before_request = before_request
        self._sleep = sleep

    async def __aenter__(self) -> ThreadsSource:
        return self

    async def __aexit__(self, *args) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def search_page(
        self,
        query: str,
        *,
        since: datetime,
        until: datetime,
        limit: int = 50,
        after: str | None = None,
    ) -> ThreadsPage:
        if (
            not query.strip()
            or not 1 <= limit <= 100
            or since.tzinfo is None
            or until.tzinfo is None
            or since >= until
        ):
            raise ValueError('invalid_search_parameters')
        params = {
            'q': query,
            'search_type': 'RECENT',
            'search_mode': 'KEYWORD',
            'since': str(int(since.timestamp())),
            'until': str(int(until.timestamp())),
            'limit': str(limit),
            'fields': 'id,text,username,permalink,timestamp,link_attachment_url,is_quote_post,is_reply',
        }
        if after:
            params['after'] = after
        for attempt in range(self._max_attempts):
            if self._before_request:
                await self._before_request()
            retry_reason = None
            try:
                response = await self._client.get(
                    'https://graph.threads.net/v1.0/keyword_search',
                    params=params,
                    headers={'Authorization': f'Bearer {self._token}'},
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except (httpx.TimeoutException, httpx.TransportError):
                retry_reason = 'network_error'
            else:
                if response.status_code == 429:
                    raise ThreadsError(
                        'rate_limited',
                        _retry_after(response.headers.get('retry-after')),
                    )
                if response.status_code >= 500:
                    retry_reason = 'service_unavailable'
                else:
                    try:
                        payload = response.json()
                    except ValueError:
                        raise ThreadsError('invalid_response') from None
                    error = payload.get('error') if isinstance(payload, dict) else None
                    if error is not None or response.status_code >= 400:
                        code = error.get('code') if isinstance(error, dict) else None
                        if code == 190 or response.status_code == 401:
                            raise ThreadsError('invalid_token')
                        if code in (10, 200) or response.status_code == 403:
                            raise ThreadsError('permission_denied')
                        if code in (4, 17, 32, 613):
                            raise ThreadsError('rate_limited')
                        raise ThreadsError('api_error')
                    if response.status_code != 200:
                        raise ThreadsError('invalid_response')
                    return self._parse_page(payload)
            if attempt + 1 == self._max_attempts:
                raise ThreadsError(retry_reason) from None
            await self._sleep(2**attempt + random.uniform(0, 0.25))
        raise ThreadsError('network_error')

    @staticmethod
    def _parse_page(payload: object) -> ThreadsPage:
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ThreadsError('invalid_response')
        posts = []
        invalid = 0
        for item in payload['data']:
            try:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get('id'), str)
                    or not item['id']
                    or not isinstance(item.get('text'), str)
                ):
                    raise ValueError
                timestamp = datetime.fromisoformat(
                    item['timestamp'].replace('Z', '+00:00')
                )
                if timestamp.tzinfo is None:
                    raise ValueError
                username = item.get('username')
                posts.append(
                    ThreadsPost(
                        item['id'],
                        item['text'],
                        username if isinstance(username, str) else None,
                        normalize_permalink(item.get('permalink')),
                        timestamp,
                        dict(item),
                    )
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                invalid += 1
        paging = payload.get('paging')
        after = None
        if isinstance(paging, dict):
            next_url = paging.get('next')
            if isinstance(next_url, str):
                try:
                    after = parse_qs(urlsplit(next_url).query).get('after', [None])[0]
                except ValueError:
                    pass
            cursors = paging.get('cursors')
            if after is None and isinstance(cursors, dict):
                after = cursors.get('after')
        return ThreadsPage(
            posts, after if isinstance(after, str) and after else None, invalid
        )
