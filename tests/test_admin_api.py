from fastapi.testclient import TestClient

from tg_vacancy_bot.admin.api import create_app
from tg_vacancy_bot.admin.telemetry import TelemetryStore


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


def test_metrics_errors_logs_prompt_and_sources_api(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csrf = _login(client)
    telemetry = TelemetryStore(str(tmp_path))
    telemetry.record_metric('post_processed')
    telemetry.record_log('ERROR', 'llm', 'token=must-not-leak')
    error = telemetry.record_error('llm', 'LLM временно недоступен')

    assert client.get('/api/v1/metrics/today').json()['counts']['posts_processed'] == 1
    logs = client.get('/api/v1/logs?level=ERROR&period=all').json()
    assert 'must-not-leak' not in str(logs)
    errors = client.get('/api/v1/errors').json()
    assert errors[0]['id'] == error['id']
    assert (
        client.post(
            f'/api/v1/errors/{error["id"]}/resolve',
            headers={'X-CSRF-Token': csrf},
            json={'confirmed': True},
        ).status_code
        == 200
    )

    prompt = client.get('/api/v1/prompt').json()
    assert prompt['is_custom'] is False
    assert (
        client.put(
            '/api/v1/prompt',
            headers={'X-CSRF-Token': csrf},
            json={
                'instructions': 'Определи только вакансии Go и не возвращай резюме кандидатов.'
            },
        ).status_code
        == 200
    )
    assert (
        client.put(
            '/api/v1/prompt',
            headers={'X-CSRF-Token': csrf},
            json={'instructions': 'api_key=do-not-accept-this-secret-value'},
        ).status_code
        == 422
    )
    assert (
        client.post(
            '/api/v1/prompt/reset',
            headers={'X-CSRF-Token': csrf},
            json={'confirmed': False},
        ).status_code
        == 400
    )

    added = client.post(
        '/api/v1/sources',
        headers={'X-CSRF-Token': csrf},
        json={'identifier': 'https://t.me/go_jobs'},
    ).json()
    token = added['item']['token']
    assert added['item']['identifier'] == '@go_jobs'
    assert (
        client.post(
            '/api/v1/sources',
            headers={'X-CSRF-Token': csrf},
            json={'identifier': '@go_jobs'},
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f'/api/v1/sources/{token}',
            headers={'X-CSRF-Token': csrf},
            json={'enabled': False},
        ).json()['item']['enabled']
        is False
    )
    assert (
        client.request(
            'DELETE',
            f'/api/v1/sources/{token}',
            headers={'X-CSRF-Token': csrf},
            json={'confirmed': True},
        ).status_code
        == 200
    )
