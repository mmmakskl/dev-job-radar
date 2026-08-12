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

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tg_vacancy_bot.admin.control import request_action
from tg_vacancy_bot.admin.settings import AdminSettings, SettingsStore
from tg_vacancy_bot.admin.telemetry import TelemetryStore


SESSION_COOKIE = 'admin_session'
CSRF_COOKIE = 'admin_csrf'
SESSION_MAX_AGE = 60 * 60 * 8


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class ActionRequest(BaseModel):
    action: str
    confirmed: bool = False


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


def _public_settings(
    settings: AdminSettings, base_channels: list[str]
) -> dict[str, Any]:
    secret = os.getenv('ADMIN_SESSION_SECRET', 'unconfigured')
    disabled = set(settings.telegram.disabled_channels)
    entries = []
    all_channels = [
        *base_channels,
        *settings.telegram.folder_channels,
        *settings.telegram.additional_channels,
    ]
    for index, channel in enumerate(dict.fromkeys(all_channels), start=1):
        is_numeric = channel.lstrip('-').isdigit()
        entries.append(
            {
                'token': _channel_token(channel, secret),
                'label': (
                    f'Приватный источник {index}'
                    if is_numeric
                    else f'@{channel.lstrip("@")} '
                ),
                'enabled': channel not in disabled,
                'kind': 'private' if is_numeric else 'public',
            }
        )
    payload = settings.model_dump()
    payload['telegram']['disabled_channels'] = []
    payload['telegram']['folder_channels'] = []
    payload['telegram']['notify_target'] = _mask_target(
        payload['telegram']['notify_target']
    )
    payload['telegram']['channels'] = entries
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
            'channel_count': (
                len(_base_channels())
                + len(settings.telegram.folder_channels)
                + len(settings.telegram.additional_channels)
            ),
        }

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
    def logs(_: str = Depends(_require_session)) -> list[dict[str, Any]]:
        """Return the deliberately redacted operational event stream."""
        return telemetry.recent_operations()

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
