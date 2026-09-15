import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_vacancy_bot.llm.schemas import validate_analysis_result
from tg_vacancy_bot.pipeline.dedupe_state import JsonlDedupeState
from tg_vacancy_bot.pipeline.processor import VacancyProcessor
from tg_vacancy_bot.premium_search.store import PremiumSearchStore
from tg_vacancy_bot.search.service import SearchService
from tg_vacancy_bot.search.settings import SearchSettings
from tg_vacancy_bot.search.store import SearchStore
from tg_vacancy_bot.telegram.candidate_store import CandidateStore
from tg_vacancy_bot.threads.rules import Classification
from tg_vacancy_bot.threads.source import ThreadsError, ThreadsPage, ThreadsPost
from tests.test_llm_schemas import valid_payload


def post(external_id='123', text='Ищем Senior Golang разработчика в remote команду'):
    return ThreadsPost(
        external_id,
        text,
        'recruiter',
        f'https://www.threads.com/@recruiter/post/{external_id}',
        datetime.now(timezone.utc) - timedelta(minutes=1),
        {'id': external_id, 'text': text},
    )


def setup(tmp_path, *, source=None, **kwargs):
    store = SearchStore(str(tmp_path / 'admin.sqlite3'))
    settings = SearchSettings(
        threads_enabled=True, token='test-secret', interval_hours=0, variants_limit=2
    )
    processor = kwargs.pop('processor', SimpleNamespace(notify_vacancy=None))
    service = SearchService(
        store,
        processor,
        candidate_path=str(tmp_path / 'candidate.sqlite3'),
        settings=settings,
        source=source,
        **kwargs,
    )
    return store, service


def test_threads_deduplicates_variants_pages_and_runs(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(return_value=ThreadsPage([post()], after='cursor'))
        )
        store, service = setup(tmp_path, source=source)
        service.classifier = AsyncMock(
            side_effect=AssertionError('clear job must not use LLM')
        )
        for _ in range(2):
            run = store.create_run(query='Senior Go Developer', sources=['threads'])
            await service._threads(run['id'])
            items = store.list_results(run['id'])['items']
            assert len(items) == 1
            assert len(items[0]['found_queries']) == 2
        with store.connect() as c:
            assert c.execute('SELECT COUNT(*) FROM search_items').fetchone()[0] == 1
        assert (
            store.get_run(run['id'])['source_states']['threads']['reason']
            == 'invalid_response_items'
        )

    asyncio.run(scenario())


def test_empty_threads_completes_without_classifier(tmp_path):
    async def scenario():
        source = SimpleNamespace(search_page=AsyncMock(return_value=ThreadsPage([])))
        store, service = setup(tmp_path, source=source, classifier=AsyncMock())
        run = store.create_run(query='Golang developer', sources=['threads'])
        await service._threads(run['id'])
        assert store.get_run(run['id'])['status'] == 'completed'
        service.classifier.assert_not_awaited()

    asyncio.run(scenario())


def test_failure_does_not_drop_telegram_results(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(side_effect=ThreadsError('timeout'))
        )
        store, service = setup(tmp_path, source=source)
        candidate = CandidateStore(service.candidate_path)
        candidate.register_vacancy(
            vacancy_id='jobs_4',
            title='Senior Go Developer',
            company='Team',
            summary='Remote Go developer',
            post_link='https://t.me/jobs/4',
            apply_link=None,
            published_at=datetime.now(timezone.utc).isoformat(),
        )
        candidate.mark_channel_published('jobs_4', 1)
        run = store.create_run(
            query='Senior Go Developer', sources=['telegram', 'threads']
        )
        await service.tick()
        await service._threads_task
        result = store.get_run(run['id'])
        assert result['status'] == 'completed_with_errors'
        assert result['source_states']['telegram']['status'] == 'completed'
        assert len(store.list_results(run['id'])['items']) == 1

    asyncio.run(scenario())


def test_disabled_threads_no_calls_and_premium_disabled_no_database(tmp_path):
    async def scenario():
        source = SimpleNamespace(search_page=AsyncMock())
        store, service = setup(tmp_path, source=source)
        service.settings = SearchSettings()
        run = store.create_run(query='Golang', sources=['threads', 'premium'])
        await service.tick()
        await service._threads_task
        assert store.get_run(run['id'])['status'] == 'completed_with_errors'
        source.search_page.assert_not_awaited()
        assert not (tmp_path / 'premium_search.sqlite3').exists()

    asyncio.run(scenario())


def test_429_preserves_partial_and_sets_durable_cooldown(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(
                side_effect=[ThreadsPage([post()]), ThreadsError('rate_limited', 300)]
            )
        )
        store, service = setup(tmp_path, source=source)
        run = store.create_run(query='Golang', sources=['threads'])
        await service._threads(run['id'])
        assert len(store.list_results(run['id'])['items']) == 1
        state = store.get_run(run['id'])['source_states']['threads']
        assert state['status'] == 'queued' and state['retry_at'] == store.state(
            'threads_cooldown'
        )
        assert store.claim_task('threads') is None
        with pytest.raises(ThreadsError, match='rate_limited'):
            await service._reserve_request()

    asyncio.run(scenario())


