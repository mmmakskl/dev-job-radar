"""Offline durability, isolation and deduplication checks for unified searches."""

from datetime import datetime, timedelta, timezone

import pytest

from tg_vacancy_bot.search.store import SearchStore


@pytest.fixture
def store(tmp_path):
    return SearchStore(str(tmp_path / 'search.sqlite3'))


def new_run(store, **kwargs):
    return store.create_run(
        query='Go backend', sources=['telegram', 'threads'], **kwargs
    )


def retain(store, run, external_id='post-1', **kwargs):
    values = {
        'source': 'threads',
        'external_id': external_id,
        'text': f'Go backend vacancy {external_id}',
        'timestamp': '2026-09-15T10:00:00+00:00',
        'permalink': f'https://www.threads.net/@jobs/post/{external_id}',
        'classification': 'accepted',
    }
    values.update(kwargs)
    return store.retain(run['id'], **values)


def test_same_post_merges_query_variants_but_preserves_run_attribution(store):
    first, second = new_run(store), new_run(store)
    item = retain(store, first, queries=['Go backend'], score=2)
    assert retain(store, first, queries=['golang', 'Go backend'], score=4) == item
    assert retain(store, second, queries=['Go remote']) == item
    first_page = store.list_results(first['id'])
    second_page = store.list_results(second['id'])
    assert first_page['total'] == second_page['total'] == 1
    assert first_page['items'][0]['found_queries'] == ['Go backend', 'golang']
    assert second_page['items'][0]['found_queries'] == ['Go remote']
    assert first_page['items'][0]['id'] == second_page['items'][0]['id'] == item


def test_permalink_deduplicates_changed_external_identifier(store):
    run = new_run(store)
    item = retain(store, run, queries=['golang'])
    repeated = retain(
        store,
        run,
        external_id='replacement-id',
        permalink='https://www.threads.net/@jobs/post/post-1',
        queries=['Go backend'],
    )
    assert repeated == item
    assert store.list_results(run['id'])['total'] == 1


@pytest.mark.parametrize('shared_key', ['permalink', 'vacancy_id', 'text'])
def test_local_and_premium_results_collapse_shared_identity(store, shared_key):
    run = new_run(store)
    shared = {
        'permalink': 'https://t.me/jobs/123',
        'vacancy_id': 'jobs_123',
        'text': 'Go developer for the same hiring team',
    }[shared_key]
    retain(store, run, 'local', source='telegram', **{shared_key: shared})
    retain(store, run, 'global', source='premium', **{shared_key: shared})
    assert store.list_results(run['id'])['total'] == 1


def test_results_filter_review_rejected_duplicates_and_private_raw_fields(store):
    run = new_run(store)
    for state in ['accepted', 'review', 'rejected']:
        retain(store, run, state, classification=state, raw_data={'secret': 'private'})
    retain(store, run, 'duplicate', publication_status='duplicate')
    page = store.list_results(run['id'])
    assert page['total'] == 1
    assert page['items'][0]['classification'] == 'accepted'
    assert store.list_results(run['id'], include_review=True)['total'] == 2
    assert not {'raw_data', 'analysis', 'text_hash', 'reason'} & page['items'][0].keys()


def test_candidate_run_ownership_active_limit_and_cancellation(store):
    run = new_run(store, owner='candidate:7')
    retain(store, run)
    for method in (store.get_run, store.cancel):
        assert method(run['id'], owner='candidate:8') is None
    assert store.list_results(run['id'], owner='candidate:8')['total'] == 0
    store.save_ui(run['id'], 'candidate:8', 8, 100)
    assert store.get_run(run['id'])['ui_chat_id'] is None
    with pytest.raises(ValueError):
        new_run(store, owner='candidate:7')
    other = new_run(store, owner='candidate:8')
    assert other['id'] != run['id']
    store.claim_task('threads')
    cancelled = store.cancel(run['id'], owner='candidate:7')
    assert cancelled['status'] == 'cancelled'
    assert {task['status'] for task in cancelled['source_states'].values()} == {
        'cancelled'
    }
    # An in-flight source completing late must not resurrect a cancelled search.
    store.update_task(run['id'], 'threads', status='completed')
    assert store.get_run(run['id'])['status'] == 'cancelled'
    assert new_run(store, owner='candidate:7')['id'] != run['id']


