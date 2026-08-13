import asyncio
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


def test_candidate_notifier_sends_compact_buttons_and_registers_vacancy(
    tmp_path,
) -> None:
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    api = FakeBotApi()
    notifier = CandidateVacancyNotifier(api, store, '@beta_vacancies')

    sent = asyncio.run(
        notifier(
            vacancy_id='jobs_42',
            post_link='https://t.me/jobs/42',
            channel_name='jobs',
            data=validate_analysis_result(valid_payload()),
            published_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
    )

    assert sent is True
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
