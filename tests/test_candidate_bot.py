import asyncio
import sqlite3

from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


class FakeBotApi:
    def __init__(self):
        self.messages = []
        self.answers = []
        self.edits = []

    async def get_updates(self, _offset, _timeout):
        return []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {'message_id': len(self.messages)}

    async def answer_callback_query(self, callback_query_id, text):
        self.answers.append((callback_query_id, text))

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append((chat_id, message_id, text, kwargs))
        return {'message_id': message_id}


def make_store(tmp_path, count=1):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    items = [
        store.register_vacancy(
            vacancy_id=f'jobs_{i}',
            title=f'Go {i}',
            company='Acme',
            summary=None,
            post_link=f'https://t.me/jobs/{i}',
            apply_link=None,
            published_at=f'2026-08-0{i}T12:00:00+00:00',
        )
        for i in range(1, count + 1)
    ]
    return store, items


def message(user_id, text):
    return {
        'message': {
            'from': {'id': user_id},
            'chat': {'id': user_id, 'type': 'private'},
            'text': text,
        }
    }


def callback(user_id, data, callback_id='cb'):
    return {
        'callback_query': {'id': callback_id, 'from': {'id': user_id}, 'data': data}
    }


def test_feed_saves_and_archive_is_private(tmp_path):
    store, items = make_store(tmp_path, 2)
    api, bot = FakeBotApi(), CandidateBot(FakeBotApi(), store, {1, 2})
    bot.api = api
    asyncio.run(bot.handle_update(message(1, 'Новые')))
    assert len(api.messages) == 1
    keyboard = api.messages[0][2]['reply_markup']['inline_keyboard']
    assert [button['text'] for button in keyboard[0]] == ['Сохранить']
    asyncio.run(bot.handle_update(callback(1, f'v:s:{items[0].callback_key}')))
    assert api.answers[-1] == ('cb', 'Сохранено.')
    assert [v.vacancy_id for v in store.list_for_user(1, 'saved')] == ['jobs_1']
    assert len(store.list_for_user(2, 'saved')) == 0
    assert [v.vacancy_id for v in store.list_for_user(1, 'new')] == ['jobs_2']


def test_feed_navigation_edits_one_private_card_and_checks_owner(tmp_path):
    store, _ = make_store(tmp_path, 3)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1, 2})
    asyncio.run(bot.handle_update(message(1, 'Новые')))
    assert len(api.messages) == 1
    assert 'Go 3' in api.messages[0][1]
    buttons = api.messages[0][2]['reply_markup']['inline_keyboard']
    next_data = next(
        button['callback_data']
        for row in buttons
        for button in row
        if button['text'] == 'Далее'
    )
    assert len(next_data.encode()) <= 64
    navigation = callback(1, next_data)
    navigation['callback_query']['message'] = {
        'message_id': 1,
        'chat': {'id': 1, 'type': 'private'},
    }
    asyncio.run(bot.handle_update(navigation))
    assert len(api.messages) == 1
    assert api.edits[-1][0:2] == (1, 1)
    assert 'Go 2' in api.edits[-1][2]

    forged = callback(2, next_data)
    forged['callback_query']['message'] = navigation['callback_query']['message']
    asyncio.run(bot.handle_update(forged))
    assert len(api.edits) == 1
    assert 'устарела' in api.answers[-1][1]


def test_legacy_callbacks_are_ignored_without_writes_and_search_removed(tmp_path):
    store, items = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    for data in (
        f'v:a:{items[0].callback_key}',
        f'v:r:{items[0].callback_key}',
        'q:any',
    ):
        asyncio.run(bot.handle_update(callback(1, data, data)))
    assert all('не поддерживается' in text for _, text in api.answers)
    assert store.list_for_user(1, 'saved') == []
    with sqlite3.connect(store.path) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert 'vacancy_reports' not in tables


def test_start_has_feed_archive_profiles_and_access_is_limited(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/start')))
    assert api.messages[-1][2]['reply_markup']['keyboard'] == [
        ['Новые', 'Сохранённые'],
        ['Профили'],
    ]
    asyncio.run(bot.handle_update(message(2, '/start')))
    assert api.messages[-1][1] == 'Доступ к beta-боту ограничен.'


def press(bot, api, label, user=1):
    rows = api.messages[-1][2]['reply_markup']['inline_keyboard']
    data = next(b['callback_data'] for row in rows for b in row if b['text'] == label)
    asyncio.run(bot.handle_update(callback(user, data)))
    return data


def complete_draft(bot, api, store):
    while store.get_profile_draft(1)['step'] != 'preview':
        press(bot, api, 'Пропустить')


def test_wizard_restart_back_validation_save_and_multiple_profiles(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1, 2})
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, 'Создать профиль')
    stale = press(bot, api, 'разработка')
    revision = store.get_profile_draft(1)['revision']
    asyncio.run(bot.handle_update(callback(1, stale)))
    assert store.get_profile_draft(1)['revision'] == revision
    assert 'устарела' in api.answers[-1][1]
    asyncio.run(bot.handle_update(callback(2, stale)))
    assert store.get_profile_draft(2) is None
    press(bot, api, 'бэкенд')
    press(bot, api, 'backend developer')
    asyncio.run(bot.handle_update(message(1, 'invalid-stack')))
    assert store.get_profile_draft(1)['step'] == 'stacks'
    press(bot, api, 'go')
    press(bot, api, 'Далее')
    press(bot, api, 'Назад')
    assert store.get_profile_draft(1)['values']['preferences']['stacks'] == ['go']
    press(bot, api, 'Далее')
    bot = CandidateBot(api, CandidateStore(store.path), {1, 2})
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, 'Продолжить черновик')
    complete_draft(bot, api, store)
    press(bot, api, 'Изменить имя')
    asyncio.run(bot.handle_update(message(1, 'Мой Go')))
    saved = press(bot, api, 'Сохранить профиль')
    asyncio.run(bot.handle_update(callback(1, saved)))
    profile = store.list_profiles(1)[0]
    assert profile.name == 'Мой Go'
    assert profile.preferences['stacks'] == ['go']
    assert profile.preferences['delivery_mode'] == 'manual'
    assert len(store.get_profile_versions(1, profile.profile_id)) == 1
    assert store.get_profile_draft(1) is None
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, 'Создать профиль')
    press(bot, api, 'тестирование')
    press(bot, api, 'ручное тестирование')
    press(bot, api, 'manual qa')
    complete_draft(bot, api, store)
    press(bot, api, 'Назад')
    press(bot, api, 'hourly')
    press(bot, api, 'Сохранить профиль')
    assert len(store.list_profiles(1)) == 2
    assert store.list_profiles(1)[1].preferences['delivery_mode'] == 'hourly'
    assert store.list_profiles(2) == []