def test_permission_failure_pauses_following_jobs(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(side_effect=ThreadsError('permission_denied'))
        )
        store, service = setup(tmp_path, source=source)
        for _ in range(2):
            run = store.create_run(query='Golang', sources=['threads'])
            await service._threads(run['id'])
        assert source.search_page.await_count == 1

    asyncio.run(scenario())


def test_ambiguous_classification_failure_is_review_not_candidate_result(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(
                return_value=ThreadsPage([post(text='Senior Golang engineer')])
            )
        )
        store, service = setup(
            tmp_path,
            source=source,
            classifier=AsyncMock(side_effect=ValueError('bad schema')),
        )
        run = store.create_run(query='Golang', sources=['threads'])
        await service._threads(run['id'])
        assert store.list_results(run['id'])['total'] == 0
        assert store.list_results(run['id'], include_review=True)['total'] == 1

    asyncio.run(scenario())


def test_changed_post_does_not_keep_accepted_classification(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(return_value=ThreadsPage([post()]))
        )
        store, service = setup(tmp_path, source=source)
        first = store.create_run(query='Golang', sources=['threads'])
        await service._threads(first['id'])
        item = store.find_item('threads', '123')
        store.update_item(item['id'], analysis_json='{}', title='old title')
        source.search_page.return_value = ThreadsPage(
            [post(text='Я Go разработчик, ищу работу')]
        )
        second = store.create_run(query='Golang', sources=['threads'])
        await service._threads(second['id'])
        current = store.find_item('threads', '123')
        assert current['classification'] == 'rejected'
        assert current['analysis'] is None and current['title'] is None

    asyncio.run(scenario())


def test_cancel_during_classifier_does_not_retain_result(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(
                return_value=ThreadsPage([post(text='Senior Golang engineer')])
            )
        )
        store, service = setup(tmp_path, source=source)
        run = store.create_run(query='Golang', sources=['threads'])

        async def cancel(_):
            store.cancel(run['id'])
            return Classification('JOB', True, 0.99, 'en', 'test')

        service.classifier = cancel
        await service._threads(run['id'])
        assert store.list_results(run['id'])['total'] == 0
        with pytest.raises(ThreadsError, match='cancelled'):
            await service._reserve_request()

    asyncio.run(scenario())


def test_background_once_six_hours_and_window_overlap(tmp_path):
    async def scenario():
        source = SimpleNamespace(search_page=AsyncMock(return_value=ThreadsPage([])))
        store, service = setup(tmp_path, source=source)
        service.settings = SearchSettings(
            threads_enabled=True, token='test', interval_hours=6, variants_limit=2
        )
        last = datetime.now(timezone.utc) - timedelta(hours=6)
        store.set_state('threads_last_collection', last.isoformat())
        service._schedule()
        service._schedule()
        runs = store.list_runs()
        assert len(runs) == 1 and runs[0]['mode'] == 'preview'
        await service._threads(runs[0]['id'])
        assert source.search_page.call_args.kwargs['since'] == last - timedelta(hours=1)
        assert store.state('threads_last_collection') > last.isoformat()

    asyncio.run(scenario())


def test_explicit_threads_id_uses_existing_persistence_once(tmp_path):
    async def scenario():
        append, notify = AsyncMock(return_value=True), AsyncMock(return_value=True)
        analysis = validate_analysis_result(valid_payload())
        processor = VacancyProcessor(
            lambda _: True,
            AsyncMock(),
            append,
            dedupe_state=JsonlDedupeState(str(tmp_path / 'state.jsonl'), ttl_days=30),
            notify_vacancy=notify,
        )
        for publish in (False, True):
            outcome = await processor.persist_analyzed_message(
                raw_text=post().text,
                post_link=post().permalink,
                published_at=post().timestamp,
                channel_name='Threads · recruiter',
                analysis_result=analysis,
                publish=publish,
                strict_delivery=True,
                vacancy_id='threads:123',
            )
            assert outcome.vacancy_id == 'threads:123'
        assert append.await_count == 1 and notify.await_count == 1
        assert append.call_args.kwargs['vacancy_id'] == 'threads:123'

    asyncio.run(scenario())


def test_failed_save_not_marked_and_retry_uses_same_id(tmp_path):
    async def scenario():
        processor = VacancyProcessor(
            lambda _: True,
            AsyncMock(),
            AsyncMock(side_effect=[False, True]),
            dedupe_state=JsonlDedupeState(str(tmp_path / 'state.jsonl'), ttl_days=30),
        )
        store, service = setup(
            tmp_path,
            processor=processor,
            analyzer=AsyncMock(return_value=validate_analysis_result(valid_payload())),
        )
        run = store.create_run(query='Golang', sources=['threads'])
        rid = store.retain(
            run['id'],
            source='threads',
            external_id='123',
            text=post().text,
            timestamp=post().timestamp.isoformat(),
            permalink=post().permalink,
            vacancy_id='threads:123',
            classification='accepted',
        )
        for expected in ('failed', 'idle'):
            store.request_action(rid, 'save')
            await service._action(store.claim_action())
            assert store.get_item(rid)['action_state'] == expected
        assert store.get_item(rid)['publication_status'] == 'saved'
        assert service.analyzer.await_count == 1

    asyncio.run(scenario())


