import asyncio
import sqlite3
from datetime import datetime, timezone

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.telegram.candidate_notifier import CandidateVacancyNotifier
from tg_vacancy_bot.telegram.candidate_store import CandidateStore
from tests.test_llm_schemas import valid_payload


class FakeBotApi:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {}


class FailingBotApi:
    async def send_message(self, *args, **kwargs):
        raise RuntimeError('temporary Bot API failure')


def test_candidate_notifier_sends_compact_buttons_and_registers_vacancy(
    tmp_path,
) -> None:
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    api = FakeBotApi()
    notifier = CandidateVacancyNotifier(api, store, '@beta_vacancies')

    kwargs = {
        'vacancy_id': 'jobs_42',
        'post_link': 'https://t.me/jobs/42',
        'channel_name': 'jobs',
        'data': validate_analysis_result(valid_payload()),
        'published_at': datetime(2026, 8, 13, tzinfo=timezone.utc),
    }
    sent = asyncio.run(notifier(**kwargs))
    repeated = asyncio.run(notifier(**kwargs))

    assert sent is True
    assert repeated is True
    assert len(api.calls) == 1
    args, kwargs = api.calls[0]
    assert args[0] == '@beta_vacancies'
    buttons = kwargs['reply_markup']['inline_keyboard']
    assert buttons[0][0] == {'text': 'Открыть', 'url': 'https://t.me/jobs/42'}
    assert [button['text'] for row in buttons for button in row] == [
        'Открыть',
        'Сохранить',
        'Откликнулся',
        'Не подходит',
        'Пожаловаться',
    ]
    assert len(buttons[0][1]['callback_data']) <= 64
    assert store.list_for_user(1001, 'new')[0].vacancy_id == 'jobs_42'


def test_candidate_notifier_releases_failed_delivery_for_retry(tmp_path) -> None:
    database = tmp_path / 'candidate.sqlite3'
    store = CandidateStore(str(database))
    notifier = CandidateVacancyNotifier(FailingBotApi(), store, '@beta_vacancies')

    sent = asyncio.run(
        notifier(
            vacancy_id='jobs_43',
            post_link='https://t.me/jobs/43',
            channel_name='jobs',
            data=validate_analysis_result(valid_payload()),
            published_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
    )

    with sqlite3.connect(database) as connection:
        state = connection.execute(
            'SELECT delivery_state FROM vacancies WHERE vacancy_id = ?',
            ('jobs_43',),
        ).fetchone()[0]

    assert sent is False
    assert state == 'pending'
