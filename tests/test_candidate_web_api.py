"""Candidate Mini App routes use Telegram identity and the existing stores."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from tests.test_multidirection_candidate_feed import decision_for
from tg_vacancy_bot.admin.api import create_app
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.search.store import SearchStore
from tg_vacancy_bot.telegram.candidate_store import CandidateStore

TOKEN = '123456:test-token'


def _headers(user_id: int, *, age: int = 0, token: str = TOKEN) -> dict[str, str]:
    values = {
        'auth_date': str(int(time.time()) - age),
        'user': json.dumps(
            {'id': user_id, 'first_name': 'Tester'}, separators=(',', ':')
        ),
    }
    check = '\n'.join(f'{key}={value}' for key, value in sorted(values.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {'X-Telegram-Init-Data': urlencode(values)}


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv('CANDIDATE_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('CANDIDATE_BOT_ALLOWED_USER_IDS', '1,2')
    monkeypatch.setenv('CANDIDATE_BOT_DB_PATH', str(tmp_path / 'candidate_bot.sqlite3'))
    monkeypatch.setenv('CANDIDATE_CATALOG_SEARCH_ENABLED', 'true')
    monkeypatch.setenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'true')
    monkeypatch.setenv('ADMIN_STATIC_DIR', str(tmp_path / 'no-static'))
    return TestClient(create_app(str(tmp_path)), base_url='https://testserver')


def _profile_payload(name: str = 'Go Backend') -> dict:
    return {
        'name': name,
        'direction_id': 'development',
        'role_id': 'backend_developer',
        'seniority': ['middle'],
        'required_skills': ['go'],
        'excluded_skills': ['стажировка'],
        'delivery_mode': 'immediate',
    }


def test_candidate_web_rejects_missing_tampered_expired_and_unlisted_identity(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    endpoint = '/api/v1/candidate/me'

    assert client.get(endpoint).status_code == 401
    valid = _headers(1)
    assert client.get(endpoint, headers=valid).json()['user']['id'] == 1
    assert client.get(endpoint, headers=_headers(3)).status_code == 403
    assert client.get(endpoint, headers=_headers(1, age=90_000)).status_code == 401
    assert client.get(endpoint, headers=_headers(1, token='wrong')).status_code == 401
    forged = valid['X-Telegram-Init-Data'].replace('%22id%22%3A1', '%22id%22%3A2')
    assert (
        client.get(endpoint, headers={'X-Telegram-Init-Data': forged}).status_code
        == 401
    )


def test_browser_code_login_is_one_time_and_session_is_owner_scoped(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    store = CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
    code = store.issue_web_login_code(1, TOKEN)
    profile = store.create_profile(
        1,
        name='Private',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
    )
    store.create_profile(
        2,
        name='Other',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
    )

    login = client.post('/api/v1/candidate/auth/code', json={'code': code})
    assert login.status_code == 200
    assert login.cookies['candidate_session']
    assert login.json()['csrf_token']
    assert (
        client.post('/api/v1/candidate/auth/code', json={'code': code}).status_code
        == 401
    )
    me = client.get('/api/v1/candidate/me')
    assert me.status_code == 200
    assert [item['profile_id'] for item in me.json()['profiles']] == [
        profile.profile_id
    ]

    payload = _profile_payload('From browser')
    assert client.post('/api/v1/candidate/profiles', json=payload).status_code == 403
    csrf = {'X-Candidate-CSRF': login.json()['csrf_token']}
    assert (
        client.post(
            '/api/v1/candidate/profiles', json=payload, headers=csrf
        ).status_code
        == 201
    )
    assert client.post('/api/v1/candidate/auth/logout', headers=csrf).status_code == 200
    assert client.get('/api/v1/candidate/me').status_code == 401


def test_browser_login_rejects_expired_code_and_limits_guesses(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store = CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
    code = store.issue_web_login_code(1, TOKEN)
    with store._connect() as connection:
        connection.execute('UPDATE candidate_web_login_codes SET expires_at = 0')
    assert (
        client.post('/api/v1/candidate/auth/code', json={'code': code}).status_code
        == 401
    )
    for _ in range(20):
        client.post('/api/v1/candidate/auth/code', json={'code': '0000000000'})
    assert (
        client.post(
            '/api/v1/candidate/auth/code', json={'code': '0000000000'}
        ).status_code
        == 429
    )


def test_browser_login_replaces_code_and_expires_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store = CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
    old_code = store.issue_web_login_code(1, TOKEN)
    new_code = store.issue_web_login_code(1, TOKEN)
    assert old_code != new_code
    assert (
        client.post('/api/v1/candidate/auth/code', json={'code': old_code}).status_code
        == 401
    )
    login = client.post('/api/v1/candidate/auth/code', json={'code': new_code})
    assert login.status_code == 200
    assert 'HttpOnly' in login.headers['set-cookie']
    assert 'Secure' in login.headers['set-cookie']
    assert 'SameSite=strict' in login.headers['set-cookie']
    assert client.get('/api/v1/candidate/me').status_code == 200
    with store._connect() as connection:
        connection.execute('UPDATE candidate_web_sessions SET expires_at=0')
    assert client.get('/api/v1/candidate/me').status_code == 401


def test_candidate_web_profiles_share_chat_state_and_reject_stale_or_foreign_edits(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _headers(1)
    other = _headers(2)

    created = client.post(
        '/api/v1/candidate/profiles', headers=owner, json=_profile_payload()
    )
    assert created.status_code == 201
    first = created.json()
    assert first['is_active'] is True
    assert first['version'] == 1
    assert (
        CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
        .get_active_profile(1)
        .name
        == 'Go Backend'
    )

    second = client.post(
        '/api/v1/candidate/profiles', headers=owner, json=_profile_payload('Second')
    )
    assert second.status_code == 201
    assert second.json()['is_active'] is False
    assert (
        len(client.get('/api/v1/candidate/me', headers=other).json()['profiles']) == 0
    )

    profile_id = second.json()['profile_id']
    assert (
        client.post(
            f'/api/v1/candidate/profiles/{profile_id}/activate', headers=other
        ).status_code
        == 404
    )
    selected = client.post(
        f'/api/v1/candidate/profiles/{profile_id}/activate', headers=owner
    )
    assert selected.status_code == 200
    assert selected.json()['is_active'] is True
    assert (
        CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
        .get_active_profile(1)
        .profile_id
        == profile_id
    )

    stale = client.put(
        f'/api/v1/candidate/profiles/{profile_id}',
        headers=owner,
        json={**_profile_payload('Edited'), 'version': 1},
    )
    assert stale.status_code == 409
    updated = client.put(
        f'/api/v1/candidate/profiles/{profile_id}',
        headers=owner,
        json={**_profile_payload('Edited'), 'version': selected.json()['version']},
    )
    assert updated.status_code == 200
    assert updated.json()['name'] == 'Edited'
    assert (
        client.delete(
            f'/api/v1/candidate/profiles/{profile_id}', headers=other
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f'/api/v1/candidate/profiles/{profile_id}', headers=owner
        ).status_code
        == 204
    )


def test_candidate_web_saves_role_stack_and_multiple_grades(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    owner = _headers(1)
    payload = {
        **_profile_payload(),
        'stacks': ['go', 'cpp'],
        'seniority': ['middle', 'senior'],
    }

    created = client.post('/api/v1/candidate/profiles', headers=owner, json=payload)

    assert created.status_code == 201
    preferences = created.json()['preferences']
    assert preferences['stacks'] == ['go', 'cpp']
    assert preferences['seniority'] == ['middle', 'senior']
    assert (
        client.post(
            '/api/v1/candidate/profiles',
            headers=owner,
            json={**payload, 'stacks': ['react']},
        ).status_code
        == 422
    )


def test_candidate_web_feed_and_saved_archive_use_real_registry_and_owner(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _headers(1)
    other = _headers(2)
    store = CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
    store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={
            'profile_contract': 'catalog-v3',
            'required_skills': ['go'],
            'delivery_mode': 'immediate',
        },
    )
    registry = VacancyRegistry(store.path)
    registry.ingest(
        vacancy_id='own',
        post_link='https://t.me/jobs/own',
        decision=decision_for(
            'development', 'backend', 'backend_developer', 'Go', 'Go role'
        ),
        published_at='2026-10-04T08:00:00+00:00',
        eligible_at='2026-10-04T08:01:00+00:00',
        raw_text='Ищем специалиста. Основной инструмент: Go.',
        channel_name='jobs',
    )
    registry.ingest(
        vacancy_id='foreign',
        post_link='https://t.me/jobs/foreign',
        decision=decision_for(
            'development', 'frontend', 'frontend_developer', 'React', 'Frontend role'
        ),
        published_at='2026-10-04T08:00:00+00:00',
        eligible_at='2026-10-04T08:01:00+00:00',
        raw_text='Ищем специалиста. Основной инструмент: React.',
        channel_name='jobs',
    )

    feed = client.get('/api/v1/candidate/feed?bucket=all', headers=owner)
    assert feed.status_code == 200
    assert [item['vacancy_id'] for item in feed.json()['items']] == ['own']
    assert feed.json()['items'][0]['title'] == 'Go role'
    assert (
        client.get('/api/v1/candidate/feed?bucket=all', headers=other).json()['items']
        == []
    )

    saved = client.put('/api/v1/candidate/saved/own', headers=owner)
    assert saved.status_code == 200
    assert saved.json()['saved'] is True
    assert [
        item['vacancy_id']
        for item in client.get('/api/v1/candidate/saved', headers=owner).json()['items']
    ] == ['own']
    assert client.get('/api/v1/candidate/saved', headers=other).json()['items'] == []
    assert (
        client.delete('/api/v1/candidate/saved/own', headers=owner).json()['saved']
        is False
    )
    assert client.get('/api/v1/candidate/saved', headers=owner).json()['items'] == []

    settings = client.put(
        '/api/v1/candidate/feed/settings', headers=owner, json={'older_days': 14}
    )
    assert settings.status_code == 200
    assert settings.json()['older_days'] == 14
    assert store.get_older_days(1) == 14
    assert (
        client.get('/api/v1/candidate/feed/settings', headers=other).json()[
            'older_days'
        ]
        == 7
    )


def test_candidate_web_premium_uses_active_profile_queue_and_owner_scope(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    owner = _headers(1)
    other = _headers(2)
    store = CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
    store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={
            'profile_contract': 'catalog-v3',
            'required_skills': ['go'],
            'delivery_mode': 'immediate',
        },
    )
    templates = client.get('/api/v1/candidate/premium/templates', headers=owner)
    assert templates.status_code == 200
    template_id = templates.json()['items'][0]['id']
    started = client.post(
        '/api/v1/candidate/premium/runs',
        headers=owner,
        json={'template_id': template_id, 'client_request_id': 'web-intent-1'},
    )
    assert started.status_code == 202
    run_id = started.json()['id']
    assert started.json()['status'] == 'queued'
    again = client.post(
        '/api/v1/candidate/premium/runs',
        headers=owner,
        json={'template_id': template_id, 'client_request_id': 'web-intent-1'},
    )
    assert again.json()['id'] == run_id
    assert (
        client.get(
            f'/api/v1/candidate/premium/runs/{run_id}', headers=other
        ).status_code
        == 404
    )
    assert (
        client.get(
            f'/api/v1/candidate/premium/runs/{run_id}/results', headers=other
        ).status_code
        == 404
    )
    assert (
        client.get('/api/v1/candidate/premium/runs', headers=owner).json()['items'][0][
            'id'
        ]
        == run_id
    )
    searches = SearchStore(str(tmp_path / 'admin' / 'admin.sqlite3'))
    result_id = searches.retain(
        run_id,
        source='premium',
        external_id='message-1',
        text='Go vacancy',
        timestamp='2026-11-05T10:00:00+00:00',
        permalink='https://t.me/jobs/99',
        classification='accepted',
        title='Go Engineer',
        company='Acme',
        summary='Go backend role',
    )
    assert (
        client.get(
            f'/api/v1/candidate/premium/runs/{run_id}/results', headers=owner
        ).json()['items'][0]['title']
        == 'Go Engineer'
    )
    save_url = f'/api/v1/candidate/premium/runs/{run_id}/results/{result_id}/saved'
    assert client.put(save_url, headers=other).status_code == 404
    assert client.put(save_url, headers=owner).json()['saved'] is True
    assert (
        client.get(
            f'/api/v1/candidate/premium/runs/{run_id}/results', headers=owner
        ).json()['items'][0]['saved']
        is True
    )
    assert [
        item['title']
        for item in client.get('/api/v1/candidate/saved', headers=owner).json()['items']
    ] == ['Go Engineer']
    assert (
        client.post(
            f'/api/v1/candidate/premium/runs/{run_id}/cancel', headers=other
        ).status_code
        == 404
    )
    assert (
        client.post(
            f'/api/v1/candidate/premium/runs/{run_id}/cancel', headers=owner
        ).json()['status']
        == 'cancelled'
    )


def test_miniapp_page_allows_telegram_bridge_and_retired_preview_is_gone(
    tmp_path, monkeypatch
) -> None:
    static = tmp_path / 'static'
    (static / '_next').mkdir(parents=True)
    (static / 'miniapp').mkdir()
    (static / 'miniapp' / 'index.html').write_text('<h1>Mini App</h1>')
    (static / 'index.html').write_text('<h1>Admin</h1>')
    monkeypatch.setenv('ADMIN_STATIC_DIR', str(static))
    client = TestClient(create_app(str(tmp_path)))

    miniapp = client.get('/miniapp/')
    assert miniapp.status_code == 200
    assert miniapp.headers['x-frame-options'] == 'SAMEORIGIN'
    assert 'https://telegram.org' in miniapp.headers['content-security-policy']
    assert client.get('/miniapp-preview/').status_code == 404
    assert client.get('/').headers['x-frame-options'] == 'DENY'


def test_profile_filters_round_trip_and_reject_invalid_values(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    owner = _headers(1)
    payload = {
        **_profile_payload(),
        'formats': ['remote', 'hybrid'],
        'geography': ['europe', 'usa'],
        'vacancy_languages': ['ru', 'en'],
    }
    response = client.post('/api/v1/candidate/profiles', headers=owner, json=payload)
    assert response.status_code == 201
    prefs = response.json()['preferences']
    assert prefs['candidate_filter_version'] == 1
    assert prefs['formats'] == ['remote', 'hybrid']
    assert prefs['geography'] == ['europe', 'usa']
    assert prefs['vacancy_languages'] == ['ru', 'en']
    assert (
        client.post(
            '/api/v1/candidate/profiles',
            headers=owner,
            json={**payload, 'formats': ['invalid']},
        ).status_code
        == 422
    )


def test_source_text_requires_access_and_preserves_full_text(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    client = _client(tmp_path, monkeypatch)
    owner = _headers(1)
    store = CandidateStore(str(tmp_path / 'candidate_bot.sqlite3'))
    store.create_profile(
        1,
        name='Backend',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'profile_contract': 'catalog-v3'},
    )
    registry = VacancyRegistry(store.path)
    raw = '  Ищем Go специалиста.\n' + 'Полное описание.\n' * 500
    registry.ingest(
        vacancy_id='jobs_42',
        post_link='https://t.me/jobs/42',
        decision=decision_for(
            'development', 'backend', 'backend_developer', 'Go', 'Go developer'
        ),
        eligible_at=datetime.now(timezone.utc),
        raw_text=raw,
    )
    response = client.get('/api/v1/candidate/vacancies/jobs_42/text', headers=owner)
    assert response.status_code == 200
    assert response.json()['text'] == raw
    assert (
        client.get(
            '/api/v1/candidate/vacancies/jobs_42/text', headers=_headers(2)
        ).status_code
        == 404
    )
