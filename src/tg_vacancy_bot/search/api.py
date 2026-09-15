"""Authenticated control-plane routes. Network work belongs to the live worker."""

from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from tg_vacancy_bot.search.settings import source_capabilities
from tg_vacancy_bot.search.store import SearchStore


class CreateSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=3, max_length=160)
    sources: list[Literal['telegram', 'premium', 'threads']] = Field(
        min_length=1, max_length=3
    )
    track: Literal['go'] = 'go'
    mode: Literal['preview', 'save', 'save_publish'] = 'preview'
    result_limit: int = Field(default=50, ge=1, le=100)
    period_days: int = Field(default=7, ge=1, le=30)
    include_review: bool = True
    confirmed: bool = False


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmed: bool = False


class SearchAction(Confirmation):
    action: Literal['save', 'publish', 'reject', 'mark_duplicate']


def install_routes(
    app, *, path: str, session, csrf, publisher_configured: bool
) -> None:
    store = SearchStore(path)
    prefix = '/api/v1/search'

    def confirm(body):
        if not body.confirmed:
            raise HTTPException(400, 'Требуется явное подтверждение')

    def require_run(run_id):
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, 'Поиск не найден')
        return run

    @app.get(prefix + '/capabilities')
    def capabilities(_: str = Depends(session)):
        return dict(
            sources=source_capabilities(), publisher_configured=publisher_configured
        )

    @app.post(prefix + '/runs', status_code=202)
    def create(body: CreateSearch, _: str = Depends(csrf)):
        confirm(body)
        if body.mode == 'save_publish' and not publisher_configured:
            raise HTTPException(422, 'Candidate publisher не настроен')
        try:
            return store.create_run(**body.model_dump(exclude={'confirmed'}))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get(prefix + '/runs')
    def runs(limit: int = Query(default=30, ge=1, le=100), _: str = Depends(session)):
        return dict(items=store.list_runs(limit))

    @app.get(prefix + '/runs/{run_id}')
    def run(run_id: str, _: str = Depends(session)):
        return require_run(run_id)

    @app.get(prefix + '/runs/{run_id}/results')
    def results(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        include_review: bool = True,
        _: str = Depends(session),
    ):
        require_run(run_id)
        return store.list_results(
            run_id, offset=offset, limit=limit, include_review=include_review
        )

    @app.post(prefix + '/runs/{run_id}/cancel', status_code=202)
    def cancel(run_id: str, body: Confirmation, _: str = Depends(csrf)):
        confirm(body)
        require_run(run_id)
        return store.cancel(run_id)

    @app.post(prefix + '/results/{item_id}/actions', status_code=202)
    def action(item_id: str, body: SearchAction, _: str = Depends(csrf)):
        confirm(body)
        if body.action == 'publish' and not publisher_configured:
            raise HTTPException(422, 'Candidate publisher не настроен')
        try:
            store.request_action(item_id, body.action)
        except KeyError as error:
            raise HTTPException(404, 'Результат не найден') from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return dict(ok=True)
