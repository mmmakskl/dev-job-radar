import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore
from tg_vacancy_bot.telegram.bot_api import BotApiError


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
        self.messages[message_id - 1] = (chat_id, text, kwargs)
        return {'message_id': message_id}


def make_store(tmp_path, count=1):
    store = CandidateStore(str(tmp_path / 'candidate.sqlite3'))
    now = datetime.now(timezone.utc)
    items = [
        store.register_vacancy(
            vacancy_id=f'jobs_{i}',
            title=f'Go {i}',
            company='Acme',
            summary=None,
            post_link=f'https://t.me/jobs/{i}',
            apply_link=None,
            published_at=(now - timedelta(hours=i)).isoformat(),
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


def test_cancel_without_draft_replies_explicitly(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/cancel')))
    assert api.messages[-1][1] == 'Нет активной настройки профиля.'


def test_feed_saves_and_archive_is_private(tmp_path):
    store, items = make_store(tmp_path, 2)
    api, bot = FakeBotApi(), CandidateBot(FakeBotApi(), store, {1, 2})
    bot.api = api
    asyncio.run(bot.handle_update(message(1, 'Новые · 24 часа')))
    assert len(api.messages) == 1
    assert 'Персональная выдача пока недоступна' in api.messages[0][1]
    asyncio.run(bot.handle_update(callback(1, f'v:s:{items[0].callback_key}')))
    assert api.answers[-1] == ('cb', 'Сохранено.')
    assert [v.vacancy_id for v in store.list_for_user(1, 'saved')] == ['jobs_1']
    assert len(store.list_for_user(2, 'saved')) == 0
    assert [v.vacancy_id for v in store.list_for_user(1, 'new')] == ['jobs_2']


def test_feed_navigation_edits_one_private_card_and_checks_owner(tmp_path):
    store, _ = make_store(tmp_path, 3)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1, 2})
    asyncio.run(bot.handle_update(message(1, 'Новые · 24 часа')))
    assert len(api.messages) == 1
    assert 'Персональная выдача пока недоступна' in api.messages[0][1]
    next_data = 'b:new:1'
    navigation = callback(1, next_data)
    navigation['callback_query']['message'] = {
        'message_id': 1,
        'chat': {'id': 1, 'type': 'private'},
    }
    asyncio.run(bot.handle_update(navigation))
    assert len(api.edits) == 0
    assert 'устарела' in api.answers[-1][1]

    forged = callback(2, next_data)
    forged['callback_query']['message'] = navigation['callback_query']['message']
    asyncio.run(bot.handle_update(forged))
    assert len(api.edits) == 0
    assert 'устарела' in api.answers[-1][1]


def test_age_filter_buttons_persist_custom_window_and_undated_explanation(tmp_path):
    store, _ = make_store(tmp_path)
    store.register_vacancy(
        vacancy_id='no-date',
        title='Без даты',
        company=None,
        summary=None,
        post_link='https://t.me/jobs/no-date',
        apply_link=None,
        published_at=None,
    )
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, 'Ранее · за 7 дней')))
    rows = api.messages[-1][2]['reply_markup']['inline_keyboard']
    assert [row[0]['text'] for row in rows] == [
        '3 дня',
        '7 дней',
        '14 дней',
        '30 дней',
        'Ввести свой срок',
    ]
    press(bot, api, '14 дней')
    assert store.get_older_days(1) == 14
    asyncio.run(bot.handle_update(message(1, 'Ранее · за 14 дней')))
    press(bot, api, 'Ввести свой срок')
    asyncio.run(bot.handle_update(message(1, '15')))
    assert store.get_older_days(1) == 15
    asyncio.run(bot.handle_update(message(1, 'Без даты')))
    assert 'Персональная выдача пока недоступна' in api.messages[-1][1]


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
        ['Новые · 24 часа', 'Ранее · за 7 дней'],
        ['Сохранённые', 'Без даты'],
        ['Профили'],
    ]
    asyncio.run(bot.handle_update(message(2, '/start')))
    assert api.messages[-1][1] == 'Доступ к beta-боту ограничен.'


def press(bot, api, label, user=1):
    if label != '→':
        for _ in range(10):
            latest = next(
                index
                for index in range(len(api.messages) - 1, -1, -1)
                if api.messages[index][0] == user
                and 'reply_markup' in api.messages[index][2]
                and 'inline_keyboard' in api.messages[index][2]['reply_markup']
            )
            labels = {
                button['text']
                for row in api.messages[latest][2]['reply_markup']['inline_keyboard']
                for button in row
            }
            if label in labels or '→' not in labels:
                break
            press(bot, api, '→', user)
    message_index = next(
        index
        for index in range(len(api.messages) - 1, -1, -1)
        if api.messages[index][0] == user
        and 'reply_markup' in api.messages[index][2]
        and 'inline_keyboard' in api.messages[index][2]['reply_markup']
    )
    rows = api.messages[message_index][2]['reply_markup']['inline_keyboard']
    data = next(b['callback_data'] for row in rows for b in row if b['text'] == label)
    update = callback(user, data)
    update['callback_query']['message'] = {
        'message_id': message_index + 1,
        'chat': {'id': user, 'type': 'private'},
    }
    asyncio.run(bot.handle_update(update))
    return data


