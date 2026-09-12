"""Premium API auth, CSRF, queueing and disabled-mode isolation."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from tests.test_admin_api import _client, _login
from tests.test_premium_search import channel, decision, message
from tg_vacancy_bot.premium_search.service import normalize_result
from tg_vacancy_bot.premium_search.store import ActiveRunError, PremiumSearchStore

PREFIX = '/api/v1/premium-search'


def test_disabled_routes_do_not_open_database(tmp_path, monkeypatch):
    monkeypatch.setenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'false')
    with patch.object(
        PremiumSearchStore,
        '__init__',
        side_effect=AssertionError('must not initialize'),
    ):
        client = _client(tmp_path, monkeypatch)
        _login(client)
        assert client.get(PREFIX + '/capabilities').status_code == 404
        assert (
            client.post(
                PREFIX + '/runs', json={'query': 'golang', 'confirmed': True}
            ).status_code
            == 404
        )
    assert not (tmp_path / 'premium_search.sqlite3').exists()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv('PREMIUM_GLOBAL_SEARCH_ENABLED', 'true')
    monkeypatch.setenv('CANDIDATE_BOT_ENABLED', 'false')
    return _client(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    'route', ['/capabilities', '/runs', '/runs/missing', '/runs/missing/results']
)
def test_gets_require_session(api_client, route):
    assert api_client.get(PREFIX + route).status_code == 401


@pytest.mark.parametrize(
    ('route', 'body'),
    [
        ('/runs', {'query': 'golang'}),
        ('/runs/missing/cancel', {}),
        ('/results/missing/actions', {'action': 'save'}),
    ],
)
def test_mutations_require_csrf_and_confirmation(api_client, route, body):
    assert api_client.post(PREFIX + route, json=body).status_code == 401
    csrf = _login(api_client)
    assert api_client.post(PREFIX + route, json=body).status_code == 403
    assert (
        api_client.post(
            PREFIX + route, json=body, headers={'X-CSRF-Token': csrf}
        ).status_code
        == 400
    )


def test_create_limits_conflict_and_no_publisher(api_client, tmp_path):
    csrf = _login(api_client)
    headers = {'X-CSRF-Token': csrf}
    assert api_client.get(PREFIX + '/capabilities').json()['max_llm_calls'] == 3000
    result = api_client.post(
        PREFIX + '/runs',
        json={'query': ' Go  jobs ', 'confirmed': True},
        headers=headers,
    )
    assert result.status_code == 202
    run_id = result.json()['search_run_id']
    conflict = api_client.post(
        PREFIX + '/runs',
        json={'query': 'go jobs', 'mode': 'save', 'confirmed': True},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()['detail']['existing_run_id'] == run_id
    for changes in [
        {'mode': 'save_publish'},
        {'result_limit': 101},
        {'period_days': 31},
        {'track': 'unknown'},
        {'confirmed': 'true'},
    ]:
        response = api_client.post(
            PREFIX + '/runs',
            json={'query': 'other jobs', 'confirmed': True, **changes},
            headers=headers,
        )
        assert response.status_code == 422
    store = PremiumSearchStore(str(tmp_path / 'premium_search.sqlite3'))
    rid = store.add_result(run_id, **normalize_result(message(), channel(), 7))
    store.update_result(
        rid, status='accepted', analysis_json=decision().as_json(), confidence=90
    )
    action = api_client.post(
        PREFIX + f'/results/{rid}/actions',
        json={'action': 'save', 'confirmed': True},
        headers=headers,
    )
    assert action.status_code == 202
    assert action.json()['item']['status'] == 'accepted'
    assert action.json()['item']['action_state'] == 'queued'
    assert 'raw_text' not in action.text and 'telegram_chat_id' not in action.text
    assert (
        api_client.post(
            PREFIX + f'/results/{rid}/actions',
            json={'action': 'publish', 'confirmed': True},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        api_client.post(
            PREFIX + f'/runs/{run_id}/cancel', json={'confirmed': True}, headers=headers
        ).json()['status']
        == 'cancelled'
    )


def test_add_source_only_queues(api_client, tmp_path):
    from tg_vacancy_bot.admin.settings import SettingsStore

    csrf = _login(api_client)
    store = PremiumSearchStore(str(tmp_path / 'premium_search.sqlite3'))
    run = store.create_run(query='Golang')
    rid = store.add_result(
        run['search_run_id'], **normalize_result(message(), channel(), 7)
    )
    settings = SettingsStore(str(tmp_path))
    before = settings.list_sources()
    result = api_client.post(
        PREFIX + f'/results/{rid}/actions',
        json={'action': 'add_public_source', 'confirmed': True},
        headers={'X-CSRF-Token': csrf},
    )
    assert result.status_code == 202 and result.json()['restart_required']
    assert settings.list_sources() == before
    assert store.get_result(rid)['action_state'] == 'queued'


def test_parallel_creates_have_one_winner(tmp_path):
    store = PremiumSearchStore(str(tmp_path / 'premium.sqlite3'))

    def create(_):
        try:
            return store.create_run(query='Go jobs')['search_run_id']
        except ActiveRunError as error:
            return error.run_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    assert len(store.list_runs()) == 1


def test_queue_action_claim_and_migration_idempotency(tmp_path):
    path = str(tmp_path / 'premium.sqlite3')
    store = PremiumSearchStore(path)
    run = store.create_run(query='Golang')
    result_id = store.add_result(
        run['search_run_id'], **normalize_result(message(), channel(), 7)
    )
    repeated = store.add_result(
        run['search_run_id'], **normalize_result(message(), channel(), 7)
    )
    assert result_id == repeated
    store.update_result(
        result_id, status='accepted', analysis_json=decision().as_json()
    )
    store.request_action(result_id, 'save')
    assert store.claim_action()['result_id'] == result_id
    assert store.claim_action() is None
    store = PremiumSearchStore(path)
    store.recover()
    assert store.claim_action()['result_id'] == result_id