@pytest.mark.parametrize('mode', ['save', 'save_publish'])
def test_candidate_cannot_create_mutating_search(store, mode):
    with pytest.raises(ValueError):
        new_run(store, owner='candidate:7', mode=mode)


def test_partial_source_failure_preserves_successful_cards(store):
    run = new_run(store)
    store.claim_task('telegram')
    item_id = retain(store, run, source='telegram')
    store.update_task(run['id'], 'telegram', status='completed')
    assert store.get_run(run['id'])['status'] == 'running'
    store.claim_task('threads')
    store.update_task(run['id'], 'threads', status='failed', reason='timeout')
    current = store.get_run(run['id'])
    assert current['status'] == 'completed_with_errors'
    assert current['source_states']['telegram']['status'] == 'completed'
    assert current['source_states']['threads']['reason'] == 'timeout'
    assert store.list_results(run['id'])['items'][0]['id'] == item_id


def test_restart_recovers_work_but_never_repeats_uncertain_publication(store):
    run = store.create_run(query='Go backend', sources=['threads', 'premium'])
    item = retain(store, run)
    store.claim_task('threads')
    store.claim_task('premium')
    store.update_task(run['id'], 'premium', status='running', child_id='legacy-job')
    store.request_action(item, 'publish')
    assert store.claim_action()['id'] == item
    restarted = SearchStore(store.path)
    restarted.recover()
    current = restarted.get_run(run['id'])
    assert current['source_states']['threads']['status'] == 'queued'
    assert current['source_states']['premium']['status'] == 'running'
    assert restarted.claim_task('threads')['run_id'] == run['id']
    assert restarted.claim_task('premium') is None
    assert restarted.claim_action() is None
    assert restarted.get_item(item)['action_error'] == 'delivery_uncertain'
    assert not restarted.get_item(item)['can_persist']
    with pytest.raises(ValueError):
        restarted.request_action(item, 'publish')


def test_daily_request_budget_is_durable_and_expires(store):
    assert store.reserve_request(2) is None
    assert store.reserve_request(2) is None
    restarted = SearchStore(store.path)
    retry_at = restarted.reserve_request(2)
    assert datetime.fromisoformat(retry_at) > datetime.now(timezone.utc)
    assert restarted.reserve_request(2) == retry_at
    expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with restarted.connect() as connection:
        connection.execute('UPDATE search_requests SET at=?', (expired,))
    assert restarted.reserve_request(2) is None


def test_admin_decisions_survive_new_search_results(store):
    first, second = new_run(store), new_run(store)
    item = retain(store, first)
    store.update_item(
        item,
        classification='rejected',
        publication_status='saved',
        reason='administrator_rejected',
    )
    retain(store, second, classification='accepted', publication_status='preview')
    current = store.get_item(item)
    assert current['classification'] == 'rejected'
    assert current['publication_status'] == 'saved'


def test_ui_and_scheduler_state_survive_restart(store):
    run = new_run(store, owner='candidate:7')
    store.save_ui(run['id'], 'candidate:7', 7, 42, position=3)
    store.set_state('threads_next_collection', '2026-09-15T16:00:00+00:00')
    restarted = SearchStore(store.path)
    restored = restarted.list_ui_runs()[0]
    assert (
        restored['ui_chat_id'],
        restored['ui_message_id'],
        restored['ui_position'],
    ) == (
        7,
        42,
        3,
    )
    assert restarted.state('threads_next_collection') == '2026-09-15T16:00:00+00:00'


def test_contact_edit_invalidates_extraction_even_when_dedupe_hash_unchanged(store):
    run = new_run(store)
    item = retain(store, run, text='Hiring Go engineer apply https://t.me/old')
    store.update_item(
        item, analysis_json='{"apply_link":"https://t.me/old"}', title='Go engineer'
    )
    retain(store, run, text='Hiring Go engineer apply https://t.me/new')
    assert store.get_item(item)['analysis'] is None
    assert store.get_item(item)['title'] is None
