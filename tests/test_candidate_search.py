"""Personal search routing, ownership and preview delivery regressions."""

import asyncio
from copy import deepcopy
from uuid import uuid4

from tg_vacancy_bot.search.bot import CandidateSearch
from tg_vacancy_bot.telegram.candidate_bot import CandidateBot
from tg_vacancy_bot.telegram.candidate_store import CandidateStore


class Api:
    def __init__(self):
        self.messages = []
        self.edits = []
        self.answers = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {'message_id': len(self.messages)}

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append((chat_id, message_id, text, kwargs))

    async def answer_callback_query(self, callback_id, text):
        self.answers.append((callback_id, text))


class Store:
    def __init__(self):
        self.runs = {}
        self.items = []
        self.cancelled = []

    def create_run(self, **values):
        run = {
            **values,
            'id': uuid4().hex,
            'status': 'queued',
            'source_states': {'telegram': {'status': 'queued'}},
        }
        self.runs[run['id']] = run
        return deepcopy(run)

    def save_ui(self, run_id, owner, chat_id, message_id, position=0):
        assert self.runs[run_id]['owner'] == owner
        self.runs[run_id].update(
            ui_chat_id=chat_id, ui_message_id=message_id, ui_position=position
        )

    def clear_ui(self, run_id, owner):
        assert self.runs[run_id]['owner'] == owner
        self.runs[run_id].update(ui_chat_id=None, ui_message_id=None)

    def get_run(self, run_id, owner=None):
        run = self.runs.get(run_id)
        return deepcopy(run) if run and run['owner'] == owner else None

    def list_ui_runs(self):
        return deepcopy(list(self.runs.values()))

    def list_results(self, run_id, *, owner, offset=0, limit=25, include_review=False):
        assert self.runs[run_id]['owner'] == owner
        assert not include_review
        return {'items': self.items[offset : offset + limit], 'total': len(self.items)}

    def cancel(self, run_id, owner=None):
        assert self.runs[run_id]['owner'] == owner
        self.cancelled.append(run_id)
        self.runs[run_id]['status'] = 'cancelled'
        return deepcopy(self.runs[run_id])


def setup_search():
    api, store = Api(), Store()
    search = CandidateSearch(
        api,
        store,
        [
            {'key': 'telegram', 'label': 'Telegram', 'enabled': True},
            {'key': 'threads', 'label': 'Threads', 'enabled': False},
        ],
    )
    return api, store, search


def callback(user=7, chat=7, message=1, kind='private'):
    return {
        'from': {'id': user},
        'message': {'chat': {'id': chat, 'type': kind}, 'message_id': message},
    }


def test_search_is_personal_and_only_uses_enabled_sources():
    api, store, search = setup_search()
    asyncio.run(search.handle_message(7, 7, '/search Go backend'))
    run = next(iter(store.runs.values()))
    assert run['query'] == 'Go backend'
    assert run['sources'] == ['telegram']
    assert run['owner'] == 'candidate:7'
    assert run['mode'] == 'preview'
    for row in api.messages[0][2]['reply_markup']['inline_keyboard']:
        for button in row:
            data = button.get('callback_data', '')
            assert 'Go' not in data
            assert len(data.encode()) <= 64


def test_prompt_and_rejected_cross_user_message_and_group_callbacks():
    api, store, search = setup_search()
    asyncio.run(search.handle_message(7, 7, 'Поиск'))
    assert not store.runs
    assert not asyncio.run(search.handle_message(8, 8, 'Go'))
    asyncio.run(search.handle_message(7, 7, 'Go backend'))
    run_id = next(iter(store.runs))
    data = f'q:{run_id}:cancel'
    for user, payload in [
        (8, callback(user=8)),
        (7, callback(chat=8)),
        (7, callback(message=99)),
        (7, callback(kind='group')),
    ]:
        asyncio.run(search.handle_callback(payload, user, 'cb', data))
    assert not store.cancelled
    assert not api.edits
    asyncio.run(search.handle_callback(callback(message=2), 7, 'cb', data))
    assert store.cancelled


def test_partial_results_navigation_and_restart_progress():
    api, store, search = setup_search()
    asyncio.run(search.handle_message(7, 7, '/search Go backend'))
    run = next(iter(store.runs.values()))
    run['status'] = 'completed_with_errors'
    run['source_states']['threads'] = {'status': 'timeout'}
    store.items = [
        {
            'source': 'telegram',
            'title': f'Go {number}',
            'summary': '<untrusted>',
            'classification': 'accepted',
            'permalink': 'https://t.me/jobs/1',
        }
        for number in range(2)
    ]
    # Restarted delivery has no in-memory UI state; the persisted run is sufficient.
    restored = CandidateSearch(api, store, search.sources)
    asyncio.run(restored.handle_callback(callback(), 7, 'cb', f'q:{run["id"]}:next'))
    assert '2 из 2' in api.edits[-1][2]
    assert 'частично' in api.edits[-1][2]
    assert '&lt;untrusted&gt;' in api.edits[-1][2]
    keyboard = str(api.edits[-1][3]['reply_markup'])
    assert 'Открыть оригинал' in keyboard
    assert 'Сохранить' not in keyboard
    assert 'Откликнулся' not in keyboard
    assert store.runs[run['id']]['ui_position'] == 1


