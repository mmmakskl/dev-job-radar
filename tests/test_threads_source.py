import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tg_vacancy_bot.threads.source import (
    ThreadsError,
    ThreadsSource,
    normalize_permalink,
)

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
POST = {
    'id': '123',
    'text': 'We are hiring a Go engineer',
    'timestamp': '2026-09-14T12:00:00Z',
    'username': 'team',
    'permalink': 'https://www.threads.net/@team/post/ABC?x=1',
}


def run_search(handler, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = ThreadsSource('secret-token', client=client, **kwargs)
            return await source.search_page(
                'Go engineer', since=NOW - timedelta(days=1), until=NOW
            )

    return asyncio.run(run())


def test_page_and_cursor_and_parameters():
    def handler(request):
        assert request.url.host == 'graph.threads.net'
        assert request.url.params['search_mode'] == 'KEYWORD'
        assert 'owner' not in request.url.params['fields']
        assert 'secret-token' not in str(request.url)
        assert request.headers['Authorization'] == 'Bearer secret-token'
        return httpx.Response(
            200,
            json={
                'data': [POST, {}, 123],
                'paging': {'next': 'https://evil.example/?after=abc'},
            },
        )

    page = run_search(handler)
    assert page.posts[0].permalink == 'https://www.threads.com/@team/post/ABC'
    assert page.posts[0].source == 'threads'
    assert page.after == 'abc'
    assert page.invalid_count == 2


def test_empty_page():
    assert run_search(lambda _: httpx.Response(200, json={'data': []})).posts == []


@pytest.mark.parametrize('payload', [None, {}, {'data': {}}, []])
def test_bad_payload(payload):
    with pytest.raises(ThreadsError, match='invalid_response'):
        run_search(lambda _: httpx.Response(200, json=payload))


def test_rate_limit_no_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, headers={'Retry-After': '23'}, json={'error': 'secret-token'}
        )

    with pytest.raises(ThreadsError) as caught:
        run_search(handler)
    assert caught.value.reason == 'rate_limited'
    assert caught.value.retry_after == 23
    assert len(calls) == 1
    assert 'secret-token' not in str(caught.value)


@pytest.mark.parametrize(
    'code,reason',
    [(190, 'invalid_token'), (10, 'permission_denied'), (613, 'rate_limited')],
)
def test_graph_errors_safe(code, reason):
    with pytest.raises(ThreadsError) as caught:
        run_search(
            lambda _: httpx.Response(
                400, json={'error': {'code': code, 'message': 'secret-token'}}
            )
        )
    assert str(caught.value) == reason


@pytest.mark.parametrize('timeout', [True, False])
def test_retry_budget_hook(timeout):
    calls, reserved, delays = [], [], []

    async def reserve():
        reserved.append(1)

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            if timeout:
                raise httpx.ReadTimeout('secret-token', request=request)
            return httpx.Response(503)
        return httpx.Response(200, json={'data': [POST]})

    assert len(run_search(handler, before_request=reserve, sleep=sleep).posts) == 1
    assert len(calls) == len(reserved) == 3
    assert len(delays) == 2


def test_exhausted_timeout_safe():
    async def sleep(_):
        pass

    def handler(request):
        raise httpx.ReadTimeout('secret-token', request=request)

    with pytest.raises(ThreadsError, match='network_error') as caught:
        run_search(handler, sleep=sleep)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    'url',
    [
        'http://threads.net/@x/post/1',
        'https://evil.example/@x/post/1',
        'https://user:pass@threads.net/@x/post/1',
        'https://threads.net:444/@x/post/1',
        None,
    ],
)
def test_unsafe_links(url):
    assert normalize_permalink(url) is None