def complete_draft(bot, api, store):
    while store.get_profile_draft(1)['step'] != 'preview':
        draft = store.get_profile_draft(1)
        step = draft['step']
        if step == 'role_id':
            press(bot, api, bot._choices(draft)[0][1])
        elif step == 'seniority':
            press(bot, api, 'Все')
        elif step == 'required_skills':
            press(bot, api, 'Готово')
        else:
            press(bot, api, 'Пропустить')


def create_roleless_profile(bot, api, store, direction, technology):
    press(bot, api, 'Создать профиль')
    press(bot, api, direction)
    role = (
        'Бэкенд разработчик'
        if direction == 'Разработка'
        else bot._choices(store.get_profile_draft(1))[0][1]
    )
    press(bot, api, role)
    press(bot, api, 'Все')
    press(bot, api, technology)
    press(bot, api, 'Готово')
    press(bot, api, 'Пропустить')
    press(bot, api, 'Сохранить профиль')
    return store.list_profiles(1)[-1]


def test_wizard_restart_back_validation_save_and_multiple_profiles(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1, 2})
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, 'Создать профиль')
    stale = press(bot, api, 'Контент')
    revision = store.get_profile_draft(1)['revision']
    asyncio.run(bot.handle_update(callback(1, stale)))
    assert store.get_profile_draft(1)['revision'] == revision
    asyncio.run(bot.handle_update(callback(2, stale)))
    assert store.get_profile_draft(2) is None
    bot = CandidateBot(api, CandidateStore(store.path), {1, 2})
    press(bot, api, 'Технический писатель')
    complete_draft(bot, api, store)
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, 'Продолжить черновик')
    press(bot, api, 'Сохранить профиль')
    profile = store.list_profiles(1)[0]
    assert profile.role_id == 'technical_writer'
    assert profile.preferences['delivery_mode'] == 'manual'
    assert len(store.get_profile_versions(1, profile.profile_id)) == 1
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, 'Создать профиль')
    press(bot, api, 'Тестирование')
    press(bot, api, 'Инженер по тестированию (QA Engineer)')
    complete_draft(bot, api, store)
    press(bot, api, 'Сохранить профиль')
    assert len(store.list_profiles(1)) == 2
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
    press(bot, api, '● Go')
    toggle = press(bot, api, 'Снять активность')
    asyncio.run(bot.handle_update(callback(1, toggle)))
    assert not store.get_profile(1, profile.profile_id).is_active
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, 'Выбрать активным')
    press(bot, api, '● Go')
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
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, '● Go')
    press(bot, api, 'Редактировать')
    press(bot, api, 'Тестирование')
    draft = store.get_profile_draft(1)
    assert draft['values']['role_id'] == ''
    assert draft['values']['specialization_id'] == ''
    assert draft['values']['preferences']['stacks'] == []
    assert 'premium_template_id' not in draft['values']['preferences']
    for action in [
        'save',
        'choose:-1',
        'choose:999',
        'choose:broken',
        'skip',
        'advanced',
    ]:
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
    press(bot, api, '● Go')
    press(bot, api, 'Редактировать')
    legacy_draft = store.get_profile_draft(1)
    assert legacy_draft['values']['preferences']['stacks'] == ['go']
    assert legacy_draft['values']['preferences']['timezone'] == 'Europe/Moscow'
    press(bot, api, 'Отмена')
    assert store.get_profile(1, profile.profile_id) == chosen


def test_short_profile_wizard_and_advanced_settings_preserve_timezone(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, 'Создать профиль')
    press(bot, api, 'Разработка')
    press(bot, api, 'Бэкенд разработчик')
    press(bot, api, 'Senior')
    press(bot, api, 'Middle')
    press(bot, api, 'Готово')
    press(bot, api, 'Go')
    press(bot, api, 'Ввести через запятую')
    asyncio.run(bot.handle_update(message(1, 'gRPC, Docker')))
    press(bot, api, 'Ввести через запятую')
    asyncio.run(bot.handle_update(message(1, 'casino')))
    press(bot, api, 'Сохранить профиль')
    profile = store.list_profiles(1)[0]
    assert profile.role_id == 'backend_developer'
    assert profile.preferences['required_skills'] == ['go', 'gRPC', 'Docker']
    assert profile.preferences['excluded_skills'] == ['casino']
    assert profile.preferences['seniority'] == ['senior', 'middle']
    assert profile.preferences['timezone'] == 'Europe/Moscow'
    assert 'formats' not in profile.preferences or not profile.preferences['formats']


