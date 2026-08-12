from fastapi.testclient import TestClient

from tg_vacancy_bot.admin.api import create_app


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-password')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'session-secret-for-tests')
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    monkeypatch.setenv('TARGET_CHANNELS', '-100111,@public_jobs')
    return TestClient(create_app(str(tmp_path)))


def _login(client: TestClient) -> str:
    response = client.post('/api/v1/auth/login', json={'password': 'test-password'})
    assert response.status_code == 200
    return client.cookies.get('admin_csrf', '')


def test_api_requires_authentication(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get('/api/v1/settings').status_code == 401
    assert client.get('/healthz').json() == {'status': 'ok'}


def test_login_csrf_and_redacted_channels(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csrf = _login(client)
    settings = client.get('/api/v1/settings').json()

    assert settings['telegram']['channels'][0]['label'].startswith('Приватный')
    assert '-100111' not in str(settings)
    forbidden = client.put('/api/v1/settings', json={'filters': {'keywords': ['go']}})
    assert forbidden.status_code == 403
    saved = client.put(
        '/api/v1/settings',
        headers={'X-CSRF-Token': csrf},
        json={
            'filters': {'keywords': ['go', 'golang'], 'exclude_keywords': ['резюме']}
        },
    )
    assert saved.status_code == 200
    assert saved.json()['filters']['exclude_keywords'] == ['резюме']


def test_dangerous_action_requires_confirmation(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csrf = _login(client)
    assert (
        client.post(
            '/api/v1/actions',
            headers={'X-CSRF-Token': csrf},
            json={'action': 'restart'},
        ).status_code
        == 400
    )
    response = client.post(
        '/api/v1/actions',
        headers={'X-CSRF-Token': csrf},
        json={'action': 'restart', 'confirmed': True},
    )
    assert response.status_code == 200