def test_premium_bridge_uses_existing_store_and_never_owns_telethon(tmp_path):
    async def scenario():
        premium = PremiumSearchStore(str(tmp_path / 'premium.sqlite3'))
        store, service = setup(tmp_path, premium_store=premium)
        run = store.create_run(query='Golang', sources=['premium'])
        await service._premium()
        task = store.active_tasks('premium')[0]
        assert premium.get_run(task['child_id'])['mode'] == 'preview'
        second = store.create_run(query='Golang', sources=['premium'])
        await service._premium()
        assert len(premium.list_runs()) == 1
        store.cancel(second['id'])
        assert premium.get_run(task['child_id'])['cancel_requested'] == 0
        premium.update_run(task['child_id'], status='completed')
        await service._premium()
        assert store.get_run(run['id'])['status'] == 'completed'

    asyncio.run(scenario())


def test_review_after_classifier_failure_keeps_threads_identity_on_save(tmp_path):
    async def scenario():
        source = SimpleNamespace(
            search_page=AsyncMock(
                return_value=ThreadsPage(
                    [post(external_id='ABC', text='Senior Golang engineer')]
                )
            )
        )
        processor = SimpleNamespace(
            persist_analyzed_message=AsyncMock(
                return_value=SimpleNamespace(outcome='saved')
            )
        )
        store, service = setup(
            tmp_path,
            source=source,
            processor=processor,
            classifier=AsyncMock(side_effect=ValueError('failed')),
            analyzer=AsyncMock(return_value=validate_analysis_result(valid_payload())),
        )
        run = store.create_run(query='Golang', sources=['threads'])
        await service._threads(run['id'])
        item = store.find_item('threads', 'ABC')
        assert item['vacancy_id'] == 'threads:ABC'
        store.request_action(item['id'], 'save')
        await service._action(store.claim_action())
        assert (
            processor.persist_analyzed_message.call_args.kwargs['vacancy_id']
            == 'threads:ABC'
        )
        assert store.get_item(item['id'])['publication_status'] == 'saved'

    asyncio.run(scenario())


def test_post_edited_during_analysis_must_be_reanalyzed_before_save(tmp_path):
    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()

        async def analyze(_):
            started.set()
            await resume.wait()
            return validate_analysis_result(valid_payload())

        processor = SimpleNamespace(persist_analyzed_message=AsyncMock())
        store, service = setup(tmp_path, processor=processor, analyzer=analyze)
        run = store.create_run(query='Golang', sources=['threads'])
        kwargs = dict(
            source='threads',
            external_id='123',
            timestamp=post().timestamp.isoformat(),
            permalink=post().permalink,
            vacancy_id='threads:123',
            classification='accepted',
        )
        item = store.retain(
            run['id'], text='Hiring Go engineer apply https://t.me/old', **kwargs
        )
        store.request_action(item, 'save')
        task = asyncio.create_task(service._action(store.claim_action()))
        await started.wait()
        store.retain(
            run['id'], text='Hiring Go engineer apply https://t.me/new', **kwargs
        )
        resume.set()
        await task
        processor.persist_analyzed_message.assert_not_awaited()
        assert store.get_item(item)['action_error'] == 'post_changed'
        assert store.get_item(item)['analysis'] is None

    asyncio.run(scenario())


def test_recovered_premium_action_reconciles_existing_child_delivery(tmp_path):
    async def scenario():
        premium = PremiumSearchStore(str(tmp_path / 'premium.sqlite3'))
        store, service = setup(tmp_path, premium_store=premium)
        child = premium.create_run(query='Golang')
        rid = premium.add_result(
            child['search_run_id'],
            post_link='https://t.me/jobs/9',
            raw_text='Hiring Go engineer',
            published_at=post().timestamp.isoformat(),
            vacancy_id='jobs_9',
            status='accepted',
            decision_reason='accepted',
            channel_name='jobs',
        )
        run = store.create_run(query='Golang', sources=['premium'])
        item = store.retain(
            run['id'],
            source='premium',
            external_id='jobs_9',
            text='Hiring Go engineer',
            timestamp=post().timestamp.isoformat(),
            permalink='https://t.me/jobs/9',
            premium_result_id=rid,
            classification='accepted',
        )
        store.request_action(item, 'save')
        store.claim_action()
        store.recover()
        premium.update_result(rid, status='saved', action_state='idle')
        service._sync_premium_actions()
        assert store.get_item(item)['publication_status'] == 'saved'
        assert store.get_item(item)['action_state'] == 'idle'

    asyncio.run(scenario())
