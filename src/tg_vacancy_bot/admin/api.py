"""FastAPI administration API with cookie authentication and CSRF protection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from collections import defaultdict
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
from starlette.middleware.trustedhost import TrustedHostMiddleware

from tg_vacancy_bot.admin.control import (
    ActionConflict,
    active_action,
    get_action,
    request_action,
)
from tg_vacancy_bot.admin.settings import (
    AdminSettings,
    SettingsStore,
    StaleSettingsError,
    normalize_public_source,
    validate_editable_instructions,
)
from tg_vacancy_bot.admin.telemetry import TelemetryStore
from tg_vacancy_bot.llm.prompts import DEFAULT_VACANCY_INSTRUCTIONS
from tg_vacancy_bot.paths import resolve_vacancy_groups_db_path
from tg_vacancy_bot.storage.vacancy_groups import VacancyGroupStore

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
    return bool(
        os.getenv('ADMIN_PASSWORD', '') and os.getenv('ADMIN_SESSION_SECRET', '')
    )


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


def _source_entries(store: SettingsStore) -> list[dict[str, Any]]:
    entries = []
    for index, source in enumerate(store.list_sources(), start=1):
        username = source['username']
        origins = source['origins']
        entries.append(
            {
                'token': source['id'],
                'label': f'@{username}' if username else f'Приватный источник {index}',
                'identifier': f'@{username}' if username else None,
                'enabled': source['enabled'],
                'kind': 'public' if username else 'private',
                'origin': origins[0] if len(origins) == 1 else 'multiple',
                'origins': origins,
                'title': source['title'],
                'chat_type': source['chat_type'],
                'added_at': source['created_at'],
                'last_seen_at': source['last_seen_at'],
                'removable': 'admin' in origins,
                'verification_status': (
                    source['verification_status'] if username else 'hidden'
                ),
            }
        )
    return entries


def _public_settings(settings: AdminSettings, store: SettingsStore) -> dict[str, Any]:
    payload = settings.model_dump()
    payload['telegram']['disabled_channels'] = []
    payload['telegram']['folder_channels'] = []
    payload['telegram']['managed_sources'] = []
    payload['telegram']['notify_target'] = _mask_target(
        payload['telegram']['notify_target']
    )
    payload['telegram']['channels'] = _source_entries(store)
    payload['mistral'].pop('vacancy_instructions', None)
    return payload


def create_app(data_dir: str | None = None) -> FastAPI:
    store = SettingsStore(data_dir or os.getenv('DATA_DIR'))
    telemetry = TelemetryStore(data_dir or os.getenv('DATA_DIR'))

    def group_store() -> VacancyGroupStore:
        """Open group state only when its private admin route is requested."""
        return VacancyGroupStore(
            resolve_vacancy_groups_db_path(
                data_dir=data_dir or os.getenv('DATA_DIR'),
                db_path=os.getenv('VACANCY_GROUPS_DB_PATH') or None,
            ),
            int(os.getenv('VACANCY_GROUP_WINDOW_DAYS', '14')),
        )

    app = FastAPI(title='Go Radar Admin API', docs_url=None, redoc_url=None)
    allowed_hosts = [
        item.strip()
        for item in os.getenv(
            'ADMIN_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver'
        ).split(',')
        if item.strip()
    ]
    if not allowed_hosts or any('/' in item or ':' in item for item in allowed_hosts):
        raise RuntimeError('ADMIN_ALLOWED_HOSTS должен содержать только имена хостов')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    failed_logins: dict[str, list[float]] = defaultdict(list)
    login_lock = threading.Lock()
    instance_id = str(uuid.uuid4())

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
        try:
            store.load()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail='storage unavailable'
            ) from error
        return {'status': 'ok', 'instance_id': instance_id}

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
    def login(
        body: LoginRequest, request: Request, response: Response
    ) -> dict[str, bool]:
        password = os.getenv('ADMIN_PASSWORD', '')
        secret = os.getenv('ADMIN_SESSION_SECRET', '')
        if not password or not secret:
            raise HTTPException(
                status_code=503, detail='Доступ администратора ещё не настроен'
            )
        if len(password) < 12 or len(secret) < 24:
            raise HTTPException(
                status_code=503,
                detail='Пароль или секрет сессии администратора слишком короткий',
            )
        client_key = request.client.host if request.client else 'unknown'
        now = time.monotonic()
        with login_lock:
            attempts = [
                stamp for stamp in failed_logins[client_key] if stamp > now - 900
            ]
            failed_logins[client_key] = attempts
        if len(attempts) >= 5:
            raise HTTPException(
                status_code=429,
                detail='Слишком много попыток входа',
                headers={'Retry-After': '900'},
            )
        if not hmac.compare_digest(body.password, password):
            with login_lock:
                failed_logins[client_key].append(now)
            raise HTTPException(status_code=401, detail='Неверный пароль')
        with login_lock:
            failed_logins.pop(client_key, None)
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
            'latest_export_at': (
                telemetry.latest_metric_at('vacancy_saved').isoformat()
                if telemetry.latest_metric_at('vacancy_saved')
                else None
            ),
            'operations': telemetry.recent_operations(10),
            'active_action': active_action(data_dir or os.getenv('DATA_DIR')),
            'secret_status': {
                'telegram': bool(os.getenv('API_ID') and os.getenv('API_HASH')),
                'mistral': bool(os.getenv('MISTRAL_API_KEY')),
                'google_sheets': bool(
                    os.getenv('GOOGLE_SHEET_URL')
                    and os.getenv('GOOGLE_CREDENTIALS_PATH')
                ),
            },
            'channel_count': len(store.list_sources(active_only=True)),
        }

    @app.get('/api/v1/metrics/today')
    def metrics(_: str = Depends(_require_session)) -> dict[str, Any]:
        return telemetry.today_metrics(store.load().sheets.output_timezone)

    @app.get('/api/v1/vacancy-groups')
    def vacancy_groups(
        limit: int = Query(default=50, ge=1, le=100),
        _: str = Depends(_require_session),
    ) -> dict[str, Any]:
        """Returns metadata only: no raw Telegram post text or private actions."""
        items = group_store().list_groups(limit)
        return {'items': items, 'total': len(items)}

    @app.get('/api/v1/vacancy-groups/{group_id}')
    def vacancy_group(
        group_id: str, _: str = Depends(_require_session)
    ) -> dict[str, Any]:
        group = group_store().get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail='Группа вакансий не найдена')
        return group

    @app.post('/api/v1/vacancy-groups/{group_id}/publications/{vacancy_id}/unlink')
    def unlink_group_publication(
        group_id: str,
        vacancy_id: str,
        body: ConfirmRequest,
        _: str = Depends(_require_csrf),
    ) -> dict[str, bool]:
        if not body.confirmed:
            raise HTTPException(status_code=400, detail='Подтвердите разъединение')
        if not group_store().unlink_publication(group_id, vacancy_id):
            raise HTTPException(
                status_code=409,
                detail='Нельзя разъединить каноническую или отсутствующую публикацию',
            )
        telemetry.record_metric('manual_ungroup')
        telemetry.record('vacancy_group_unlinked')
        return {'ok': True}

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
        return _public_settings(store.load(), store)

    @app.put('/api/v1/settings')
    def put_settings(
        payload: dict[str, Any], _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        current = store.load()
        submitted_revision = payload.get('revision')
        if submitted_revision != current.revision:
            raise HTTPException(
                status_code=409, detail='Настройки уже изменены. Обновите страницу.'
            )
        merged = current.model_dump()
        for key in ('telegram', 'filters', 'mistral', 'sheets', 'retention', 'alerts'):
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
        try:
            saved = (
                store.replace_with_enabled_sources(merged, set(incoming_tokens))
                if incoming_tokens is not None
                else store.replace_from_payload(merged)
            )
        except StaleSettingsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        telemetry.record('settings_saved', revision=saved.revision)
        return _public_settings(saved, store)

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
        entries = _source_entries(store)
        return {'items': entries, 'total': len(entries), 'restart_required': True}

    @app.post('/api/v1/sources')
    async def add_source(
        body: SourceRequest, response: Response, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        try:
            identifier = normalize_public_source(body.identifier)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        existing = {
            str(item.get('identifier') or '').strip().lstrip('@').casefold()
            for item in _source_entries(store)
        }
        if identifier.casefold() in existing:
            raise HTTPException(status_code=409, detail='Этот источник уже добавлен')
        item = store.upsert_source(
            identifier,
            origin='admin',
            enabled=False,
            verification_status='unverified',
        )
        try:
            action = request_action(
                'verify_source', data_dir or os.getenv('DATA_DIR'), target_id=item['id']
            )
        except ActionConflict as error:
            store.remove_admin_origin(item['id'])
            raise HTTPException(status_code=409, detail=str(error)) from error
        telemetry.record('source_added')
        response.status_code = 202
        public_item = next(
            entry for entry in _source_entries(store) if entry['token'] == item['id']
        )
        return {
            'item': public_item,
            'action': action,
            'restart_required': True,
        }

    def find_source(token: str) -> dict[str, Any]:
        for item in _source_entries(store):
            if hmac.compare_digest(item['token'], token):
                return item
        raise HTTPException(status_code=404, detail='Источник не найден')

    @app.patch('/api/v1/sources/{token}')
    def update_source(
        token: str, body: SourceEnabledRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        find_source(token)
        try:
            store.set_source_enabled(token, body.enabled)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        telemetry.record('source_enabled_changed', enabled=body.enabled)
        refreshed = next(
            entry for entry in _source_entries(store) if entry['token'] == token
        )
        return {'item': refreshed, 'restart_required': True}

    @app.post('/api/v1/sources/{token}/verify')
    async def verify_source(
        token: str, response: Response, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        item = find_source(token)
        identifier = (item['identifier'] or '').strip().lstrip('@')
        if not identifier:
            raise HTTPException(
                status_code=422,
                detail='Приватный источник нельзя проверить из браузера',
            )
        try:
            action = request_action(
                'verify_source', data_dir or os.getenv('DATA_DIR'), target_id=token
            )
        except ActionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        response.status_code = 202
        return {'item': item, 'action': action, 'restart_required': True}

    @app.delete('/api/v1/sources/{token}')
    def delete_source(
        token: str, body: ConfirmRequest, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail='Подтвердите удаление источника'
            )
        item = find_source(token)
        if 'admin' not in item['origins']:
            raise HTTPException(
                status_code=409,
                detail='Этот источник пришёл из .env или папки Telegram и не удаляется здесь',
            )
        store.remove_admin_origin(token)
        telemetry.record('source_deleted')
        return {'ok': True, 'restart_required': True}

    @app.post('/api/v1/actions')
    def action(
        body: ActionRequest, response: Response, _: str = Depends(_require_csrf)
    ) -> dict[str, Any]:
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail='Требуется явное подтверждение действия'
            )
        try:
            requested = request_action(body.action, data_dir or os.getenv('DATA_DIR'))
        except ActionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        telemetry.record('action_requested', action=body.action)
        response.status_code = 202
        return requested

    @app.get('/api/v1/actions/{action_id}')
    def action_status(
        action_id: str, _: str = Depends(_require_session)
    ) -> dict[str, Any]:
        result = get_action(action_id, data_dir or os.getenv('DATA_DIR'))
        if result is None:
            raise HTTPException(status_code=404, detail='Операция не найдена')
        return result

    from tg_vacancy_bot.premium_search.settings import (
        database_path,
        enabled,
        publisher_configured,
    )

    if enabled():
        from tg_vacancy_bot.premium_search.api import install_routes

        install_routes(
            app,
            path=database_path(data_dir),
            session=_require_session,
            csrf=_require_csrf,
            publisher_configured=publisher_configured(),
        )

    static_dir = Path(os.getenv('ADMIN_STATIC_DIR', '/app/web'))
    if static_dir.exists():
        app.mount('/_next', StaticFiles(directory=static_dir / '_next'), name='next')

        @app.get('/', include_in_schema=False)
        @app.get('/{frontend_path:path}', include_in_schema=False)
        def frontend(frontend_path: str = '') -> FileResponse:
            # Next is exported as a static SPA. These private UI routes need the
            # same entry document for direct links and browser back/forward;
            # `/api` endpoints have already been matched above.
            if frontend_path == 'api' or frontend_path.startswith('api/'):
                raise HTTPException(status_code=404, detail='Маршрут API не найден')
            del frontend_path
            return FileResponse(static_dir / 'index.html')

    return app


app = create_app()
