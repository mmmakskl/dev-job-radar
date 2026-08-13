import asyncio

from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


class FakeBotApi:
    def __init__(self) -> None:
        self.messages: list[tuple[int | str, str, dict]] = []
        self.answers: list[tuple[str, str]] = []

    async def get_updates(self, _offset, _timeout):
        return []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {}

    async def answer_callback_query(self, callback_query_id, text):
        self.answers.append((callback_query_id, text))


def make_store(tmp_path):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    item = store.register_vacancy(
        vacancy_id='jobs_42',
        title='Senior Go Developer',
        company='Acme',
        summary='Go и PostgreSQL',
        post_link='https://t.me/jobs/42',
        apply_link=None,
        published_at='2026-08-13T12:00:00+00:00',
    )
    return store, item


def test_allowed_user_can_list_and_change_personal_status(tmp_path) -> None:
    store, item = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001})

    asyncio.run(
        bot.handle_update(
            {
                'message': {
                    'from': {'id': 1001},
                    'chat': {'id': 1001, 'type': 'private'},
                    'text': 'Новые',
                }
            }
        )
    )
    assert 'Senior Go Developer' in api.messages[0][1]
    assert (
        api.messages[0][2]['reply_markup']['inline_keyboard'][0][1]['callback_data']
        == f'v:s:{item.callback_key}'
    )

    asyncio.run(
        bot.handle_update(
            {
                'callback_query': {
                    'id': 'callback-1',
                    'from': {'id': 1001},
                    'data': f'v:a:{item.callback_key}',
                }
            }
        )
    )
    assert store.list_for_user(1001, 'applications')[0].status == 'applied'
    assert api.answers == [('callback-1', 'Отклик отмечен')]


def test_access_is_limited_and_report_is_private_signal(tmp_path) -> None:
    store, item = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001})

    asyncio.run(
        bot.handle_update(
            {
                'message': {
                    'from': {'id': 2002},
                    'chat': {'id': 2002, 'type': 'private'},
                    'text': '/start',
                }
            }
        )
    )
    assert api.messages[0][1] == 'Доступ к beta-боту ограничен.'

    asyncio.run(
        bot.handle_update(
            {
                'callback_query': {
                    'id': 'report-open',
                    'from': {'id': 1001},
                    'data': f'v:r:{item.callback_key}',
                }
            }
        )
    )
    assert api.answers[-1] == ('report-open', 'Выберите причину в личном чате.')
    assert api.messages[-1][0] == 1001

    asyncio.run(
        bot.handle_update(
            {
                'callback_query': {
                    'id': 'report-reason',
                    'from': {'id': 1001},
                    'data': f'r:{item.callback_key}:dup',
                }
            }
        )
    )
    assert api.answers[-1] == ('report-reason', 'Спасибо, сигнал сохранён.')
