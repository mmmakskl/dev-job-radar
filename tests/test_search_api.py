from fastapi.testclient import TestClient

from tg_vacancy_bot.admin.api import create_app
from tg_vacancy_bot.search.settings import search_database_path
from tg_vacancy_bot.search.store import SearchStore, now
from tests.test_admin_api import _login

PREFIX = '/api/v1/search'


def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-password')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'session-secret-for-tests')
    monkeypatch.setenv('ADMIN_COOKIE_SECURE', 'false')
    monkeypatch.setenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'false')
    monkeypatch.setenv('THREADS_ENABLED', 'false')
    return TestClient(create_app(str(tmp_path)))


def test_new_search_requires_session_csrf_confirmation(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    assert api.get(PREFIX + '/capabilities').status_code == 401
    csrf = _login(api)
    request = dict(query='Golang hiring', sources=['telegram', 'threads'])
    assert api.post(PREFIX + '/runs', json=request).status_code == 403
    headers = {'X-CSRF-Token': csrf}
    assert api.post(PREFIX + '/runs', json=request, headers=headers).status_code == 400
    response = api.post(
        PREFIX + '/runs', json={**request, 'confirmed': True}, headers=headers
    )
    assert response.status_code == 202
    run = response.json()
    assert run['mode'] == 'preview' and run['sources'] == request['sources']
    assert not (tmp_path / 'premium_search.sqlite3').exists()
    assert api.get('/api/v1/premium-search/capabilities').status_code == 404
    states = api.get(PREFIX + '/capabilities').json()['sources']
    assert not next(s for s in states if s['key'] == 'threads')['enabled']
    assert (
        api.post(
            PREFIX + '/runs/' + run['id'] + '/cancel',
            json={'confirmed': True},
            headers=headers,
        ).json()['status']
        == 'cancelled'
    )


def test_api_reads_partial_results_without_raw_data(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    _login(api)
    store = SearchStore(search_database_path(str(tmp_path)))
    run = store.create_run(query='Golang', sources=['telegram', 'threads'])
    store.retain(
        run['id'],
        source='threads',
        external_id='1',
        text='Hiring Go engineer',
        timestamp=now(),
        permalink='https://www.threads.com/@user/post/abc',
        raw_data={'private_fixture': 'internal'},
        classification='accepted',
        confidence=0.97,
    )
    store.update_task(run['id'], 'telegram', status='completed')
    store.update_task(run['id'], 'threads', status='failed', reason='timeout')
    response = api.get(PREFIX + '/runs/' + run['id'] + '/results').json()
    assert response['total'] == 1
    assert response['items'][0]['confidence'] == 0.97
    assert 'raw_data' not in response['items'][0]
    assert (
        api.get(PREFIX + '/runs/' + run['id']).json()['status']
        == 'completed_with_errors'
    )
    assert api.get(PREFIX + '/runs/missing/results').status_code == 404


def test_api_validates_sources_and_publisher_without_remote_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setenv('CANDIDATE_BOT_ENABLED', 'false')
    api = client(tmp_path, monkeypatch)
    csrf = _login(api)
    headers = {'X-CSRF-Token': csrf}
    request = dict(query='Golang', sources=['threads'], confirmed=True)
    assert (
        api.post(
            PREFIX + '/runs', json={**request, 'sources': ['unknown']}, headers=headers
        ).status_code
        == 422
    )
    assert (
        api.post(
            PREFIX + '/runs', json={**request, 'mode': 'save_publish'}, headers=headers
        ).status_code
        == 422
    )
    assert (
        api.post(
            PREFIX + '/runs', json={**request, 'owner': 'candidate:5'}, headers=headers
        ).status_code
        == 422
    )
    store = SearchStore(search_database_path(str(tmp_path)))
    run = store.create_run(query='Golang', sources=['threads'])
    item = store.retain(
        run['id'], source='threads', external_id='1', text='Hiring Go', timestamp=now()
    )
    assert (
        api.post(
            PREFIX + '/results/' + item + '/actions',
            json={'action': 'save', 'confirmed': True},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        api.post(
            PREFIX + '/results/' + item + '/actions',
            json={'action': 'reject', 'confirmed': True},
            headers=headers,
        ).status_code
        == 202
    )


def test_history_parameters_can_be_submitted_for_refresh(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    headers = {'X-CSRF-Token': _login(api)}
    params = dict(
        query='Senior Go Developer',
        sources=['threads'],
        include_review=True,
        confirmed=True,
    )
    first = api.post(PREFIX + '/runs', json=params, headers=headers).json()
    assert first['include_review'] is True
    refresh = {
        k: first[k]
        for k in (
            'query',
            'sources',
            'track',
            'mode',
            'result_limit',
            'period_days',
            'include_review',
        )
    }
    second = api.post(
        PREFIX + '/runs', json={**refresh, 'confirmed': True}, headers=headers
    )
    assert second.status_code == 202
    assert second.json()['id'] != first['id']
