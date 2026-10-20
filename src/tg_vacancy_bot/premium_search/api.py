"""Protected Premium routes; mutations only enqueue local work."""

import os
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from tg_vacancy_bot.premium_search.store import (
    ActiveRunError,
    PremiumSearchStore,
)
from tg_vacancy_bot.premium_search.tracks import TRACKS


class CreateRun(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=3, max_length=160)
    track: str = 'go'
    mode: Literal['preview', 'save', 'save_publish'] = 'preview'
    result_limit: int = Field(default=50, ge=1, le=100)
    period_days: int = Field(default=7, ge=1, le=30)
    profile_filters: dict | None = None
    include_review: bool = True
    confirmed: bool = False


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmed: bool = False


class ResultAction(Confirmation):
    action: Literal['save', 'publish', 'reject', 'mark_duplicate', 'add_public_source']


def install_routes(
    app, *, path: str, session, csrf, publisher_configured: bool
) -> None:
    store = PremiumSearchStore(path)
    prefix = '/api/v1/premium-search'

    def confirm(body):
        if not body.confirmed:
            raise HTTPException(400, 'Требуется явное подтверждение')

    def public_run(run):
        if run is None:
            return None
        result = dict(run)
        result.pop('owner', None)
        result.pop('quota_subject', None)
        profile = result.get('profile_snapshot')
        if isinstance(profile, dict):
            profile = dict(profile)
            profile.pop('telegram_user_id', None)
            result['profile_snapshot'] = profile
        return result

    def require_run(run_id):
        run = store.get_run(run_id)
        if run is None or run['owner'].startswith('candidate:'):
            raise HTTPException(404, 'Поиск не найден')
        return run

    @app.get(prefix + '/capabilities')
    def capabilities(_: str = Depends(session)):
        admin_id = os.getenv('PREMIUM_SEARCH_WEB_ADMIN_TELEGRAM_ID')
        allowed_user_ids = {
            value.strip()
            for value in os.getenv('CANDIDATE_BOT_ALLOWED_USER_IDS', '').split(',')
            if value.strip().isdigit()
        }
        catalog_enabled = bool(
            admin_id
            and admin_id in allowed_user_ids
            and os.getenv('CANDIDATE_CATALOG_SEARCH_ENABLED', '').casefold()
            in {'1', 'true', 'yes', 'on'}
        )
        from tg_vacancy_bot.candidate_catalog import CATALOG

        return dict(
            enabled=True,
            publisher_configured=publisher_configured,
            result_limit=50,
            max_results=100,
            max_llm_calls=3000,
            max_search_posts=1000,
            profile_max_search_posts=10_000,
            period_days=7,
            max_period_days=30,
            profile_max_results=50,
            profile_max_period_days=7,
            weekly_search_limit=None,
            catalog_profile_filters=catalog_enabled,
            profile_directions=(
                [
                    {
                        'id': direction['id'],
                        'label': direction['label'],
                        'roles': [
                            {'id': role['id'], 'label': role['label']}
                            for specialization in direction['specializations']
                            for role in specialization['roles']
                        ],
                    }
                    for direction in CATALOG['directions']
                ]
                if catalog_enabled
                else []
            ),
            tracks=[
                dict(
                    key=t.key,
                    label=t.label,
                    confidence_threshold=t.confidence_threshold,
                )
                for t in TRACKS.values()
                if t.key != 'catalog' or catalog_enabled
            ],
        )

    @app.post(prefix + '/runs', status_code=202)
    def create(body: CreateRun, _: str = Depends(csrf)):
        confirm(body)
        owner = 'admin'
        profile_snapshot = None
        try:
            web_admin_id = int(os.getenv('PREMIUM_SEARCH_WEB_ADMIN_TELEGRAM_ID') or 0)
        except ValueError:
            web_admin_id = 0
        if web_admin_id:
            owner = f'admin:{web_admin_id}'
        if body.track == 'catalog':
            if body.period_days > 7 or body.result_limit > 50:
                raise HTTPException(
                    422, 'Профильный поиск ограничен 50 вакансиями и 7 днями'
                )
            try:
                from tg_vacancy_bot.candidate_catalog import role_path

                admin_id = web_admin_id
                allowed = {
                    int(value.strip())
                    for value in os.getenv('CANDIDATE_BOT_ALLOWED_USER_IDS', '').split(
                        ','
                    )
                    if value.strip().isdigit()
                }
                if not admin_id or admin_id not in allowed:
                    raise ValueError(
                        'Для профильного поиска не настроен Telegram ID администратора'
                    )
                filters = body.profile_filters or {}
                allowed_filter_keys = {
                    'direction_id',
                    'role_id',
                    'seniority',
                    'required_skills',
                    'excluded_skills',
                }
                if set(filters) - allowed_filter_keys:
                    raise ValueError('Неизвестный фильтр профиля')
                for key in ('seniority', 'required_skills', 'excluded_skills'):
                    values = filters.get(key, [])
                    if (
                        not isinstance(values, list)
                        or len(values) > 20
                        or any(
                            not isinstance(value, str)
                            or not value.strip()
                            or len(value) > 80
                            for value in values
                        )
                    ):
                        raise ValueError('Проверьте значения фильтров профиля')
                path = role_path(filters.get('role_id', ''))
                if not path or path[0] != filters.get('direction_id'):
                    raise ValueError('Выберите направление и роль из каталога')
                owner = f'admin:{admin_id}'
                profile_snapshot = dict(
                    profile_id='admin-premium-filter',
                    telegram_user_id=admin_id,
                    name='Admin Premium filters',
                    direction_id=path[0],
                    specialization_id=path[1],
                    role_id=path[2],
                    preferences={
                        'profile_contract': 'catalog-v3',
                        'seniority': filters.get('seniority', []),
                        'required_skills': filters.get('required_skills', []),
                        'excluded_skills': filters.get('excluded_skills', []),
                    },
                    is_active=True,
                    version=1,
                )
            except (TypeError, ValueError) as error:
                raise HTTPException(422, str(error)) from error
            if body.mode != 'preview':
                raise HTTPException(
                    422, 'Профильный поиск работает в режиме предпросмотра'
                )
        elif body.profile_filters is not None:
            raise HTTPException(
                422, 'Фильтры профиля доступны только для каталожного поиска'
            )
        if body.mode == 'save_publish' and not publisher_configured:
            raise HTTPException(422, 'Candidate publisher не настроен')
        try:
            values = body.model_dump(exclude={'confirmed', 'profile_filters'})
            values.update(
                owner=owner,
                profile_snapshot=profile_snapshot,
            )
            return public_run(store.create_run(**values))
        except ActiveRunError as error:
            raise HTTPException(
                409, dict(message=str(error), existing_run_id=error.run_id)
            ) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get(prefix + '/runs')
    def runs(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(session)):
        return dict(
            items=[
                public_run(run) for run in store.list_runs(limit, include_private=False)
            ]
        )

    @app.get(prefix + '/runs/{run_id}')
    def run(run_id: str, _: str = Depends(session)):
        return public_run(require_run(run_id))

    @app.post(prefix + '/runs/{run_id}/cancel', status_code=202)
    def cancel(run_id: str, body: Confirmation, _: str = Depends(csrf)):
        confirm(body)
        require_run(run_id)
        store.cancel(run_id)
        return public_run(store.get_run(run_id))

    @app.get(prefix + '/runs/{run_id}/results')
    def results(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        status: str | None = None,
        _: str = Depends(session),
    ):
        require_run(run_id)
        return store.list_results(run_id, offset, limit, status)

    @app.post(prefix + '/results/{result_id}/actions', status_code=202)
    def action(result_id: str, body: ResultAction, _: str = Depends(csrf)):
        confirm(body)
        target = store.get_result(result_id)
        if target is None:
            raise HTTPException(404, 'Результат не найден')
        run = require_run(target['search_run_id'])
        if run['track'] == 'catalog' and body.action in {'save', 'publish'}:
            raise HTTPException(
                409,
                'Профильный результат уже сохраняется только в общий реестр',
            )
        if body.action == 'publish' and not publisher_configured:
            raise HTTPException(422, 'Candidate publisher не настроен')
        try:
            result = store.request_action(result_id, body.action)
        except KeyError as error:
            raise HTTPException(404, 'Результат не найден') from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return dict(item=result, restart_required=body.action == 'add_public_source')