def test_allowlist_private_scope_and_existing_keyboard(tmp_path):
    api, store, search = setup_search()
    bot = CandidateBot(api, CandidateStore(str(tmp_path / 'personal.db')), {7}, search)
    for user, kind in [(8, 'private'), (7, 'group')]:
        asyncio.run(
            bot.handle_update(
                {
                    'message': {
                        'from': {'id': user},
                        'chat': {'id': user, 'type': kind},
                        'text': '/search Go backend',
                    }
                }
            )
        )
    assert not store.runs
    asyncio.run(
        bot.handle_update(
            {
                'message': {
                    'from': {'id': 7},
                    'chat': {'id': 7, 'type': 'private'},
                    'text': '/start',
                }
            }
        )
    )
    assert ['Поиск'] in api.messages[-1][2]['reply_markup']['keyboard']


def test_background_progress_restores_private_run_and_stops():
    api, store, search = setup_search()
    asyncio.run(search.handle_message(7, 7, '/search Go backend'))
    next(iter(store.runs.values()))['status'] = 'completed'

    async def exercise():
        stop = asyncio.Event()
        original = api.edit_message_text

        async def edit(*args, **kwargs):
            await original(*args, **kwargs)
            stop.set()

        api.edit_message_text = edit
        restored = CandidateSearch(api, store, search.sources)
        await asyncio.wait_for(restored.run(stop), timeout=1)

    asyncio.run(exercise())
    assert api.edits[-1][0] == 7
    assert 'Поиск завершён' in api.edits[-1][2]


def test_real_store_restart_hides_review_and_rejects_other_owner(tmp_path):
    from tg_vacancy_bot.search.store import SearchStore

    api = Api()
    path = str(tmp_path / 'search.sqlite3')
    store = SearchStore(path)
    sources = [{'key': 'telegram', 'label': 'Telegram', 'enabled': True}]
    search = CandidateSearch(api, store, sources)
    asyncio.run(search.handle_message(7, 7, '/search Go backend'))
    run = store.list_ui_runs()[0]
    for classification in ('accepted', 'review', 'rejected'):
        store.retain(
            run['id'],
            source='telegram',
            external_id=classification,
            text=f'Go backend {classification}',
            title=classification,
            timestamp='2026-09-15T10:00:00+00:00',
            permalink=f'https://t.me/jobs/{classification}',
            classification=classification,
        )
    restored = CandidateSearch(api, SearchStore(path), sources)
    asyncio.run(restored.handle_callback(callback(), 7, 'cb', f'q:{run["id"]}:next'))
    assert '1 из 1' in api.edits[-1][2]
    assert '<b>accepted</b>' in api.edits[-1][2]
    assert 'review' not in api.edits[-1][2]
    asyncio.run(restored.handle_callback(callback(), 8, 'cb', f'q:{run["id"]}:cancel'))
    assert store.get_run(run['id'])['status'] == 'queued'
    asyncio.run(restored.handle_callback(callback(), 7, 'cb', f'q:{run["id"]}:cancel'))
    assert store.get_run(run['id'])['status'] == 'cancelled'


def test_refresh_creates_fresh_run_and_rebinds_same_message(tmp_path):
    from tg_vacancy_bot.search.store import SearchStore

    api = Api()
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    sources = [{'key': 'telegram', 'label': 'Telegram', 'enabled': True}]
    search = CandidateSearch(api, store, sources)
    asyncio.run(search.handle_message(7, 7, '/search Go backend'))
    old = store.list_ui_runs()[0]
    store.update_task(old['id'], 'telegram', status='completed')
    asyncio.run(
        search.handle_callback(callback(), 7, 'refresh', f'q:{old["id"]}:refresh')
    )
    new = store.list_ui_runs()[0]
    assert new['id'] != old['id']
    assert new['query'] == old['query']
    assert new['owner'] == old['owner']
    assert new['sources'] == old['sources']
    assert new['status'] == 'queued'
    assert new['mode'] == 'preview'
    assert new['ui_message_id'] == old['ui_message_id']
    assert store.get_run(old['id'])['ui_message_id'] is None
    assert len(api.messages) == 1
    assert new['id'] in str(api.edits[-1][3]['reply_markup'])
    asyncio.run(search.handle_callback(callback(), 7, 'stale', f'q:{old["id"]}:cancel'))
    assert store.get_run(old['id'])['status'] == 'completed'
    assert api.answers[-1][1] == 'Эта карточка недоступна.'


def test_refresh_active_run_preserves_current_binding(tmp_path):
    from tg_vacancy_bot.search.store import SearchStore

    api = Api()
    store = SearchStore(str(tmp_path / 'search.sqlite3'))
    search = CandidateSearch(
        api, store, [{'key': 'telegram', 'label': 'Telegram', 'enabled': True}]
    )
    asyncio.run(search.handle_message(7, 7, '/search Go backend'))
    run = store.list_ui_runs()[0]
    asyncio.run(
        search.handle_callback(callback(), 7, 'refresh', f'q:{run["id"]}:refresh')
    )
    assert len(store.list_runs()) == 1
    assert store.get_run(run['id'])['ui_message_id'] == 1
    assert 'Дождитесь' in api.answers[-1][1]


def test_existing_bucket_clears_pending_query(tmp_path):
    api, store, search = setup_search()
    bot = CandidateBot(api, CandidateStore(str(tmp_path / 'personal.db')), {7}, search)
    for text in ['Поиск', 'Новые', 'Go backend']:
        asyncio.run(
            bot.handle_update(
                {
                    'message': {
                        'from': {'id': 7},
                        'chat': {'id': 7, 'type': 'private'},
                        'text': text,
                    }
                }
            )
        )
    assert not store.runs
    assert any('Новых вакансий пока нет' in text for _, text, _ in api.messages)
