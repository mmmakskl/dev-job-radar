import asyncio

from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


class FakeBotApi:
    def __init__(self) -> None:
        self.messages: list[tuple[int | str, str, dict]] = []
        self.edits: list[tuple[int | str, int, str, dict]] = []
        self.answers: list[tuple[str, str]] = []

    async def get_updates(self, _offset, _timeout):
        return []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {'message_id': len(self.messages)}

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append((chat_id, message_id, text, kwargs))
        return {'message_id': message_id}

    async def answer_callback_query(self, callback_query_id, text):
        self.answers.append((callback_query_id, text))


def make_store(tmp_path, count: int = 1):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    items = []
    for number in range(1, count + 1):
        items.append(
            store.register_vacancy(
                vacancy_id=f'jobs_{number}',
                title=f'Go Developer {number}',
                company='Acme',
                summary=f'Вакансия {number}',
                post_link=f'https://t.me/jobs/{number}',
                apply_link=None,
                published_at=f'2026-08-{number:02d}T12:00:00+00:00',
            )
        )
    return store, items


def message_update(user_id: int, text: str) -> dict:
    return {
        'message': {
            'from': {'id': user_id},
            'chat': {'id': user_id, 'type': 'private'},
            'text': text,
        }
    }


def browser_callback(
    user_id: int, message_id: int, data: str, callback_id: str
) -> dict:
    return {
        'callback_query': {
            'id': callback_id,
            'from': {'id': user_id},
            'message': {'chat': {'id': user_id}, 'message_id': message_id},
            'data': data,
        }
    }


def browser_token(api: FakeBotApi) -> str:
    callback_data = api.messages[-1][2]['reply_markup']['inline_keyboard'][0][1][
        'callback_data'
    ]
    return callback_data.split(':')[1]


def test_browser_edits_one_card_through_first_middle_and_last(tmp_path) -> None:
    store, _items = make_store(tmp_path, count=3)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001})

    asyncio.run(bot.handle_update(message_update(1001, 'Новые')))
    token = browser_token(api)
    assert len(api.messages) == 1
    assert 'Новые · 1 из 3' in api.messages[0][1]

    asyncio.run(
        bot.handle_update(browser_callback(1001, 1, f'b:{token}:prev', 'first'))
    )
    assert api.edits == []
    assert api.answers[-1] == ('first', 'Это первая вакансия.')

    asyncio.run(
        bot.handle_update(browser_callback(1001, 1, f'b:{token}:next', 'middle'))
    )
    assert len(api.messages) == 1
    assert 'Новые · 2 из 3' in api.edits[-1][2]

    asyncio.run(bot.handle_update(browser_callback(1001, 1, f'b:{token}:next', 'last')))
    assert 'Новые · 3 из 3' in api.edits[-1][2]
    asyncio.run(
        bot.handle_update(browser_callback(1001, 1, f'b:{token}:next', 'after-last'))
    )
    assert api.answers[-1] == ('after-last', 'Это последняя вакансия.')


def test_browser_handles_empty_and_all_existing_buckets(tmp_path) -> None:
    store, items = make_store(tmp_path, count=3)
    store.set_status(1001, items[0].callback_key, 'saved')
    store.set_status(1001, items[1].callback_key, 'applied')
    store.set_status(1001, items[2].callback_key, 'hidden')
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001})

    for label, expected in (
        ('Новые', 'Новых вакансий пока нет.'),
        ('Сохранённые', 'Сохранённые · 1 из 1'),
        ('Мои отклики', 'Мои отклики · 1 из 1'),
        ('Скрытые', 'Скрытые · 1 из 1'),
    ):
        asyncio.run(bot.handle_update(message_update(1001, label)))
        assert expected in api.messages[-1][1]


def test_status_change_preserves_browser_position_without_new_message(tmp_path) -> None:
    store, items = make_store(tmp_path, count=3)
    for item in items:
        store.set_status(1001, item.callback_key, 'applied')
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001})

    asyncio.run(bot.handle_update(message_update(1001, 'Мои отклики')))
    token = browser_token(api)
    asyncio.run(bot.handle_update(browser_callback(1001, 1, f'b:{token}:next', 'next')))
    assert 'Мои отклики · 2 из 3' in api.edits[-1][2]
    message_count = len(api.messages)

    asyncio.run(bot.handle_update(browser_callback(1001, 1, f'b:{token}:p', 'reply')))
    assert len(api.messages) == message_count
    assert 'Мои отклики · 2 из 3' in api.edits[-1][2]
    assert '<b>Статус:</b> replied' in api.edits[-1][2]


def test_browser_callback_cannot_change_another_users_state(tmp_path) -> None:
    store, items = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001, 2002})

    asyncio.run(bot.handle_update(message_update(1001, 'Новые')))
    token = browser_token(api)
    asyncio.run(bot.handle_update(browser_callback(2002, 1, f'b:{token}:a', 'foreign')))

    assert store.list_for_user(1001, 'new')[0].status == 'new'
    assert store.list_for_user(2002, 'new')[0].status == 'new'
    assert api.answers[-1] == ('foreign', 'Эта карточка больше неактуальна.')


def test_access_is_limited_and_channel_report_stays_private(tmp_path) -> None:
    store, items = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1001})

    asyncio.run(bot.handle_update(message_update(2002, '/start')))
    assert api.messages[0][1] == 'Доступ к beta-боту ограничен.'

    asyncio.run(
        bot.handle_update(
            {
                'callback_query': {
                    'id': 'report-open',
                    'from': {'id': 1001},
                    'data': f'v:r:{items[0].callback_key}',
                }
            }
        )
    )
    assert api.answers[-1] == ('report-open', 'Выберите причину в личном чате.')
    assert api.messages[-1][0] == 1001
