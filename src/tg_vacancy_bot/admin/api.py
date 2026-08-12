"""FastAPI administration API with cookie authentication and CSRF protection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tg_vacancy_bot.admin.control import request_action
from tg_vacancy_bot.admin.settings import (
    AdminSettings,
    ManagedSource,
    SettingsStore,
    normalize_public_source,
    validate_editable_instructions,
)
from tg_vacancy_bot.admin.telemetry import TelemetryStore
from tg_vacancy_bot.llm.prompts import DEFAULT_VACANCY_INSTRUCTIONS


SESSION_COOKIE = 'admin_session'
CSRF_COOKIE = 'admin_csrf'
SESSION_MAX_AGE = 60 * 60 * 8


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class ActionRequest(BaseModel):
    action: str
    confirmed: bool = False


class SourceRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=256)


class SourceEnabledRequest(BaseModel):
    enabled: bool


class ConfirmRequest(BaseModel):
    confirmed: bool = False


class PromptRequest(BaseModel):
    instructions: str = Field(min_length=20, max_length=12000)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _sign(payload: str, secret: str) -> str:
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64(signature)


def _session_value(secret: str) -> tuple[str, str]:
    csrf = _b64(os.urandom(24))
    payload = _b64(
        json.dumps({'exp': int(time.time()) + SESSION_MAX_AGE, 'csrf': csrf}).encode()
    )
    return f'{payload}.{_sign(payload, secret)}', csrf


def _session_csrf(value: str | None, secret: str) -> str | None:
    if not value or '.' not in value:
        return None
    payload, signature = value.rsplit('.', 1)
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get('exp', 0) < time.time():
        return None
    csrf = data.get('csrf')
    return csrf if isinstance(csrf, str) else None


def _is_configured() -> bool:
    return bool(os.getenv('ADMIN_PASSWORD') and os.getenv('ADMIN_SESSION_SECRET'))


def _require_session(
    admin_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> str:
    secret = os.getenv('ADMIN_SESSION_SECRET', '')
    csrf = _session_csrf(admin_session, secret) if secret else None
    if not csrf:
        raise HTTPException(status_code=401, detail='Требуется вход в панель')
    return csrf


def _require_csrf(
    session_csrf: str = Depends(_require_session),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias='X-CSRF-Token'),
) -> str:
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail='CSRF token обязателен')
    if not (
        hmac.compare_digest(session_csrf, csrf_cookie)
        and hmac.compare_digest(session_csrf, csrf_header)
    ):
        raise HTTPException(status_code=403, detail='CSRF token не совпадает')
    return session_csrf


def _mask_target(value: str) -> str:
    if value.lstrip('-').isdigit():
        return 'Настроен приватный Telegram-источник'
    return value


def _channel_token(value: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest())[:18]


def _source_token(origin: str, value: str, secret: str) -> str:
    return _channel_token(f'{origin}:{value}', secret)


def _source_values(
    settings: AdminSettings, base_channels: list[str]
) -> list[tuple[str, str, str | None, bool]]:
    return [
        *[
            (
                'environment',
                value,
                None,
                value not in set(settings.telegram.disabled_channels),
            )
            for value in base_channels
        ],
        *[
            (
                'folder',
                value,
                None,
                value not in set(settings.telegram.disabled_channels),
            )
            for value in settings.telegram.folder_channels
        ],
        *[
            (
                'legacy',
                value,
                None,
                value not in set(settings.telegram.disabled_channels),
            )
            for value in settings.telegram.additional_channels
        ],
        *[
            ('managed', item.identifier, item.added_at, item.enabled)
            for item in settings.telegram.managed_sources
        ],
    ]


def _source_entries(
    settings: AdminSettings, base_channels: list[str]
) -> list[dict[str, Any]]:
    secret = os.getenv('ADMIN_SESSION_SECRET', 'unconfigured')
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, (origin, value, added_at, enabled) in enumerate(
        _source_values(settings, base_channels), start=1
    ):
        identity = value.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        numeric = value.lstrip('-').isdigit()
        entries.append(
            {
                'token': _source_token(origin, value, secret),
                'label': (
                    f'Приватный источник {index}'
                    if numeric
                    else f'@{value.lstrip("@")}'
                ),
                'identifier': None if numeric else f'@{value.lstrip("@")}',
                'enabled': enabled,
                'kind': 'private' if numeric else 'public',
                'origin': origin,
                'added_at': added_at,
                'removable': origin == 'managed',
            }
        )
    return entries


def _public_settings(
    settings: AdminSettings, base_channels: list[str]
) -> dict[str, Any]:
    payload = settings.model_dump()
    payload['telegram']['disabled_channels'] = []
    payload['telegram']['folder_channels'] = []
    payload['telegram']['managed_sources'] = []
    payload['telegram']['notify_target'] = _mask_target(
        payload['telegram']['notify_target']
    )
    payload['telegram']['channels'] = _source_entries(settings, base_channels)
    payload['mistral'].pop('vacancy_instructions', None)
    return payload


def _base_channels() -> list[str]:
    return [
        item.strip()
        for item in os.getenv('TARGET_CHANNELS', '').split(',')
        if item.strip()
    ]


def create_app(data_dir: str | None = None) -> FastAPI:
    store = SettingsStore(data_dir or os.getenv('DATA_DIR'))
    telemetry = TelemetryStore(data_dir or os.getenv('DATA_DIR'))
    app = FastAPI(title='Go Radar Admin API', docs_url=None, redoc_url=None)

    @app.middleware('http')
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get('/healthz')
    def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/v1/auth/status')
    def auth_status(
        admin_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)
    ) -> dict[str, bool]:
        secret = os.getenv('ADMIN_SESSION_SECRET', '')
        return {
            'configured': _is_configured(),
            'authenticated': bool(_session_csrf(admin_session, secret)),
        }

    @app.post('/api/v1/auth/login')
    def login(body: LoginRequest, response: Response) -> dict[str, bool]:
        password = os.getenv('ADMIN_PASSWORD', '')
        secret = os.getenv('ADMIN_SESSION_SECRET', '')
        if not password or not secret:
            raise HTTPException(
                status_code=503, detail='Доступ администратора ещё не настроен'
            )
        if not hmac.compare_digest(body.password, password):
            raise HTTPException(status_code=401, detail='Неверный пароль')
        session, csrf = _session_value(secret)
        secure = os.getenv('ADMIN_COOKIE_SECURE', 'true').lower() != 'false'
        response.set_cookie(
            SESSION_COOKIE,
            session,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=secure,
            samesite='strict',
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            max_age=SESSION_MAX_AGE,
            httponly=False,
            secure=secure,
            samesite='strict',
        )
        telemetry.record('admin_login')
        return {'ok': True}

    @app.post('/api/v1/auth/logout')
    def logout(response: Response, _: str = Depends(_require_csrf)) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE)
        return {'ok': True}

    @app.get('/api/v1/dashboard')
    def dashboard(_: str = Depends(_require_session)) -> dict[str, Any]:
        settings = store.load()
        return {
            'settings_revision': settings.revision,
            'heartbeat': telemetry.read_heartbeat(),
            'operations': telemetry.recent_operations(10),
            'secret_status': {
                'telegram': bool(os.getenv('API_ID') and os.getenv('API_HASH')),
                'mistral': bool(os.getenv('MISTRAL_API_KEY')),
                'google_sheets': bool(
                    os.getenv('GOOGLE_SHEET_URL')
                    and os.getenv('GOOGLE_CREDENTIALS_PATH')
                ),
            },
            'channel_count': (len(_source_entries(settings, _base_channels()))),
        }

    @app.get('/api/v1/metrics/today')
    def metrics(_: str = Depends(_require_session)) -> dict[str, Any]:
        return telemetry.today_metrics(store.load().sheets.output_timezone)

    @app.get('/api/v1/errors')
    def errors(_: str = Depends(_require_session)) -> list[dict[str, Any]]:
        return telemetry.attention_errors()

    @app.post('/api/v1/errors/{error_id}/resolve')
    def resolve_error(
        error_id: str, body: ConfirmRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail='Подтвердите обновление статуса'
            )
        resolved = telemetry.resolve_error(error_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail='Ошибка не найдена')
        telemetry.record('error_resolved', error_id=error_id)
        return resolved

    @app.get('/api/v1/settings')
    def get_settings(_: str = Depends(_require_session)) -> dict[str, Any]:
        return _public_settings(store.load(), _base_channels())

    @app.put('/api/v1/settings')
    def put_settings(
        payload: dict[str, Any], _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        current = store.load()
        merged = current.model_dump()
        for key in ('telegram', 'filters', 'mistral', 'sheets'):
            if key in payload and isinstance(payload[key], dict):
                update = dict(payload[key])
                if key == 'telegram':
                    update.pop('channels', None)
                    update.pop('folder_channels', None)
                    update.pop('disabled_channels', None)
                    update.pop('enabled_channel_tokens', None)
                    update.pop('managed_sources', None)
                if key == 'mistral':
                    update.pop('vacancy_instructions', None)
                merged[key].update(update)
        incoming_tokens = payload.get('telegram', {}).get('enabled_channel_tokens')
        if incoming_tokens is not None:
            all_channels = [
                *_base_channels(),
                *current.telegram.folder_channels,
                *current.telegram.additional_channels,
            ]
            allowed = {
                _channel_token(
                    item, os.getenv('ADMIN_SESSION_SECRET', 'unconfigured')
                ): item
                for item in all_channels
            }
            merged['telegram']['disabled_channels'] = [
                value
                for token, value in allowed.items()
                if token not in set(incoming_tokens)
            ]
        try:
            saved = store.replace_from_payload(merged)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        telemetry.record('settings_saved', revision=saved.revision)
        return _public_settings(saved, _base_channels())

    @app.get('/api/v1/operations')
    def operations(_: str = Depends(_require_session)) -> list[dict[str, Any]]:
        return telemetry.recent_operations()

    @app.get('/api/v1/logs')
    def logs(
        level: str | None = Query(default=None, pattern='^(INFO|WARNING|ERROR)$'),
        component: str | None = Query(default=None),
        period: str = Query(default='7d', pattern='^(today|7d|all)$'),
        search: str = Query(default='', max_length=120),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        _: str = Depends(_require_session),
    ) -> dict[str, Any]:
        """Return a limited, already-redacted application log slice."""
        return telemetry.read_logs(
            level=level,
            component=component,
            period=period,
            search=search,
            offset=offset,
            limit=limit,
        )

    @app.get('/api/v1/prompt')
    def get_prompt(_: str = Depends(_require_session)) -> dict[str, Any]:
        instructions = store.load().mistral.vacancy_instructions
        return {
            'instructions': instructions or DEFAULT_VACANCY_INSTRUCTIONS,
            'is_custom': bool(instructions),
            'default_instructions': DEFAULT_VACANCY_INSTRUCTIONS,
            'restart_required': True,
            'variables': [],
        }

    @app.put('/api/v1/prompt')
    def put_prompt(
        body: PromptRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        try:
            instructions = validate_editable_instructions(body.instructions)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        current = store.load()
        current.mistral.vacancy_instructions = instructions
        saved = store.save(current)
        telemetry.record('prompt_saved', revision=saved.revision)
        return {
            'instructions': instructions,
            'is_custom': True,
            'restart_required': True,
        }

    @app.post('/api/v1/prompt/reset')
    def reset_prompt(
        body: ConfirmRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail='Подтвердите восстановление промпта'
            )
        current = store.load()
        current.mistral.vacancy_instructions = None
        saved = store.save(current)
        telemetry.record('prompt_reset', revision=saved.revision)
        return {
            'instructions': DEFAULT_VACANCY_INSTRUCTIONS,
            'is_custom': False,
            'restart_required': True,
        }

    @app.get('/api/v1/sources')
    def sources(_: str = Depends(_require_session)) -> dict[str, Any]:
        entries = _source_entries(store.load(), _base_channels())
        return {'items': entries, 'total': len(entries), 'restart_required': True}

    @app.post('/api/v1/sources')
    def add_source(
        body: SourceRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        try:
            identifier = normalize_public_source(body.identifier)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        current = store.load()
        existing = {
            str(item.get('identifier') or '').strip().lstrip('@').casefold()
            for item in _source_entries(current, _base_channels())
        }
        if identifier.casefold() in existing:
            raise HTTPException(status_code=409, detail='Этот источник уже добавлен')
        current.telegram.managed_sources.append(ManagedSource(identifier=identifier))
        saved = store.save(current)
        telemetry.record('source_added', source=identifier, revision=saved.revision)
        entries = _source_entries(saved, _base_channels())
        item = next(
            entry
            for entry in entries
            if entry['origin'] == 'managed' and entry['identifier'] == f'@{identifier}'
        )
        return {'item': item, 'restart_required': True}

    def find_source(token: str) -> tuple[AdminSettings, dict[str, Any]]:
        current = store.load()
        for item in _source_entries(current, _base_channels()):
            if hmac.compare_digest(item['token'], token):
                return current, item
        raise HTTPException(status_code=404, detail='Источник не найден')

    @app.patch('/api/v1/sources/{token}')
    def update_source(
        token: str, body: SourceEnabledRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        current, item = find_source(token)
        identifier = (item['identifier'] or '').strip().lstrip('@')
        if item['origin'] == 'managed':
            for source in current.telegram.managed_sources:
                if source.identifier.casefold() == identifier.casefold():
                    source.enabled = body.enabled
                    break
        else:
            secret = os.getenv('ADMIN_SESSION_SECRET', 'unconfigured')
            raw = next(
                (
                    value
                    for origin, value, _, _ in _source_values(current, _base_channels())
                    if _source_token(origin, value, secret) == token
                ),
                None,
            )
            if raw is None:
                raise HTTPException(status_code=404, detail='Источник не найден')
            disabled = set(current.telegram.disabled_channels)
            if body.enabled:
                disabled.discard(raw)
            else:
                disabled.add(raw)
            current.telegram.disabled_channels = sorted(disabled)
        saved = store.save(current)
        telemetry.record(
            'source_enabled_changed', enabled=body.enabled, revision=saved.revision
        )
        refreshed = next(
            entry
            for entry in _source_entries(saved, _base_channels())
            if entry['origin'] == item['origin']
            and entry['identifier'] == item['identifier']
        )
        return {'item': refreshed, 'restart_required': True}

    @app.delete('/api/v1/sources/{token}')
    def delete_source(
        token: str, body: ConfirmRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail='Подтвердите удаление источника'
            )
        current, item = find_source(token)
        if item['origin'] != 'managed':
            raise HTTPException(
                status_code=409,
                detail='Этот источник пришёл из .env или папки Telegram и не удаляется здесь',
            )
        identifier = (item['identifier'] or '').strip().lstrip('@').casefold()
        current.telegram.managed_sources = [
            source
            for source in current.telegram.managed_sources
            if source.identifier.casefold() != identifier
        ]
        saved = store.save(current)
        telemetry.record('source_deleted', revision=saved.revision)
        return {'ok': True, 'restart_required': True}

    @app.post('/api/v1/actions')
    def action(body: ActionRequest, _: str = Depends(_require_csrf)) -> dict[str, str]:
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail='Требуется явное подтверждение действия'
            )
        try:
            requested = request_action(body.action, data_dir or os.getenv('DATA_DIR'))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        telemetry.record('action_requested', action=body.action)
        return {'id': requested['id'], 'action': requested['action']}

    static_dir = Path(os.getenv('ADMIN_STATIC_DIR', '/app/web'))
    if static_dir.exists():
        app.mount('/_next', StaticFiles(directory=static_dir / '_next'), name='next')

        @app.get('/')
        def frontend() -> FileResponse:
            return FileResponse(static_dir / 'index.html')

    return app


app = create_app()
