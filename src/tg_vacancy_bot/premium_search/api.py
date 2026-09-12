"""Protected Premium routes; mutations only enqueue local work."""

from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from tg_vacancy_bot.premium_search.store import ActiveRunError, PremiumSearchStore
from tg_vacancy_bot.premium_search.tracks import TRACKS


class CreateRun(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=3, max_length=160)
    track: str = 'go'
    mode: Literal['preview', 'save', 'save_publish'] = 'preview'
    result_limit: int = Field(default=50, ge=1, le=100)
    period_days: int = Field(default=7, ge=1, le=30)
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

    def require_run(run_id):
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, 'Поиск не найден')
        return run

    @app.get(prefix + '/capabilities')
    def capabilities(_: str = Depends(session)):
        return dict(
            enabled=True,
            publisher_configured=publisher_configured,
            result_limit=50,
            max_results=100,
            max_llm_calls=3000,
            max_search_posts=1000,
            period_days=7,
            max_period_days=30,
            tracks=[
                dict(
                    key=t.key,
                    label=t.label,
                    confidence_threshold=t.confidence_threshold,
                )
                for t in TRACKS.values()
            ],
        )

    @app.post(prefix + '/runs', status_code=202)
    def create(body: CreateRun, _: str = Depends(csrf)):
        confirm(body)
        if body.mode == 'save_publish' and not publisher_configured:
            raise HTTPException(422, 'Candidate publisher не настроен')
        try:
            return store.create_run(**body.model_dump(exclude={'confirmed'}))
        except ActiveRunError as error:
            raise HTTPException(
                409, dict(message=str(error), existing_run_id=error.run_id)
            ) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get(prefix + '/runs')
    def runs(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(session)):
        return dict(items=store.list_runs(limit))

    @app.get(prefix + '/runs/{run_id}')
    def run(run_id: str, _: str = Depends(session)):
        return require_run(run_id)

    @app.post(prefix + '/runs/{run_id}/cancel', status_code=202)
    def cancel(run_id: str, body: Confirmation, _: str = Depends(csrf)):
        confirm(body)
        require_run(run_id)
        store.cancel(run_id)
        return store.get_run(run_id)

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
        if body.action == 'publish' and not publisher_configured:
            raise HTTPException(422, 'Candidate publisher не настроен')
        try:
            result = store.request_action(result_id, body.action)
        except KeyError as error:
            raise HTTPException(404, 'Результат не найден') from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return dict(item=result, restart_required=body.action == 'add_public_source')
