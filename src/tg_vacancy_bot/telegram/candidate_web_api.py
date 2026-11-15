"""Owner-scoped Candidate Mini App API backed by the existing bot stores."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from tg_vacancy_bot.candidate_catalog import (
    PROFILE_CONTRACT,
    role_path,
    validate_profile_path,
)
from tg_vacancy_bot.models import NOT_SPECIFIED
from tg_vacancy_bot.profile_matcher import match_candidate_filters
from tg_vacancy_bot.premium_search.settings import catalog_search_allowed
from tg_vacancy_bot.premium_search.store import PremiumSearchStore
from tg_vacancy_bot.premium_search.templates import compose_query, list_templates
from tg_vacancy_bot.registry import RegistryMatch, VacancyRegistry
from tg_vacancy_bot.search.store import SearchStore
from tg_vacancy_bot.telegram.candidate_store import CandidateProfile, CandidateStore

PREFIX = '/api/v1/candidate'
INIT_DATA_MAX_AGE = 24 * 60 * 60
WEB_SESSION_COOKIE = 'candidate_session'
WEB_SESSION_AGE = 7 * 24 * 60 * 60


class BrowserCodeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    code: str = Field(pattern=r'^\d{10}$')


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    name: str = Field(min_length=1, max_length=80)
    direction_id: str = Field(min_length=1, max_length=80)
    role_id: str = Field(min_length=1, max_length=80)
    seniority: list[
        Literal['intern', 'junior', 'middle', 'senior', 'staff', 'lead', 'head']
    ] = Field(default_factory=list, max_length=7)
    stacks: list[str] = Field(default_factory=list, max_length=20)
    required_skills: list[str] = Field(default_factory=list, max_length=20)
    excluded_skills: list[str] = Field(default_factory=list, max_length=20)
    delivery_mode: Literal['immediate', 'hourly', 'manual'] = 'immediate'
    formats: list[Literal['remote', 'hybrid', 'office']] | None = None
    geography: list[Literal['russia', 'europe', 'usa', 'cis', 'asia']] | None = None
    vacancy_languages: list[Literal['ru', 'en', 'other']] | None = None


class ProfileUpdate(ProfileInput):
    version: int = Field(ge=1)


class StartPremium(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    template_id: str = Field(min_length=2, max_length=120)
    client_request_id: str = Field(min_length=8, max_length=80)


class FeedSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    older_days: int = Field(ge=2, le=3650)


def _telegram_user(init_data: str | None, token: str, allowed: set[int]) -> dict:
    """Verify Telegram's bot-token HMAC before trusting any user field."""
    if not token:
        raise HTTPException(503, 'Mini App не настроен')
    if not init_data or len(init_data) > 4096:
        raise HTTPException(401, 'Откройте Mini App через Telegram')
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError('duplicate field')
        received_hash = values.pop('hash')
        auth_date = int(values['auth_date'])
        user = json.loads(values['user'])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, 'Недействительные данные Telegram') from None
    now = int(time.time())
    if (
        not isinstance(user, dict)
        or isinstance(user.get('id'), bool)
        or not isinstance(user.get('id'), int)
    ):
        raise HTTPException(401, 'Недействительные данные Telegram')
    if auth_date > now + 60 or now - auth_date > INIT_DATA_MAX_AGE:
        raise HTTPException(401, 'Сессия Mini App истекла')
    check = '\n'.join(f'{key}={value}' for key, value in sorted(values.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise HTTPException(401, 'Недействительные данные Telegram')
    if user['id'] not in allowed:
        raise HTTPException(403, 'Доступ к Candidate Bot ограничен')
    return {
        'id': user['id'],
        'first_name': str(user.get('first_name') or '')[:80],
        'username': str(user.get('username') or '')[:80],
    }


def _profile_view(profile: CandidateProfile) -> dict:
    return {
        'profile_id': profile.profile_id,
        'name': profile.name,
        'direction_id': profile.direction_id,
        'role_id': profile.role_id,
        'preferences': profile.preferences,
        'is_active': profile.is_active,
        'version': profile.version,
    }


def _preferences(body: ProfileInput, current: dict | None = None) -> dict:
    for value in [*body.required_skills, *body.excluded_skills]:
        if not value.strip() or len(value) > 80:
            raise HTTPException(422, 'Проверьте навыки и слова-исключения')
    existing = dict(current or {})
    if 'stacks' in body.model_fields_set:
        existing.pop('primary_language', None)
        existing.pop('additional_languages', None)
    if any(
        field in body.model_fields_set
        for field in ('formats', 'geography', 'vacancy_languages')
    ):
        if existing.get('candidate_filter_version') != 1:
            existing['legacy_candidate_filters'] = {
                key: existing.get(key, [])
                for key in ('formats', 'geography', 'vacancy_languages')
            }
            for key in ('formats', 'geography', 'vacancy_languages'):
                existing[key] = []
        for key in ('formats', 'geography', 'vacancy_languages'):
            value = getattr(body, key)
            if value is not None:
                existing[key] = value
        existing['candidate_filter_version'] = 1
    return {
        **existing,
        'profile_contract': PROFILE_CONTRACT,
        'seniority': body.seniority,
        'stacks': _requested_stacks(body, current),
        'required_skills': body.required_skills,
        'excluded_skills': body.excluded_skills,
        'delivery_mode': body.delivery_mode,
    }


def _requested_stacks(body: ProfileInput, current: dict | None = None) -> list[str]:
    return (
        body.stacks
        if 'stacks' in body.model_fields_set
        else list((current or {}).get('stacks', []))
    )


def _profile_path(
    body: ProfileInput, current: dict | None = None
) -> tuple[str, str, str]:
    path = role_path(body.role_id)
    if path is None or path[0] != body.direction_id:
        raise HTTPException(422, 'Выберите роль в указанном направлении')
    try:
        validate_profile_path(*path, stacks=_requested_stacks(body, current))
    except ValueError as exc:
        raise HTTPException(422, 'Выберите стек, подходящий роли') from exc
    return path


def _salary(analysis) -> str:
    low, high = analysis.salary_from, analysis.salary_to
    currency = analysis.currency if analysis.currency != NOT_SPECIFIED else ''
    if low is None and high is None:
        return 'По договорённости'
    numbers = [
        f'{int(value):,}'.replace(',', ' ')
        for value in (low, high)
        if value is not None
    ]
    prefix = (
        'от '
        if low is not None and high is None
        else 'до ' if high is not None and low is None else ''
    )
    return f'{prefix}{"–".join(numbers)} {currency}'.strip()


def _match_view(match: RegistryMatch, saved: set[str]) -> dict:
    analysis = match.analysis
    format_name = {'Remote': 'Удалённо', 'Hybrid': 'Гибрид', 'Office': 'Офис'}.get(
        analysis.work_format, analysis.work_format
    )
    location = ' · '.join(
        value
        for value in (analysis.city, analysis.country)
        if value and value != NOT_SPECIFIED
    )
    grade = analysis.grade_from if analysis.grade_from != NOT_SPECIFIED else ''
    return {
        'vacancy_id': match.vacancy_id,
        'title': analysis.title,
        'company': analysis.company,
        'summary': analysis.summary,
        'post_link': match.post_link,
        'published_at': match.published_at,
        'grade': grade,
        'salary': _salary(analysis),
        'location': location,
        'format': format_name if format_name != NOT_SPECIFIED else '',
        'tags': analysis.required_stack[:4],
        'saved': match.vacancy_id in saved,
    }


def _saved_view(vacancy) -> dict:
    return {
        'vacancy_id': vacancy.vacancy_id,
        'title': vacancy.title,
        'company': vacancy.company or '',
        'summary': vacancy.summary or '',
        'post_link': vacancy.post_link,
        'published_at': vacancy.published_at,
        'grade': '',
        'salary': 'По договорённости',
        'location': '',
        'format': '',
        'tags': [],
        'saved': True,
    }


@lru_cache(maxsize=8)
def _premium_store(path: str) -> PremiumSearchStore:
    return PremiumSearchStore(path)


def _run_view(run: dict, search: SearchStore, user_id: int) -> dict:
    results = search.list_results(run['id'], owner=f'candidate:{user_id}', limit=1)
    child = None
    premium_path = Path(search.path).parent / 'premium_search.sqlite3'
    if premium_path.exists():
        premium = _premium_store(str(premium_path))
        child = premium.find_request(f'candidate:{user_id}', 'search:' + run['id'])
    metrics = dict(child['metrics']) if child else {}
    if child:
        metrics['pending'] = premium.list_results(
            child['search_run_id'], limit=1, status='pending'
        )['total']
    return {
        'id': run['id'],
        'status': run['status'],
        'created_at': run['created_at'],
        'query': run['query'],
        'confirmed_count': results['total'],
        'source_states': run['source_states'],
        'phase': child['phase'] if child else None,
        'metrics': metrics,
        'error_reason': (child['error_reason'] if child else None)
        or run['source_states'].get('premium', {}).get('reason'),
    }


def _premium_item_view(item: dict, saved: set[str]) -> dict:
    vacancy_id = item.get('vacancy_id') or f"search-preview:{item['id']}"
    return {
        'id': item['id'],
        'vacancy_id': vacancy_id,
        'title': item.get('title') or 'Вакансия',
        'company': item.get('company') or '',
        'summary': item.get('summary') or '',
        'post_link': item.get('permalink') or '',
        'published_at': item.get('timestamp'),
        'grade': '',
        'salary': 'По договорённости',
        'location': '',
        'format': '',
        'tags': [],
        'saved': vacancy_id in saved,
    }


def install_routes(app, *, candidate_path: str, search_path: str) -> None:
    """Install same-origin routes without sharing admin cookies or privileges."""

    @lru_cache(maxsize=1)
    def candidates() -> CandidateStore:
        return CandidateStore(candidate_path)

    @lru_cache(maxsize=1)
    def registry() -> VacancyRegistry:
        return VacancyRegistry(candidate_path)

    @lru_cache(maxsize=1)
    def searches() -> SearchStore:
        return SearchStore(search_path)

    def allowed_users() -> set[int]:
        raw_allowed = os.getenv('CANDIDATE_BOT_ALLOWED_USER_IDS', '')
        return {
            int(value.strip())
            for value in raw_allowed.split(',')
            if value.strip().isdigit()
        }

    def identity(request: Request) -> dict:
        init_data = request.headers.get('X-Telegram-Init-Data')
        if init_data:
            return _telegram_user(
                init_data, os.getenv('CANDIDATE_BOT_TOKEN', ''), allowed_users()
            )
        session = candidates().web_session(request.cookies.get(WEB_SESSION_COOKIE))
        if session is None:
            raise HTTPException(401, 'Введите код из бота для входа на сайт')
        owner, csrf = session
        if owner not in allowed_users():
            raise HTTPException(403, 'Доступ к Candidate Bot ограничен')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not hmac.compare_digest(
            request.headers.get('X-Candidate-CSRF', ''), csrf
        ):
            raise HTTPException(403, 'Обновите страницу и повторите действие')
        return {'id': owner, 'first_name': 'Кандидат', 'username': ''}

    def user_id(user: dict = Depends(identity)) -> int:
        return user['id']

    @app.post(PREFIX + '/auth/code')
    def browser_code_login(
        body: BrowserCodeInput, request: Request, response: Response
    ):
        token = os.getenv('CANDIDATE_BOT_TOKEN', '')
        if not token:
            raise HTTPException(503, 'Вход через бота не настроен')
        client_key = request.client.host if request.client else 'unknown'
        if not candidates().allow_web_login_attempt(client_key):
            raise HTTPException(429, 'Слишком много попыток. Повторите через 10 минут')
        owner = candidates().redeem_web_login_code(body.code, token)
        if owner is None or owner not in allowed_users():
            raise HTTPException(401, 'Код неверный или срок его действия истёк')
        session_token, csrf = candidates().create_web_session(owner)
        secure = os.getenv('CANDIDATE_WEB_COOKIE_SECURE', 'true').lower() != 'false'
        response.set_cookie(
            WEB_SESSION_COOKIE,
            session_token,
            max_age=WEB_SESSION_AGE,
            httponly=True,
            secure=secure,
            samesite='strict',
            path=PREFIX,
        )
        response.headers['Cache-Control'] = 'no-store'
        return {'ok': True, 'csrf_token': csrf}

    @app.get(PREFIX + '/auth/session')
    def browser_session(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        session = candidates().web_session(request.cookies.get(WEB_SESSION_COOKIE))
        if session is None or session[0] not in allowed_users():
            return {'authenticated': False}
        return {'authenticated': True, 'csrf_token': session[1]}

    @app.post(PREFIX + '/auth/logout')
    def browser_logout(request: Request, response: Response, _: int = Depends(user_id)):
        candidates().revoke_web_session(request.cookies.get(WEB_SESSION_COOKIE))
        response.delete_cookie(WEB_SESSION_COOKIE, path=PREFIX)
        response.headers['Cache-Control'] = 'no-store'
        return {'ok': True}

    @app.get(PREFIX + '/me')
    def me(user: dict = Depends(identity)):
        profiles = candidates().list_profiles(user['id'])
        active = candidates().get_active_profile(user['id'])
        return {
            'user': user,
            'profiles': [_profile_view(profile) for profile in profiles],
            'active_profile_id': active.profile_id if active else None,
            'profile_state': candidates().get_active_profile_state(user['id']),
            'premium_enabled': catalog_search_allowed(f"candidate:{user['id']}"),
        }

    @app.post(PREFIX + '/profiles', status_code=201)
    def create_profile(body: ProfileInput, owner: int = Depends(user_id)):
        direction, specialization, role = _profile_path(body)
        try:
            profile = candidates().create_profile(
                owner,
                name=body.name,
                direction_id=direction,
                specialization_id=specialization,
                role_id=role,
                preferences=_preferences(body),
                is_active=not candidates().list_profiles(owner),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return _profile_view(profile)

    @app.put(PREFIX + '/profiles/{profile_id}')
    def update_profile(
        profile_id: str, body: ProfileUpdate, owner: int = Depends(user_id)
    ):
        current = candidates().get_profile(owner, profile_id)
        if current is None:
            raise HTTPException(404, 'Профиль не найден')
        if current.version != body.version:
            raise HTTPException(409, 'Профиль изменён. Обновите страницу.')
        direction, specialization, role = _profile_path(body, current.preferences)
        try:
            updated = candidates().update_profile(
                owner,
                profile_id,
                expected_version=body.version,
                name=body.name,
                direction_id=direction,
                specialization_id=specialization,
                role_id=role,
                preferences=_preferences(body, current.preferences),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if updated is None:
            raise HTTPException(409, 'Профиль изменён. Обновите страницу.')
        return _profile_view(updated)

    @app.post(PREFIX + '/profiles/{profile_id}/activate')
    def activate_profile(profile_id: str, owner: int = Depends(user_id)):
        selected = candidates().activate_profile(owner, profile_id)
        if selected is None:
            raise HTTPException(404, 'Профиль не найден или требует настройки')
        return _profile_view(selected)

    @app.delete(PREFIX + '/profiles/{profile_id}', status_code=204)
    def delete_profile(profile_id: str, owner: int = Depends(user_id)):
        if not candidates().delete_profile(owner, profile_id):
            raise HTTPException(404, 'Профиль не найден')
        return Response(status_code=204)

    @app.get(PREFIX + '/feed')
    def feed(
        bucket: Literal['all', 'new', 'older', 'undated'] = 'all',
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
        formats: list[Literal['remote', 'hybrid', 'office']] = Query(default=[]),
        geography: list[Literal['russia', 'europe', 'usa', 'cis', 'asia']] = Query(
            default=[]
        ),
        vacancy_languages: list[Literal['ru', 'en', 'other']] = Query(default=[]),
        owner: int = Depends(user_id),
    ):
        if candidates().get_active_profile(owner) is None:
            return {'items': [], 'total': 0, 'offset': offset, 'limit': limit}
        filters = {
            'candidate_filter_version': 1,
            'formats': formats,
            'geography': geography,
            'vacancy_languages': vacancy_languages,
        }
        filtered = bool(formats or geography or vacancy_languages)
        if bucket == 'all' and not filtered:
            matches = registry().list_personal_matches(
                owner, limit=limit, offset=offset
            )
            total = registry().count_personal_matches(owner)
        else:
            now = datetime.now(timezone.utc)
            recent = now - timedelta(hours=24)
            older = now - timedelta(days=candidates().get_older_days(owner))
            available = registry().list_personal_matches(owner)
            matches = []
            for match in available:
                if (
                    filtered
                    and match_candidate_filters(match.analysis, filters).status
                    == 'no_match'
                ):
                    continue
                if bucket == 'all':
                    matches.append(match)
                    continue
                published = candidates()._trusted_published_datetime(match.published_at)
                if (
                    (bucket == 'undated' and published is None)
                    or (
                        bucket == 'new'
                        and published is not None
                        and recent <= published <= now
                    )
                    or (
                        bucket == 'older'
                        and published is not None
                        and older <= published < recent
                    )
                ):
                    matches.append(match)
            total = len(matches)
            matches = matches[offset : offset + limit]
        saved = {item.vacancy_id for item in candidates().list_for_user(owner, 'saved')}
        return {
            'items': [_match_view(match, saved) for match in matches],
            'total': total,
            'offset': offset,
            'limit': limit,
        }

    @app.get(PREFIX + '/feed/settings')
    def feed_settings(owner: int = Depends(user_id)):
        return {
            'older_days': candidates().get_older_days(owner),
            'max_older_days': candidates().max_older_days(),
        }

    @app.put(PREFIX + '/feed/settings')
    def update_feed_settings(body: FeedSettings, owner: int = Depends(user_id)):
        if not candidates().set_older_days(owner, body.older_days):
            raise HTTPException(422, 'Недопустимый срок выдачи')
        return {
            'older_days': candidates().get_older_days(owner),
            'max_older_days': candidates().max_older_days(),
        }

    @app.get(PREFIX + '/saved')
    def saved(owner: int = Depends(user_id)):
        return {
            'items': [
                _saved_view(item) for item in candidates().list_for_user(owner, 'saved')
            ]
        }

    @app.get(PREFIX + '/vacancies/{vacancy_id}/text')
    def vacancy_source_text(vacancy_id: str, owner: int = Depends(user_id)):
        matches = registry().list_personal_matches(
            owner, vacancy_ids={vacancy_id}, limit=1
        )
        saved_ids = {
            item.vacancy_id for item in candidates().list_for_user(owner, 'saved')
        }
        if not matches and vacancy_id not in saved_ids:
            raise HTTPException(404, 'Вакансия недоступна')
        vacancy = registry().get(vacancy_id)
        return {'text': vacancy.raw_text if vacancy and vacancy.raw_text else None}

    @app.put(PREFIX + '/saved/{vacancy_id}')
    def save(vacancy_id: str, owner: int = Depends(user_id)):
        matches = registry().list_personal_matches(
            owner, vacancy_ids={vacancy_id}, limit=1
        )
        if not matches or not candidates().save_vacancy(owner, matches[0].callback_key):
            raise HTTPException(404, 'Вакансия не найдена в вашей ленте')
        return {'saved': True}

    @app.delete(PREFIX + '/saved/{vacancy_id}')
    def unsave(vacancy_id: str, owner: int = Depends(user_id)):
        if not candidates().unsave_vacancy(owner, vacancy_id):
            raise HTTPException(404, 'Сохранённая вакансия не найдена')
        return {'saved': False}

    @app.get(PREFIX + '/premium/templates')
    def premium_templates(owner: int = Depends(user_id)):
        profile = candidates().get_active_profile(owner)
        if profile is None or not catalog_search_allowed(f'candidate:{owner}'):
            return {'items': []}
        return {
            'items': [
                {
                    'id': template.id,
                    'label': template.label,
                    'query': compose_query(template.id, profile),
                }
                for template in list_templates(profile)
            ]
        }

    @app.post(PREFIX + '/premium/runs', status_code=202)
    def premium_start(body: StartPremium, owner: int = Depends(user_id)):
        profile = candidates().get_active_profile(owner)
        if profile is None:
            raise HTTPException(409, 'Сначала выберите активный профиль')
        if not catalog_search_allowed(f'candidate:{owner}'):
            raise HTTPException(403, 'Premium поиск недоступен')
        if body.template_id not in {item.id for item in list_templates(profile)}:
            raise HTTPException(422, 'Шаблон не подходит профилю')
        try:
            run = searches().create_run(
                query=compose_query(body.template_id, profile),
                sources=['premium'],
                owner=f'candidate:{owner}',
                track='catalog',
                mode='preview',
                result_limit=50,
                period_days=7,
                include_review=False,
                profile_snapshot=asdict(profile),
                client_request_id=body.client_request_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _run_view(run, searches(), owner)

    def owned_run(run_id: str, owner: int) -> dict:
        run = searches().get_run(run_id, f'candidate:{owner}')
        if run is None:
            raise HTTPException(404, 'Поиск не найден')
        return run

    @app.get(PREFIX + '/premium/runs')
    def premium_runs(owner: int = Depends(user_id)):
        with searches().connect() as connection:
            rows = connection.execute(
                'SELECT id FROM search_runs WHERE owner=? ORDER BY created_at DESC LIMIT 20',
                (f'candidate:{owner}',),
            ).fetchall()
        return {
            'items': [
                _run_view(owned_run(row['id'], owner), searches(), owner)
                for row in rows
            ]
        }

    @app.get(PREFIX + '/premium/runs/{run_id}')
    def premium_run(run_id: str, owner: int = Depends(user_id)):
        return _run_view(owned_run(run_id, owner), searches(), owner)

    @app.get(PREFIX + '/premium/runs/{run_id}/results')
    def premium_results(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
        owner: int = Depends(user_id),
    ):
        owned_run(run_id, owner)
        results = searches().list_results(
            run_id,
            owner=f'candidate:{owner}',
            offset=offset,
            limit=limit,
        )
        saved_ids = {
            item.vacancy_id for item in candidates().list_for_user(owner, 'saved')
        }
        return {
            'items': [_premium_item_view(item, saved_ids) for item in results['items']],
            'total': results['total'],
            'offset': offset,
            'limit': limit,
        }

    @app.post(PREFIX + '/premium/runs/{run_id}/cancel')
    def premium_cancel(run_id: str, owner: int = Depends(user_id)):
        owned_run(run_id, owner)
        return _run_view(
            searches().cancel(run_id, f'candidate:{owner}'), searches(), owner
        )

    @app.put(PREFIX + '/premium/runs/{run_id}/results/{item_id}/saved')
    def premium_save(run_id: str, item_id: str, owner: int = Depends(user_id)):
        owned_run(run_id, owner)
        results = searches().list_results(run_id, owner=f'candidate:{owner}', limit=100)
        item = next(
            (entry for entry in results['items'] if entry['id'] == item_id), None
        )
        if item is None or not item.get('permalink'):
            raise HTTPException(404, 'Результат не найден')
        vacancy = candidates().register_vacancy(
            vacancy_id=item.get('vacancy_id') or f"search-preview:{item['id']}",
            title=item.get('title') or 'Вакансия',
            company=item.get('company'),
            summary=item.get('summary'),
            post_link=item['permalink'],
            apply_link=None,
            published_at=item.get('timestamp') or None,
            go_visible=False,
            update_existing=False,
        )
        candidates().save_vacancy(owner, vacancy.callback_key)
        return {'saved': True}
