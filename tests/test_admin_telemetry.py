import json
from datetime import datetime, timedelta, timezone

from tg_vacancy_bot.admin.telemetry import TelemetryStore, sanitize_text


def test_today_metrics_are_persistent_and_aggregate_only(tmp_path) -> None:
    store = TelemetryStore(str(tmp_path))
    for event in (
        'post_processed',
        'vacancy_saved',
        'skipped_duplicate',
        'processing_error',
    ):
        store.record_metric(event, 'pipeline')

    metrics = store.today_metrics('Europe/Moscow')

    assert metrics['counts'] == {
        'posts_processed': 1,
        'vacancies_added': 1,
        'skipped': 1,
        'errors': 1,
        'exact_duplicates': 0,
        'grouped_reposts': 0,
        'group_candidates_separate': 0,
        'manual_ungroups': 0,
    }
    assert 'тексты сообщений' in metrics['description']


def test_metrics_preserve_safe_skip_and_error_reasons(tmp_path) -> None:
    store = TelemetryStore(str(tmp_path))
    store.record_metric('skipped_invalid', reason='empty_text')
    store.record_metric('skipped_duplicate', reason='duplicate_link_or_id')
    store.record_metric('processing_error', 'llm', 'llm_error')

    assert store.today_metrics()['reasons'] == {
        'empty_text': 1,
        'duplicate_link_or_id': 1,
        'llm_error': 1,
    }


def test_group_metrics_are_counted_without_post_content(tmp_path) -> None:
    store = TelemetryStore(str(tmp_path))
    for event in (
        'exact_duplicate',
        'grouped_repost',
        'group_candidate_separate',
        'manual_ungroup',
    ):
        store.record_metric(event)

    assert store.today_metrics()['counts'] == {
        'posts_processed': 0,
        'vacancies_added': 0,
        'skipped': 0,
        'errors': 0,
        'exact_duplicates': 1,
        'grouped_reposts': 1,
        'group_candidates_separate': 1,
        'manual_ungroups': 1,
    }


def test_errors_merge_and_explicit_resolution(tmp_path) -> None:
    store = TelemetryStore(str(tmp_path))
    first = store.record_error('llm', 'Внешний API недоступен', 'token=do-not-show')
    repeated = store.record_error('llm', 'Внешний API недоступен')

    assert repeated['status'] == 'repeating'
    assert repeated['count'] == 2
    assert 'do-not-show' not in str(store.attention_errors())
    assert store.resolve_error(first['id'])['status'] == 'resolved'
    assert store.attention_errors() == []


def test_logs_are_sanitized_filtered_and_limited(tmp_path) -> None:
    store = TelemetryStore(str(tmp_path))
    store.record_log('ERROR', 'llm', 'Authorization: Bearer top-secret-token')
    store.record_log('INFO', 'telegram', 'Подключение успешно')

    page = store.read_logs(level='ERROR', period='all', limit=1)

    assert page['total'] == 1
    assert page['items'][0]['component'] == 'llm'
    assert 'top-secret-token' not in page['items'][0]['message']
    assert sanitize_text('password=hunter2') == 'password=[redacted]'


def test_cleanup_prunes_only_expired_observability_data(tmp_path) -> None:
    store = TelemetryStore(str(tmp_path))
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    store.directory.mkdir(parents=True)
    for path in (store.logs_path, store.operations_path, store.metrics_path):
        path.write_text(
            '\n'.join(json.dumps({'at': old, 'event': 'old'}) for _ in range(1))
            + '\n'
            + json.dumps({'at': recent, 'event': 'recent'})
            + '\n',
            encoding='utf-8',
        )
    store.errors_path.write_text(
        json.dumps(
            [
                {'last_seen_at': old},
                {'last_seen_at': recent},
            ]
        ),
        encoding='utf-8',
    )
    protected_state = tmp_path / 'state.jsonl'
    protected_state.write_text('must stay\n', encoding='utf-8')
    protected_settings = tmp_path / 'admin' / 'settings.json'
    protected_settings.write_text('{}', encoding='utf-8')

    removed = store.cleanup(
        logs_days=1, errors_days=1, operations_days=1, metrics_days=1
    )

    assert removed == {'logs': 1, 'operations': 1, 'metrics': 1, 'errors': 1}
    assert protected_state.read_text(encoding='utf-8') == 'must stay\n'
    assert protected_settings.read_text(encoding='utf-8') == '{}'
