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
    }
    assert 'тексты сообщений' in metrics['description']


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