def test_edit_cancel_toggle_templates_and_forged_callbacks(tmp_path):
    store, _ = make_store(tmp_path)
    profile = store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={'stacks': ['go']},
    )
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1, 2})
    asyncio.run(bot.handle_update(callback(2, f'p:toggle:{profile.profile_id}:1')))
    assert store.get_profile(1, profile.profile_id).is_active
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, '✓ Go')
    toggle = press(bot, api, 'Выключить')
    asyncio.run(bot.handle_update(callback(1, toggle)))
    assert not store.get_profile(1, profile.profile_id).is_active
    press(bot, api, 'Premium-шаблоны')
    rows = api.messages[-1][2]['reply_markup']['inline_keyboard']
    data = rows[-1][0]['callback_data']
    assert len(data.encode()) <= 64
    asyncio.run(bot.handle_update(callback(1, data)))
    chosen = store.get_profile(1, profile.profile_id)
    assert chosen.preferences['premium_template_id']
    assert any('Запрос:' in m[1] for m in api.messages)
    asyncio.run(bot.handle_update(callback(1, data)))
    assert store.get_profile(1, profile.profile_id).version == chosen.version
    press(bot, api, 'Редактировать')
    press(bot, api, 'тестирование')
    draft = store.get_profile_draft(1)
    assert 'role_id' not in draft['values']
    assert draft['values']['preferences']['stacks'] == []
    assert 'premium_template_id' not in draft['values']['preferences']
    for action in ['save', 'choose:-1', 'choose:999', 'choose:broken', 'next', 'name']:
        asyncio.run(
            bot.handle_update(
                callback(1, f'w:{draft["token"]}:{draft["revision"]}:{action}')
            )
        )
        assert store.get_profile_draft(1) == draft
    press(bot, api, 'Отмена')
    assert store.get_profile_draft(1) is None
    assert store.get_profile(1, profile.profile_id) == chosen
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, '○ Go')
    press(bot, api, 'Редактировать')
    press(bot, api, 'разработка')
    press(bot, api, 'бэкенд')
    press(bot, api, 'backend developer')
    press(bot, api, 'Пропустить')
    complete_draft(bot, api, store)
    press(bot, api, 'Сохранить профиль')
    updated = store.get_profile(1, profile.profile_id)
    assert updated.version == chosen.version + 1
    assert 'premium_template_id' not in updated.preferences


def test_all_wizard_fields_timezone_and_edit_backtracking(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, 'Создать профиль')
    press(bot, api, 'разработка')
    press(bot, api, 'бэкенд')
    press(bot, api, 'backend developer')
    for value in (
        'go, python',
        'senior',
        'remote',
        'Europe',
        'ru, en',
        'SQL, API',
        'Docker',
        'PHP',
    ):
        asyncio.run(bot.handle_update(message(1, value)))
    assert store.get_profile_draft(1)['step'] == 'timezone'
    draft = store.get_profile_draft(1)
    asyncio.run(bot.handle_update(message(1, 'Mars/Olympus')))
    assert store.get_profile_draft(1) == draft
    asyncio.run(bot.handle_update(message(1, 'Europe/Berlin')))
    press(bot, api, 'immediate')
    assert not store.list_profiles(1)
    press(bot, api, 'Сохранить профиль')
    profile = store.list_profiles(1)[0]
    assert profile.preferences == {
        'stacks': ['go', 'python'],
        'seniority': ['senior'],
        'formats': ['remote'],
        'geography': ['Europe'],
        'vacancy_languages': ['ru', 'en'],
        'required_skills': ['SQL', 'API'],
        'desired_skills': ['Docker'],
        'excluded_skills': ['PHP'],
        'timezone': 'Europe/Berlin',
        'delivery_mode': 'immediate',
    }
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, '✓ backend developer')
    press(bot, api, 'Редактировать')
    press(bot, api, 'разработка')
    press(bot, api, 'бэкенд')
    press(bot, api, 'Назад')
    press(bot, api, 'фронтенд')
    draft = store.get_profile_draft(1)
    assert 'role_id' not in draft['values']
    assert draft['values']['preferences']['stacks'] == []
    press(bot, api, 'frontend developer')
    complete_draft(bot, api, store)
    press(bot, api, 'Сохранить профиль')
    assert store.get_profile(1, profile.profile_id).role_id == 'frontend_developer'
    assert store.get_profile(1, profile.profile_id).version == 2
    asyncio.run(bot.handle_update(message(1, 'Новые')))
    assert 'Go 1' in api.messages[-1][1]
