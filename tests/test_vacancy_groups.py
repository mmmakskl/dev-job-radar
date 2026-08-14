import sqlite3
from datetime import datetime, timedelta, timezone

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore
from tests.test_llm_schemas import valid_payload


def data(**overrides):
    return validate_analysis_result(valid_payload(**overrides))


def register(
    store: VacancyGroupStore,
    vacancy_id: str,
    *,
    hours: int = 0,
    payload: dict | None = None,
):
    return store.register_publication(
        vacancy_id=vacancy_id,
        post_link=f'https://t.me/{vacancy_id}/1',
        channel_name=vacancy_id,
        data=data(**(payload or {})),
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=hours),
        text_hash=f'hash-{vacancy_id}',
    )


def test_same_vacancy_in_different_channels_groups_by_normalized_apply_url(
    tmp_path,
) -> None:
    store = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), window_days=14)
    first = register(store, 'jobs_one')
    repost = register(
        store,
        'jobs_two',
        hours=5,
        payload={'apply_link': 'https://EXAMPLE.com/apply/?utm_source=telegram'},
    )

    assert first.is_canonical is True
    assert repost.is_canonical is False
    assert repost.group_id == first.group_id
    assert repost.merge_reason == 'same_application_url'
    assert len(store.get_group(first.group_id)['publications']) == 2


def test_similar_but_different_roles_at_same_company_stay_separate(tmp_path) -> None:
    store = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), window_days=14)
    first = register(store, 'jobs_one')
    other = register(
        store,
        'jobs_two',
        payload={
            'title': 'Go Engineering Manager',
            'contact': None,
            'apply_link': None,
        },
    )

    assert other.is_canonical is True
    assert other.group_id != first.group_id
    assert other.left_separate_candidate is True


def test_same_stack_in_different_companies_never_groups(tmp_path) -> None:
    store = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), window_days=14)
    first = register(store, 'jobs_one')
    other = register(
        store,
        'jobs_two',
        payload={
            'company': 'Other Corp',
            'contact': None,
            'apply_link': None,
            'required_stack': ['Go', 'PostgreSQL'],
        },
    )

    assert other.is_canonical is True
    assert other.group_id != first.group_id


def test_missing_contact_and_apply_url_do_not_group_by_similar_text(tmp_path) -> None:
    store = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), window_days=14)
    first = register(
        store,
        'jobs_one',
        payload={'company': None, 'contact': None, 'apply_link': None},
    )
    other = register(
        store,
        'jobs_two',
        payload={'company': None, 'contact': None, 'apply_link': None},
    )

    assert other.is_canonical is True
    assert other.group_id != first.group_id


def test_manual_unlink_creates_blocked_pair_and_preserves_group_history(
    tmp_path,
) -> None:
    store = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), window_days=14)
    first = register(store, 'jobs_one')
    register(store, 'jobs_two', hours=1)

    assert store.unlink_publication(first.group_id, 'jobs_two') is True
    assert (
        store.get_group(first.group_id)['publications'][0]['vacancy_id'] == 'jobs_one'
    )
    split_id = next(
        item['group_id']
        for item in store.list_groups()
        if item['canonical_vacancy_id'] == 'jobs_two'
    )
    split = store.get_group(split_id)
    assert split is not None
    assert split['canonical_vacancy_id'] == 'jobs_two'

    with sqlite3.connect(store.path) as connection:
        blocked = connection.execute(
            'SELECT vacancy_id_a, vacancy_id_b FROM blocked_group_pairs'
        ).fetchall()
    assert blocked == [('jobs_one', 'jobs_two')]
    assert store.unlink_publication(first.group_id, 'jobs_two') is False

    # Simulate re-evaluating the exact old pair after its split record was
    # removed by a future explicit admin workflow. The durable block remains.
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            'DELETE FROM group_publications WHERE vacancy_id = ?', ('jobs_two',)
        )
        connection.execute(
            'DELETE FROM vacancy_groups WHERE canonical_vacancy_id = ?', ('jobs_two',)
        )
    retried = register(store, 'jobs_two', hours=2)
    assert retried.is_canonical is True
    assert retried.group_id != first.group_id
    assert retried.left_separate_candidate is True


def test_group_window_prevents_late_repost_from_merging(tmp_path) -> None:
    store = VacancyGroupStore(str(tmp_path / 'groups.sqlite3'), window_days=1)
    first = register(store, 'jobs_one')
    other = register(store, 'jobs_two', hours=49)

    assert other.is_canonical is True
    assert other.group_id != first.group_id
