import sqlite3

from tg_vacancy_bot.telegram.candidate_store import CandidateStore


def vacancy(store: CandidateStore, vacancy_id: str = 'jobs_42'):
    return store.register_vacancy(
        vacancy_id=vacancy_id,
        title='Senior Go Developer',
        company='Acme',
        summary='Go и PostgreSQL',
        post_link='https://t.me/jobs/42',
        apply_link='https://example.com/apply',
        published_at='2026-08-13T12:00:00+00:00',
    )


def test_store_migrates_idempotently_and_keeps_actions_personal(tmp_path) -> None:
    path = tmp_path / 'candidate.sqlite3'
    first = CandidateStore(str(path))
    item = vacancy(first)
    second = CandidateStore(str(path))

    assert second.set_status(1001, item.callback_key, 'saved') is not None
    assert second.set_status(2002, item.callback_key, 'hidden') is not None
    assert [item.status for item in second.list_for_user(1001, 'saved')] == ['saved']
    assert [item.status for item in second.list_for_user(2002, 'hidden')] == ['hidden']
    assert second.list_for_user(1001, 'hidden') == []

    with sqlite3.connect(path) as connection:
        action = connection.execute(
            '''
            SELECT telegram_user_id, status, created_at, updated_at, personal_note
            FROM user_vacancy_actions ORDER BY telegram_user_id
            '''
        ).fetchall()
    assert action[0][0:2] == (1001, 'saved')
    assert action[0][2] and action[0][3]
    assert action[0][4] is None


def test_store_lists_unclaimed_as_new_and_records_neutral_reports(tmp_path) -> None:
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    item = vacancy(store)

    assert [entry.vacancy_id for entry in store.list_for_user(1001, 'new')] == [
        'jobs_42'
    ]
    assert store.add_report(1001, item.callback_key, 'duplicate') is True

    with sqlite3.connect(store.path) as connection:
        report = connection.execute(
            'SELECT telegram_user_id, vacancy_id, reason FROM vacancy_reports'
        ).fetchone()
    assert report == (1001, 'jobs_42', 'duplicate')