def test_edit_old_profile_keeps_legacy_matching_and_delivery_preferences(tmp_path):
    store, _ = make_store(tmp_path)
    old = store.create_profile(
        1,
        name='My backend',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        preferences={
            'stacks': ['go'],
            'timezone': 'Europe/Berlin',
            'formats': ['remote'],
            'geography': ['Россия'],
        },
    )
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, 'Профили')))
    press(bot, api, '● My backend')
    press(bot, api, 'Редактировать')
    press(bot, api, '✓ Разработка')
    press(bot, api, '✓ Бэкенд разработчик')
    complete_draft(bot, api, store)
    press(bot, api, 'Сохранить профиль')
    updated = store.get_profile(1, old.profile_id)
    assert updated.preferences['profile_contract'] == 'catalog-v3'
    assert updated.preferences['timezone'] == 'Europe/Berlin'
    assert updated.preferences['formats'] == ['remote']
    assert updated.preferences['geography'] == ['Россия']
    assert updated.version == old.version + 1


def test_new_profile_uses_catalog_technology_pages_and_edits_one_message(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, 'Создать профиль')
    wizard_message_count = len(api.messages)
    press(bot, api, 'Разработка')
    assert store.get_profile_draft(1)['step'] == 'role_id'
    press(bot, api, 'Бэкенд разработчик')
    press(bot, api, 'Все')
    press(bot, api, 'Go')
    assert store.get_profile_draft(1)['values']['preferences']['required_skills'] == [
        'go'
    ]
    press(bot, api, '→')
    assert store.get_profile_draft(1)['values']['preferences']['required_skills'] == [
        'go'
    ]
    press(bot, api, 'Готово')
    press(bot, api, 'Пропустить')
    press(bot, api, 'Сохранить профиль')
    assert len(api.messages) == wizard_message_count
    assert store.list_profiles(1)[0].role_id == 'backend_developer'


def test_grade_format_are_multi_select_and_all_means_no_filter(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, 'Создать профиль')
    press(bot, api, 'Дизайн')
    press(bot, api, 'Продуктовый дизайнер')
    press(bot, api, 'Middle')
    press(bot, api, 'Senior')
    assert store.get_profile_draft(1)['values']['preferences']['seniority'] == [
        'middle',
        'senior',
    ]
    press(bot, api, 'Все')
    assert store.get_profile_draft(1)['values']['preferences']['seniority'] == []
    assert store.get_profile_draft(1)['step'] == 'required_skills'


def test_vacancy_text_language_has_exactly_one_skip_button_and_is_not_programming_language(
    tmp_path,
):
    store, _ = make_store(tmp_path)
    profile = store.create_profile(
        1,
        name='Legacy backend',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
    )
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(callback(1, f'p:advanced:{profile.profile_id}:1')))
    draft = store.get_profile_draft(1)
    while draft['step'] != 'vacancy_languages':
        press(
            bot,
            api,
            'Готово' if draft['step'] == 'additional_languages' else 'Пропустить',
        )
        draft = store.get_profile_draft(1)
    rows = api.messages[-1][2]['reply_markup']['inline_keyboard']
    labels = [button['text'] for row in rows for button in row]
    assert labels.count('Пропустить') == 1
    assert 'язык объявления, а не язык программирования' in api.messages[-1][1].lower()


def test_every_main_menu_command_has_a_live_route(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    routes = {
        '/start': 'Выберите',
        '/help': 'Команды',
        '/new': 'Персональная выдача пока недоступна',
        '/older': 'Выберите срок',
        '/saved': 'Сохранённых',
        '/undated': 'Персональная выдача пока недоступна',
        '/profiles': 'Профили',
    }
    for command, expected in routes.items():
        asyncio.run(bot.handle_update(message(1, command)))
        assert expected.lower() in api.messages[-1][1].lower()


def test_ambiguous_legacy_active_profiles_require_explicit_selection(tmp_path):
    store, _ = make_store(tmp_path)
    first = store.create_profile(
        1,
        name='Go',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
    )
    second = store.create_profile(
        1,
        name='Python',
        direction_id='development',
        specialization_id='backend',
        role_id='backend_developer',
        is_active=False,
    )
    with store._connect() as connection:
        connection.execute(
            'UPDATE candidate_profiles SET is_active=1 WHERE profile_id=?',
            (second.profile_id,),
        )
    api = FakeBotApi()
    bot = CandidateBot(
        api,
        store,
        {1},
        registry=object(),
    )
    asyncio.run(bot.handle_update(message(1, 'Для меня')))
    assert 'раньше было активно несколько' in api.messages[-2][1]
    assert store.get_active_profile_state(1) == 'ambiguous'
    press(bot, api, 'Выбрать активным')
    assert store.get_active_profile_state(1) == 'single'
    assert store.get_active_profile(1).profile_id == first.profile_id


def test_wizard_replaces_uneditable_message_and_persists_new_message_id(tmp_path):
    store, _ = make_store(tmp_path)
    api = FakeBotApi()
    bot = CandidateBot(api, store, {1})
    asyncio.run(bot.handle_update(message(1, '/profiles')))
    press(bot, api, 'Создать профиль')
    old_id = store.get_profile_draft(1)['message_id']
    api.edit_message_text = AsyncMock(side_effect=BotApiError('message is too old'))
    press(bot, api, 'Разработка')
    draft = store.get_profile_draft(1)
    assert len(api.messages) == 3
    assert draft['message_id'] == 3 and draft['message_id'] != old_id
    assert 'Роль' in api.messages[-1][1]
