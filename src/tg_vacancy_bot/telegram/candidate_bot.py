"""Personal vacancy feed, profile wizard and Premium templates over Bot API."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from tg_vacancy_bot.candidate_catalog import CATALOG, validate_profile_path
from tg_vacancy_bot.premium_search.templates import compose_query, list_templates
from tg_vacancy_bot.telegram.candidate_card_formatter import (
    format_card,
    format_legacy_card,
)
from tg_vacancy_bot.telegram.candidate_delivery_worker import personal_card
from tg_vacancy_bot.telegram.candidate_search import CandidateSearch
from tg_vacancy_bot.telegram.candidate_store import (
    CandidateProfile,
    CandidateStore,
    CandidateVacancy,
)


class CandidateBotApi(Protocol):
    async def get_updates(self, offset: int | None, timeout: int) -> list[dict]: ...
    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict: ...
    async def answer_callback_query(
        self, callback_query_id: str, text: str
    ) -> None: ...
    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict: ...


PROFILE_STEPS = (
    'direction_id',
    'specialization_id',
    'role_id',
    'primary_language',
    'seniority',
    'formats',
    'geography',
    'preview',
)
STEP_LABELS = dict(
    zip(
        PROFILE_STEPS,
        (
            'Направление',
            'Специализация',
            'Роль',
            'Основной язык',
            'Грейд',
            'Формат',
            'География',
            'Предпросмотр',
        ),
    )
)
STEP_LABELS['name'] = 'Имя профиля'
STEP_LABELS['premium_template_id'] = 'Premium-шаблон'
STEP_LABELS.update(
    {
        'additional_languages': 'Дополнительные языки',
        'required_skills': 'Обязательные навыки',
        'desired_skills': 'Желаемые навыки',
        'excluded_skills': 'Исключения',
        'vacancy_languages': 'Язык текста вакансии',
        'legacy_stacks': 'Технологии из старого профиля',
    }
)
LANGUAGE_LABELS = {
    'go': 'Go',
    'python': 'Python',
    'java': 'Java',
    'javascript': 'JavaScript / TypeScript',
    'cpp': 'C++',
    'csharp': 'C#',
    'rust': 'Rust',
    'php': 'PHP',
    'kotlin': 'Kotlin',
    'swift': 'Swift',
}
ROLE_LABELS = {
    'backend_developer': ('Backend-разработчик', 'создаёт серверную часть и API'),
    'api_developer': ('Backend-разработчик', 'роль API включена в backend'),
    'frontend_developer': (
        'Frontend-разработчик',
        'создаёт интерфейсы сайтов и приложений',
    ),
    'mobile_developer': (
        'Мобильный разработчик',
        'создаёт приложения для iOS и Android',
    ),
    'embedded_developer': (
        'Разработчик встроенных систем',
        'пишет код для устройств и оборудования',
    ),
    'manual_qa_engineer': ('Инженер по тестированию', 'проверяет продукт вручную'),
    'automation_qa_engineer': ('Инженер автоматизации тестирования', 'пишет автотесты'),
    'data_engineer': ('Инженер данных', 'строит системы обработки данных'),
    'data_scientist': (
        'Специалист по Data Science',
        'анализирует данные и строит модели',
    ),
    'ml_engineer': ('Инженер машинного обучения', 'внедряет ML-модели в продукты'),
    'product_analyst': (
        'Продуктовый аналитик',
        'изучает метрики и поведение пользователей',
    ),
    'business_analyst': ('Бизнес-аналитик', 'описывает процессы и требования'),
    'product_designer': ('Продуктовый дизайнер', 'проектирует интерфейсы и сценарии'),
    'graphic_designer': ('Графический дизайнер', 'создаёт визуальные материалы'),
    'devops_engineer': ('DevOps-инженер', 'автоматизирует инфраструктуру и поставку'),
    'systems_administrator': ('Системный администратор', 'поддерживает серверы и сети'),
    'sre_engineer': ('Инженер надёжности (SRE)', 'обеспечивает стабильность сервисов'),
    'appsec_engineer': (
        'Инженер безопасности приложений',
        'защищает программные продукты',
    ),
    'security_analyst': ('Аналитик кибербезопасности', 'выявляет и разбирает угрозы'),
    'product_manager': ('Менеджер продукта', 'развивает продукт и его показатели'),
    'project_manager': ('Менеджер проектов', 'планирует сроки и работу команды'),
    'engineering_manager': (
        'Руководитель разработки',
        'развивает команду и инженерный процесс',
    ),
    'it_recruiter': ('IT-рекрутер', 'ищет и отбирает специалистов'),
    'hr_manager': ('Менеджер по персоналу', 'развивает процессы работы с людьми'),
    'digital_marketer': (
        'Интернет-маркетолог',
        'привлекает аудиторию через цифровые каналы',
    ),
    'content_manager': ('Контент-менеджер', 'планирует и выпускает материалы'),
}
SENIORITY_LABELS = {
    'intern': 'Стажёр',
    'junior': 'Начинающий',
    'middle': 'Средний',
    'senior': 'Опытный',
    'lead': 'Ведущий',
}
FORMAT_LABELS = {'remote': 'Удалённо', 'hybrid': 'Гибрид', 'office': 'В офисе'}
SPECIALIZATION_LABELS = {
    'backend': 'Серверная разработка (backend)',
    'frontend': 'Клиентская разработка (frontend)',
    'mobile': 'Мобильные приложения',
    'embedded': 'Встроенные системы',
    'manual_qa': 'Ручное тестирование',
    'automation_qa': 'Автоматизация тестирования',
    'data_engineering': 'Инженерия данных',
    'data_science': 'Анализ данных (Data Science)',
    'machine_learning': 'Машинное обучение',
    'product_analytics': 'Продуктовая аналитика',
    'business_analytics': 'Бизнес-анализ',
    'product_design': 'Продуктовый дизайн',
    'graphic_design': 'Графический дизайн',
    'devops': 'DevOps и инфраструктура',
    'systems_admin': 'Системное администрирование',
    'sre': 'Надёжность сервисов (SRE)',
    'application_security': 'Безопасность приложений',
    'security_operations': 'Операционная безопасность',
    'product_management': 'Управление продуктом',
    'project_management': 'Управление проектами',
    'engineering_management': 'Управление разработкой',
    'recruiting': 'Подбор персонала',
    'hr_management': 'Управление персоналом',
    'digital_marketing': 'Интернет-маркетинг',
    'content': 'Контент и редактура',
}
GEOGRAPHY_CHOICES = [
    ('worldwide', 'Весь мир'),
    ('Россия', 'Россия'),
    ('Казахстан', 'Казахстан'),
    ('Беларусь', 'Беларусь'),
    ('ЕС', 'ЕС'),
    ('__custom__', 'Другая страна'),
]


def personal_keyboard(callback_key: str) -> dict:
    return {
        'inline_keyboard': [
            [
                {'text': 'Сохранить', 'callback_data': f'v:s:{callback_key}'},
            ]
        ]
    }


def vacancy_text(vacancy: CandidateVacancy) -> str:
    body = format_legacy_card(vacancy, now=datetime.now(timezone.utc))
    published = None
    if vacancy.published_at:
        try:
            published = datetime.fromisoformat(
                vacancy.published_at.replace('Z', '+00:00')
            )
        except ValueError:
            pass
    if published is None or published.tzinfo is None:
        body = (
            body.replace(vacancy.published_at or '', 'Дата публикации неизвестна')
            if vacancy.published_at
            else body
        )
        return '<i>Дата публикации неизвестна</i>\n' + body
    return body


LANGUAGE_STACKS = {
    'go': {'go'},
    'python': {'python'},
    'java': {'java'},
    'javascript': {'javascript', 'typescript', 'nodejs'},
    'cpp': {'cpp', 'c++'},
    'csharp': {'csharp', 'c#'},
    'rust': {'rust'},
    'php': {'php'},
    'kotlin': {'kotlin'},
    'swift': {'swift'},
}


def _role_for_values(values: dict) -> dict | None:
    for direction in CATALOG['directions']:
        if direction['id'] != values.get('direction_id'):
            continue
        for specialization in direction['specializations']:
            if specialization['id'] != values.get('specialization_id'):
                continue
            return next(
                (
                    role
                    for role in specialization['roles']
                    if role['id'] == values.get('role_id')
                ),
                None,
            )
    return None


def _language_choices(values: dict) -> list[tuple[str, str]]:
    role = _role_for_values(values)
    if not role:
        return []
    available = set(role['stacks'])
    return [
        (language, label)
        for language, label in LANGUAGE_LABELS.items()
        if available & LANGUAGE_STACKS[language]
    ]


def _profile_name(values: dict) -> str:
    label = ROLE_LABELS.get(
        values.get('role_id'), (values.get('role_id', 'Профиль'), '')
    )[0]
    language = values.get('preferences', {}).get('primary_language')
    if language and values.get('direction_id') in {'development', 'qa', 'data_ai'}:
        return (
            f'{LANGUAGE_LABELS.get(language, language)} {label[:1].lower() + label[1:]}'
        )
    return label


def _days_label(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        word = 'день'
    elif days % 10 in {2, 3, 4} and days % 100 not in {12, 13, 14}:
        word = 'дня'
    else:
        word = 'дней'
    return f'{days} {word}'


class CandidateBot:
    """Routes personal feed, profiles and templates for an allowlisted user."""

    def __init__(
        self,
        api: CandidateBotApi,
        store: CandidateStore,
        allowed_user_ids: set[int],
        *,
        registry=None,
        profile_feed_enabled: bool = False,
        profile_allowed_user_ids: set[int] | None = None,
        search: CandidateSearch | None = None,
    ) -> None:
        self.api = api
        self.store = store
        self.allowed_user_ids = allowed_user_ids
        self.registry = registry
        self.profile_feed_enabled = profile_feed_enabled
        self.profile_allowed_user_ids = profile_allowed_user_ids or set()
        self.search = search

    def _keyboard(self, user_id: int) -> dict:
        days = self.store.get_older_days(user_id)
        first_row = ['Новые · 24 часа', f'Ранее · за {days} дней']
        second_row = ['Сохранённые', 'Без даты']
        if self.profile_feed_enabled and user_id in self.profile_allowed_user_ids:
            second_row.append('Для меня')
        return {
            'keyboard': [first_row, second_row, ['Профили']],
            'resize_keyboard': True,
        }

    async def _personal_feed(self, user_id: int) -> None:
        await self._show_browser(user_id, 'for_me')

    async def _show_age_choices(self, user_id: int) -> None:
        rows = [
            [{'text': _days_label(days), 'callback_data': f'f:days:{days}'}]
            for days in (3, 7, 14, 30)
        ]
        rows.append([{'text': 'Ввести свой срок', 'callback_data': 'f:custom'}])
        await self.api.send_message(
            user_id,
            f"Выберите срок. «Ранее» покажет объявления старше 24 часов и не старше выбранного срока. Сейчас доступно от 2 до {self.store.max_older_days()} дней — по самой старой дате в ленте.",
            reply_markup={'inline_keyboard': rows},
        )

    async def _show_browser(
        self, user_id: int, bucket: str, index: int = 0, message_id: int | None = None
    ) -> bool:
        """Show one current card; navigation edits only the owner's private message."""
        if bucket == 'for_me' and (
            not self.profile_feed_enabled
            or user_id not in self.profile_allowed_user_ids
            or self.registry is None
        ):
            if message_id is None:
                await self.api.send_message(
                    user_id, 'Персональная выдача пока недоступна.'
                )
            return False
        if bucket == 'for_me':
            matches = await asyncio.to_thread(
                self.registry.list_personal_matches, user_id
            )
            cards = [
                (
                    format_card(
                        personal_card(match),
                        now=datetime.now(timezone.utc),
                        timezone=min(
                            match.matched_profiles, key=lambda p: p.profile_id
                        ).preferences.get('timezone', 'Europe/Moscow'),
                    ),
                    match.callback_key,
                )
                for match in matches
            ]
        elif bucket in {'new', 'older', 'undated', 'saved'}:
            days = self.store.get_older_days(user_id)
            cards = [
                (vacancy_text(vacancy), vacancy.callback_key)
                for vacancy in self.store.list_for_user(
                    user_id, bucket, older_days=days
                )
            ]
        else:
            return False
        if not cards:
            if message_id is not None:
                return False
            await self.api.send_message(
                user_id,
                (
                    'Подтверждённых совпадений активных профилей пока нет.'
                    if bucket == 'for_me'
                    else {
                        'new': 'За последние 24 часа вакансий с подтверждённой датой публикации нет. Посмотрите более ранние или объявления без даты.',
                        'older': f"За период от 24 часов до {_days_label(self.store.get_older_days(user_id))} вакансий нет. Можно выбрать другой срок или проверить «Новые».",
                        'undated': 'Вакансий без достоверной даты публикации пока нет. Такие объявления не попадают в «Новые».',
                        'saved': 'Сохранённых вакансий пока нет. Сохраняйте интересные карточки кнопкой «Сохранить».',
                    }[bucket]
                ),
                reply_markup=self._keyboard(user_id),
            )
            return True
        if index < 0 or index >= len(cards):
            return False
        content, callback_key = cards[index]
        content = f'<i>{index + 1} / {len(cards)}</i>\n\n{content}'
        keyboard = personal_keyboard(callback_key)
        if len(cards) > 1:
            controls = []
            if index > 0:
                controls.append(
                    {'text': '←', 'callback_data': f'b:{bucket}:{index - 1}'}
                )
            if index + 1 < len(cards):
                controls.append(
                    {'text': '→', 'callback_data': f'b:{bucket}:{index + 1}'}
                )
            keyboard['inline_keyboard'].append(controls)
        if message_id is None:
            await self.api.send_message(
                user_id, content, parse_mode='HTML', reply_markup=keyboard
            )
        else:
            await self.api.edit_message_text(
                user_id, message_id, content, parse_mode='HTML', reply_markup=keyboard
            )
        return True

    async def run(self, shutdown_event: asyncio.Event) -> None:
        offset: int | None = None
        while not shutdown_event.is_set():
            try:
                for update in await self.api.get_updates(offset, timeout=25):
                    update_id = update.get('update_id')
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    await self.handle_update(update)
            except Exception:
                logging.exception('Ошибка long polling пользовательского бота')
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    continue

    async def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get('message'), dict):
            await self._handle_message(update['message'])
        elif isinstance(update.get('callback_query'), dict):
            await self._handle_callback(update['callback_query'])

    async def _handle_message(self, message: dict[str, Any]) -> None:
        sender, chat = message.get('from') or {}, message.get('chat') or {}
        user_id, chat_id = sender.get('id'), chat.get('id')
        if (
            not isinstance(user_id, int)
            or not isinstance(chat_id, int)
            or chat.get('type') != 'private'
        ):
            return
        if user_id not in self.allowed_user_ids:
            await self.api.send_message(chat_id, 'Доступ к beta-боту ограничен.')
            return
        text = (message.get('text') or '').strip()
        if text in {'/start', '/help'}:
            await self.api.send_message(
                chat_id,
                'Выберите период ленты. Даты берутся из публикации источника; без даты — отдельный раздел.',
                reply_markup=self._keyboard(user_id),
            )
            return
        if text in {'Для меня', '/forme'}:
            await self._personal_feed(user_id)
            return
        if text in {'Профили', '/profiles'}:
            await self._profiles(user_id)
            return
        if text in {'Отмена', '/cancel'} and self.store.get_profile_draft(user_id):
            draft = self.store.get_profile_draft(user_id)
            self.store.finish_profile_draft(
                user_id, draft['token'], draft['revision'], save=False
            )
            await self.api.send_message(user_id, 'Настройка отменена.')
            return
        if text.startswith('Ранее'):
            await self._show_age_choices(user_id)
            return
        if text == 'Новые · 24 часа':
            await self._show_browser(user_id, 'new')
            return
        if text == 'Без даты':
            await self._show_browser(user_id, 'undated')
            return
        if text in {'Сохранённые', '/saved'}:
            await self._show_browser(user_id, 'saved')
            return
        if self.store.take_custom_days_request(user_id):
            try:
                days = int(text)
            except ValueError:
                days = -1
            if self.store.set_older_days(user_id, days):
                await self.api.send_message(
                    user_id,
                    f'Срок изменён: {_days_label(days)}.',
                    reply_markup=self._keyboard(user_id),
                )
                await self._show_browser(user_id, 'older')
            else:
                self.store.request_custom_days(user_id)
                await self.api.send_message(
                    user_id,
                    f"Введите целое число от 2 до {self.store.max_older_days()} дней.",
                )
            return
        if self.store.get_profile_draft(user_id) and text not in {
            'Новые · 24 часа',
            'Сохранённые',
            '/new',
            '/saved',
        }:
            await self._draft_text(user_id, text)
            return
        bucket = {
            'Новые': 'new',
            '/new': 'new',
        }.get(text)
        if bucket is None:
            await self.api.send_message(
                chat_id,
                'Используйте «Новые», «Сохранённые» или «Профили».',
                reply_markup=self._keyboard(user_id),
            )
            return
        await self._show_browser(user_id, bucket)

    async def _can_save(self, user_id: int, key: str) -> bool:
        with self.store._connect() as connection:
            row = connection.execute(
                'SELECT v.vacancy_id FROM vacancies v WHERE v.callback_key=? AND '
                '(v.go_visible=1 OR EXISTS(SELECT 1 FROM user_saved_vacancies s '
                'WHERE s.vacancy_id=v.vacancy_id AND s.telegram_user_id=?))',
                (key, user_id),
            ).fetchone()
        if row:
            return True
        if (
            self.profile_feed_enabled
            and user_id in self.profile_allowed_user_ids
            and self.registry is not None
        ):
            matches = await asyncio.to_thread(
                self.registry.list_personal_matches, user_id
            )
            return any(match.callback_key == key for match in matches)
        return False

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        sender = callback.get('from') or {}
        user_id, callback_id = sender.get('id'), callback.get('id')
        if not isinstance(user_id, int) or not isinstance(callback_id, str):
            return
        if user_id not in self.allowed_user_ids:
            await self.api.answer_callback_query(callback_id, 'Доступ ограничен.')
            return
        data = callback.get('data') or ''
        if not isinstance(data, str):
            return
        parts = data.split(':')
        if len(parts) == 3 and parts[0] == 'b':
            source = callback.get('message') or {}
            chat = source.get('chat') or {}
            try:
                index = int(parts[2])
            except ValueError:
                index = -1
            valid = (
                chat.get('type') == 'private'
                and chat.get('id') == user_id
                and isinstance(source.get('message_id'), int)
                and await self._show_browser(
                    user_id, parts[1], index, source['message_id']
                )
            )
            await self.api.answer_callback_query(
                callback_id, 'Готово.' if valid else 'Кнопка устарела или недоступна.'
            )
            return
        if parts[0] == 'cs' and self.search is not None:
            valid = await self.search.callback(user_id, parts)
            await self.api.answer_callback_query(
                callback_id, 'Готово.' if valid else 'Кнопка устарела или недоступна.'
            )
            return
        if len(parts) == 3 and parts[0] == 'f' and parts[1] == 'days':
            try:
                days = int(parts[2])
            except ValueError:
                days = -1
            valid = self.store.set_older_days(user_id, days)
            await self.api.answer_callback_query(
                callback_id, 'Срок сохранён.' if valid else 'Недопустимый срок.'
            )
            if valid:
                await self.api.send_message(
                    user_id,
                    f'Срок для «Ранее» сохранён: {_days_label(days)}.',
                    reply_markup=self._keyboard(user_id),
                )
                await self._show_browser(user_id, 'older')
            return
        if parts == ['f', 'custom']:
            self.store.request_custom_days(user_id)
            await self.api.answer_callback_query(callback_id, 'Введите число дней.')
            await self.api.send_message(
                user_id,
                f"Введите срок от 2 до {self.store.max_older_days()} дней. Значение сохранится для следующих запусков.",
            )
            return
        if parts[0] in {'p', 'w'}:
            try:
                valid = await self._profile_callback(user_id, parts)
            except (ValueError, KeyError, TypeError):
                valid = False
            await self.api.answer_callback_query(
                callback_id, 'Готово.' if valid else 'Кнопка устарела или недоступна.'
            )
            return
        # Legacy status, search, and complaint callback payloads are deliberately
        # unsupported and can never write to personal data.
        if len(parts) == 3 and parts[0] == 'v' and parts[1] == 's':
            saved = await self._can_save(user_id, parts[2]) and self.store.save_vacancy(
                user_id, parts[2]
            )
            await self.api.answer_callback_query(
                callback_id, 'Сохранено.' if saved else 'Вакансия недоступна.'
            )
            return
        await self.api.answer_callback_query(
            callback_id, 'Это действие больше не поддерживается.'
        )

    def _choices(self, draft: dict) -> list[tuple[str, str]]:
        step, values = draft['step'], draft['values']
        directions = CATALOG['directions']
        if step == 'direction_id':
            labels = {
                'development': 'Разработка',
                'qa': 'Тестирование',
                'data_ai': 'Данные и искусственный интеллект',
                'analytics': 'Аналитика',
                'design': 'Дизайн',
                'devops_admin': 'DevOps и системное администрирование',
                'security': 'Информационная безопасность',
                'management': 'Менеджмент',
                'hr_recruiting': 'HR и подбор',
                'marketing_content': 'Маркетинг и контент',
            }
            return [
                (d['id'], labels.get(d['id'], d['synonyms'][0])) for d in directions
            ]
        elif step == 'specialization_id':
            items = next(d for d in directions if d['id'] == values['direction_id'])[
                'specializations'
            ]
        elif step in {'role_id', 'primary_language', 'additional_languages'}:
            specs = next(d for d in directions if d['id'] == values['direction_id'])[
                'specializations'
            ]
            roles = next(s for s in specs if s['id'] == values['specialization_id'])[
                'roles'
            ]
            if step == 'primary_language':
                return _language_choices(values)
            if step == 'additional_languages':
                main = values.get('preferences', {}).get('primary_language')
                return [
                    choice for choice in _language_choices(values) if choice[0] != main
                ]
            roles = [r for r in roles if r['id'] != 'api_developer']
            items = roles
        elif step == 'legacy_stacks':
            role = _role_for_values(values)
            return [(value, value) for value in (role or {}).get('stacks', [])]
        elif step == 'geography':
            return GEOGRAPHY_CHOICES
        else:
            return {
                'seniority': [(k, SENIORITY_LABELS[k]) for k in SENIORITY_LABELS],
                'formats': [(k, FORMAT_LABELS[k]) for k in FORMAT_LABELS],
                'vacancy_languages': [('ru', 'Русский'), ('en', 'Английский')],
            }.get(step, [])
        if step == 'role_id':
            return [
                (
                    item['id'],
                    ROLE_LABELS.get(item['id'], (item['synonyms'][0], ''))[0]
                    + (
                        ' — ' + ROLE_LABELS[item['id']][1]
                        if ROLE_LABELS.get(item['id'], ('', ''))[1]
                        else ''
                    ),
                )
                for item in items
            ]
        if step == 'specialization_id':
            return [
                (item['id'], SPECIALIZATION_LABELS.get(item['id'], item['synonyms'][0]))
                for item in items
            ]
        return [(x['id'], x['synonyms'][0]) for x in items]

    @staticmethod
    def _profile_text(values: dict) -> str:
        prefs = values.get('preferences', {})
        role = ROLE_LABELS.get(values.get('role_id'), (values.get('role_id', '—'), ''))
        direction = next(
            (
                item['synonyms'][0]
                for item in CATALOG['directions']
                if item['id'] == values.get('direction_id')
            ),
            '—',
        )
        specialization = next(
            (
                item['synonyms'][0]
                for d in CATALOG['directions']
                if d['id'] == values.get('direction_id')
                for item in d['specializations']
                if item['id'] == values.get('specialization_id')
            ),
            '—',
        )
        lines = [
            f"Профиль: {_profile_name(values)}",
            f'Направление: {direction}',
            f'Специализация: {specialization}',
            f'Роль: {role[0]} — {role[1]}' if role[1] else f'Роль: {role[0]}',
        ]
        if prefs.get('primary_language'):
            lines.append(
                f"Основной язык: {LANGUAGE_LABELS.get(prefs['primary_language'], prefs['primary_language'])}"
            )
        lines.extend(
            f"{STEP_LABELS.get(key, key)}: {', '.join(LANGUAGE_LABELS.get(item, item) for item in value) if isinstance(value, list) else value}"
            for key, value in prefs.items()
            if key
            not in {
                'timezone',
                'delivery_mode',
                'premium_template_id',
                'primary_language',
                'stacks',
            }
            and value
        )
        if prefs.get('stacks'):
            lines.append(
                'Технологии из прежних настроек: ' + ', '.join(prefs['stacks'])
            )
        return '\n'.join(lines)[:3500]

    async def _profiles(self, user_id: int) -> None:
        rows = [
            [
                {
                    'text': f'{"✓" if p.is_active else "○"} {p.name}',
                    'callback_data': f'p:view:{p.profile_id}:{p.version}',
                }
            ]
            for p in self.store.list_profiles(user_id)
        ]
        rows.append([{'text': 'Создать профиль', 'callback_data': 'p:new'}])
        if self.store.get_profile_draft(user_id):
            rows.append([{'text': 'Продолжить черновик', 'callback_data': 'p:resume'}])
        await self.api.send_message(
            user_id, 'Профили', reply_markup={'inline_keyboard': rows}
        )

    async def _show_profile(self, user_id: int, profile: CandidateProfile) -> None:
        rows = [
            [
                {
                    'text': label,
                    'callback_data': f'p:{action}:{profile.profile_id}:{profile.version}',
                }
            ]
            for action, label in [
                ('edit', 'Редактировать'),
                ('advanced', 'Дополнительные настройки'),
                ('toggle', 'Выключить' if profile.is_active else 'Включить'),
                ('templates', 'Premium-шаблоны'),
                ('search', 'Искать'),
            ]
        ]
        await self.api.send_message(
            user_id,
            self._profile_text(vars(profile)),
            reply_markup={'inline_keyboard': rows},
        )

    async def _start_draft(
        self,
        user_id: int,
        profile: CandidateProfile | None = None,
        *,
        wizard: str = 'basic',
    ) -> None:
        if not self.store.get_profile_draft(user_id):
            values = (
                {
                    k: getattr(profile, k)
                    for k in (
                        'name',
                        'direction_id',
                        'specialization_id',
                        'role_id',
                        'preferences',
                        'is_active',
                    )
                }
                if profile
                else {
                    'name': '',
                    'preferences': {
                        'delivery_mode': 'manual',
                        'timezone': 'Europe/Moscow',
                    },
                    'is_active': True,
                }
            )
            self.store.put_profile_draft(
                user_id,
                {
                    'token': uuid.uuid4().hex[:12],
                    'revision': 0,
                    'step': (
                        self._advanced_steps(values)[0]
                        if wizard == 'advanced'
                        else PROFILE_STEPS[0]
                    ),
                    'wizard': wizard,
                    'values': values,
                    'profile_id': profile.profile_id if profile else None,
                    'profile_version': profile.version if profile else None,
                },
            )
        elif wizard == 'advanced':
            draft = self.store.get_profile_draft(user_id)
            draft['wizard'] = 'advanced'
            draft['step'] = self._advanced_steps(draft['values'])[0]
            revision = draft['revision']
            draft['revision'] += 1
            self.store.put_profile_draft(user_id, draft, revision)
        await self._show_draft(user_id)

    @staticmethod
    def _advanced_steps(values: dict) -> list[str]:
        choices = _language_choices(values)
        main = values.get('preferences', {}).get('primary_language')
        choices = [choice for choice in choices if choice[0] != main]
        prefix = ['additional_languages'] if choices else []
        if values.get('preferences', {}).get('stacks'):
            prefix.append('legacy_stacks')
        return [
            *prefix,
            'required_skills',
            'excluded_skills',
            'vacancy_languages',
            'preview',
        ]

    @staticmethod
    def _basic_steps(values: dict) -> list[str]:
        prefix = ['direction_id', 'specialization_id', 'role_id']
        if _language_choices(values):
            prefix.append('primary_language')
        return [*prefix, 'seniority', 'formats', 'geography', 'preview']

    async def _show_draft(self, user_id: int) -> None:
        draft = self.store.get_profile_draft(user_id)
        if draft is None:
            await self._profiles(user_id)
            return
        step = draft['step']
        prefix = f'w:{draft["token"]}:{draft["revision"]}:'

        def button(label, action):
            return {'text': label, 'callback_data': prefix + action}

        rows = []
        if step == 'preview':
            text = 'Проверьте профиль перед сохранением:\n\n' + self._profile_text(
                draft['values']
            )
            rows = [
                [button('Сохранить профиль', 'save')],
                [button('Дополнительные настройки', 'advanced')],
            ]
        else:
            hints = {
                'direction_id': 'Выберите направление. Например: разработка или аналитика.',
                'specialization_id': 'Уточните область. Например: backend или мобильная разработка.',
                'role_id': 'Выберите понятную роль. Например: backend-разработчик.',
                'primary_language': 'Какой язык для вас основной? Например: Go.',
                'seniority': 'Выберите комфортный уровень. Например: средний (middle).',
                'formats': 'Где готовы работать? Например: удалённо или в офисе.',
                'geography': 'Где ищете работу? Можно выбрать несколько стран или регионов.',
                'additional_languages': 'Можно добавить языки помимо основного. Например: Python.',
                'required_skills': self._required_skills_hint(draft['values']),
                'excluded_skills': 'Какие технологии или условия исключить? Введите через запятую или пропустите.',
                'vacancy_languages': 'Русский, английский или любой; это не язык программирования.',
                'legacy_stacks': 'Технологии из старых настроек. Это может быть Docker, PostgreSQL или язык; список уже участвует в matching.',
            }
            text = f"{STEP_LABELS.get(step, step)}\n{hints.get(step, 'Выберите подходящее или пропустите.')}"
            choices = self._choices(draft)
            selected = draft['values']['preferences'].get(
                ('stacks' if step == 'legacy_stacks' else step),
                draft['values'].get(step, []),
            )
            rows = [
                [button(('✓ ' if key in selected else '') + label, f'choose:{i}')]
                for i, (key, label) in enumerate(choices)
            ]
            if step not in {'direction_id', 'specialization_id', 'role_id'}:
                if step in {'required_skills', 'excluded_skills'}:
                    rows.append([button('Ввести через запятую', 'input')])
                if step in {'geography', 'additional_languages', 'legacy_stacks'}:
                    rows.append([button('Готово', 'next')])
                elif step in {'seniority', 'formats', 'vacancy_languages'} and choices:
                    rows.append([button('Пропустить', 'skip')])
                rows.append([button('Пропустить', 'skip')])
                if step == 'primary_language' and not choices:
                    text = 'Для этой роли основной язык не требуется. Можно продолжить.'
                    rows.append([button('Продолжить', 'next')])
            if selected:
                selected_labels = (
                    [
                        dict(choices).get(item, LANGUAGE_LABELS.get(item, item))
                        for item in selected
                        if isinstance(selected, list)
                    ]
                    if isinstance(selected, list)
                    else [dict(choices).get(selected, selected)]
                )
                text += f"\nВыбрано: {', '.join(selected_labels)}"
        order = (
            self._advanced_steps(draft['values'])
            if draft.get('wizard') == 'advanced'
            else self._basic_steps(draft['values'])
        )
        if step != order[0]:
            rows.append([button('Назад', 'back')])
        rows.append([button('Отмена', 'cancel')])
        await self.api.send_message(
            user_id, text, reply_markup={'inline_keyboard': rows}
        )

    @staticmethod
    def _required_skills_hint(values: dict) -> str:
        examples = {
            'go': 'PostgreSQL, gRPC, Docker',
            'python': 'Django или FastAPI, PostgreSQL',
            'java': 'Spring, PostgreSQL, Kafka',
            'javascript': 'TypeScript, React, Node.js',
            'cpp': 'CMake, Linux, embedded',
            'csharp': '.NET, ASP.NET, SQL Server',
            'rust': 'Tokio, PostgreSQL, Linux',
            'php': 'Laravel, PostgreSQL, Redis',
            'kotlin': 'Ktor, Spring, PostgreSQL',
            'swift': 'SwiftUI, UIKit, iOS',
        }
        language = values.get('preferences', {}).get('primary_language')
        example = examples.get(language, 'например, SQL или Docker')
        return f"Необязательно. Пример для ориентира: {example}. Это не добавит навыки автоматически; введите свои или пропустите."

    def _set_draft_value(self, draft: dict, value: str | list[str]) -> None:
        step, values = draft['step'], draft['values']
        if step in PROFILE_STEPS[:3]:
            if values.get(step) != value:
                index = PROFILE_STEPS.index(step)
                for key in PROFILE_STEPS[index + 1 : 3]:
                    values.pop(key, None)
                values['preferences']['stacks'] = []
                values['preferences'].pop('primary_language', None)
                values['preferences'].pop('additional_languages', None)
                values['preferences'].pop('premium_template_id', None)
                values['name'] = ''
            values[step] = value
            if step == 'role_id' and not values['name']:
                values['name'] = dict(self._choices(draft))[value]
        elif step == 'name':
            if not value or not str(value).strip():
                raise ValueError('Имя не может быть пустым.')
            values['name'] = value
        elif step == 'primary_language':
            preferences = {**values['preferences'], step: value}
            values['preferences'] = self.store._validate_preferences(preferences)
        else:
            if step == 'geography' and isinstance(value, str):
                selected = list(values['preferences'].get('geography', []))
                if value == 'worldwide':
                    value = ['worldwide']
                else:
                    selected = [
                        item
                        for item in selected
                        if item not in {'worldwide', '__custom__'}
                    ]
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.append(value)
                    value = selected
            if isinstance(value, str):
                value = [value]
            pref_step = 'stacks' if step == 'legacy_stacks' else step
            preferences = {**values['preferences'], pref_step: value}
            if pref_step == 'stacks':
                validate_profile_path(
                    values['direction_id'],
                    values['specialization_id'],
                    values['role_id'],
                    value,
                )
                self.store._clear_invalid_template(values['role_id'], preferences)
            values['preferences'] = self.store._validate_preferences(preferences)

    def _advance(self, draft: dict) -> None:
        steps = (
            self._advanced_steps(draft['values'])
            if draft.get('wizard') == 'advanced'
            else self._basic_steps(draft['values'])
        )
        draft['step'] = steps[min(steps.index(draft['step']) + 1, len(steps) - 1)]

    def _back(self, draft: dict) -> None:
        steps = (
            self._advanced_steps(draft['values'])
            if draft.get('wizard') == 'advanced'
            else self._basic_steps(draft['values'])
        )
        draft['step'] = steps[max(steps.index(draft['step']) - 1, 0)]

    async def _draft_text(self, user_id: int, text: str) -> None:
        draft = self.store.get_profile_draft(user_id)
        step = draft['step']
        try:
            if step in {'direction_id', 'specialization_id', 'role_id', 'preview'}:
                raise ValueError('Используйте кнопки текущего шага.')
            if step == 'geography' and '__custom__' in draft['values'][
                'preferences'
            ].get('geography', []):
                geo = [
                    x
                    for x in draft['values']['preferences']['geography']
                    if x not in {'__custom__', 'worldwide'}
                ]
                self._set_draft_value(draft, geo + [text.strip()])
            else:
                value = (
                    text
                    if step in {'name', 'timezone', 'delivery_mode'}
                    else [x.strip() for x in text.split(',') if x.strip()]
                )
                self._set_draft_value(draft, value)
                self._advance(draft)
            revision = draft['revision']
            draft['revision'] += 1
            self.store.put_profile_draft(user_id, draft, revision)
        except ValueError as error:
            await self.api.send_message(user_id, str(error))
        await self._show_draft(user_id)

    async def _profile_callback(self, user_id: int, parts: list[str]) -> bool:
        if parts == ['p', 'new']:
            await self._start_draft(user_id)
            return True
        if parts == ['p', 'resume']:
            await self._show_draft(user_id)
            return True
        if parts[0] == 'w':
            draft = self.store.get_profile_draft(user_id)
            if (
                len(parts) < 4
                or draft is None
                or parts[1] != draft['token']
                or parts[2] != str(draft['revision'])
            ):
                return False
            action, step = parts[3], draft['step']
            revision = draft['revision']
            if len(parts) == 4 and (
                action == 'cancel'
                or (step == 'preview' and action in {'save', 'advanced'})
            ):
                if action == 'advanced' and step == 'preview':
                    draft['wizard'] = 'advanced'
                    draft['step'] = self._advanced_steps(draft['values'])[0]
                    draft['revision'] += 1
                    if not self.store.put_profile_draft(user_id, draft, revision):
                        return False
                    await self._show_draft(user_id)
                    return True
                if action == 'save' and step == 'preview':
                    draft['values']['name'] = _profile_name(draft['values'])
                    if not self.store.put_profile_draft(user_id, draft, revision):
                        return False
                result = self.store.finish_profile_draft(
                    user_id, parts[1], revision, save=action == 'save'
                )
                if not result:
                    return False
                if action == 'save':
                    await self.api.send_message(
                        user_id,
                        f"Профиль «{_profile_name(draft['values'])}» сохранён.",
                    )
                else:
                    await self.api.send_message(user_id, 'Настройка профиля отменена.')
                await self._profiles(user_id)
                return True
            if action == 'choose' and len(parts) == 5:
                choices = self._choices(draft)
                index = int(parts[4])
                if index < 0 or index >= len(choices):
                    return False
                value = choices[index][0]
                if step in {'direction_id', 'specialization_id', 'role_id'}:
                    self._set_draft_value(draft, value)
                    self._advance(draft)
                elif step in {
                    'primary_language',
                    'seniority',
                    'formats',
                    'vacancy_languages',
                }:
                    self._set_draft_value(draft, value)
                    self._advance(draft)
                elif step == 'geography' and value == '__custom__':
                    selected = draft['values']['preferences'].get('geography', [])
                    draft['values']['preferences']['geography'] = list(
                        dict.fromkeys([*selected, '__custom__'])
                    )
                    await self.api.send_message(
                        user_id,
                        'Введите страну или регион. Можно указать точное значение.',
                    )
                elif step == 'geography':
                    selected = list(draft['values']['preferences'].get('geography', []))
                    if value == 'worldwide':
                        selected = [] if selected == ['worldwide'] else ['worldwide']
                    else:
                        selected = [
                            item
                            for item in selected
                            if item not in {'worldwide', '__custom__'}
                        ]
                        if value in selected:
                            selected.remove(value)
                        else:
                            selected.append(value)
                    self._set_draft_value(draft, selected)
                else:
                    pref_step = 'stacks' if step == 'legacy_stacks' else step
                    selected = list(draft['values']['preferences'].get(pref_step, []))
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.append(value)
                    self._set_draft_value(draft, selected)
            elif len(parts) != 4:
                return False
            elif action == 'back':
                self._back(draft)
            elif action == 'input' and step in {'required_skills', 'excluded_skills'}:
                await self.api.send_message(
                    user_id,
                    'Введите свои значения через запятую. Примеры не добавляются автоматически.',
                )
            elif action in {'skip', 'next'} and step not in (
                'direction_id',
                'specialization_id',
                'role_id',
                'preview',
            ):
                if action == 'skip':
                    if draft.get('profile_id') is None and step == 'primary_language':
                        draft['values']['preferences'].pop('primary_language', None)
                    elif draft.get('profile_id') is None and step != 'legacy_stacks':
                        self._set_draft_value(draft, [])
                self._advance(draft)
            else:
                return False
            draft['revision'] += 1
            if not self.store.put_profile_draft(user_id, draft, revision):
                return False
            await self._show_draft(user_id)
            return True
        if len(parts) not in {4, 5}:
            return False
        profile = self.store.get_profile(user_id, parts[2])
        if profile is None or parts[3] != str(profile.version):
            return False
        action = parts[1]
        if action == 'template' and len(parts) == 5:
            templates = list_templates(profile)
            index = int(parts[4])
            if index < 0 or index >= len(templates):
                return False
            template = templates[index]
            query = compose_query(template.id, profile)
            profile = self.store.update_profile(
                user_id,
                profile.profile_id,
                expected_version=profile.version,
                preferences={**profile.preferences, 'premium_template_id': template.id},
            )
            if profile is None:
                return False
            await self.api.send_message(user_id, f'Шаблон сохранён. Запрос:\n{query}')
            await self._show_profile(user_id, profile)
        elif len(parts) != 4:
            return False
        elif action == 'view':
            await self._show_profile(user_id, profile)
        elif action == 'edit':
            await self._start_draft(user_id, profile)
        elif action == 'advanced':
            await self._start_draft(user_id, profile, wizard='advanced')
        elif action == 'toggle':
            profile = self.store.update_profile(
                user_id,
                profile.profile_id,
                expected_version=profile.version,
                is_active=not profile.is_active,
            )
            if profile is None:
                return False
            await self._show_profile(user_id, profile)
        elif action == 'search':
            if self.search is None:
                await self.api.send_message(user_id, 'Premium-поиск недоступен.')
            else:
                await self.search.preview(user_id, profile)
        elif action == 'templates':
            rows = [
                [
                    {
                        'text': t.label,
                        'callback_data': f'p:template:{profile.profile_id}:{profile.version}:{i}',
                    }
                ]
                for i, t in enumerate(list_templates(profile))
            ]
            await self.api.send_message(
                user_id,
                'Выберите Premium-шаблон. Поиск не запускается.',
                reply_markup={'inline_keyboard': rows},
            )
        else:
            return False
        return True
