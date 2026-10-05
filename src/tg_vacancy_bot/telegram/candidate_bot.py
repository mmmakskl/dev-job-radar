"""Personal vacancy feed, profile wizard and Premium templates over Bot API."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from tg_vacancy_bot.candidate_catalog import CATALOG, direction_stacks
from tg_vacancy_bot.premium_search.templates import compose_query, list_templates
from tg_vacancy_bot.telegram.candidate_card_formatter import (
    format_card,
    format_legacy_card,
)
from tg_vacancy_bot.telegram.candidate_delivery_worker import personal_card
from tg_vacancy_bot.telegram.bot_api import BotApiError
from tg_vacancy_bot.telegram.candidate_search import CandidateSearch
from tg_vacancy_bot.telegram.candidate_store import (
    CandidateProfile,
    CandidateStore,
    CandidateVacancy,
)


class CandidateBotApi(Protocol):
    async def get_updates(self, offset: int | None, timeout: int) -> list[dict]: ...
    async def set_my_commands(self, commands: list[dict[str, str]]) -> None: ...
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
    'stacks',
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
            'Языки и технологии',
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
    'onec_developer': ('Разработчик 1С', 'разрабатывает и сопровождает решения 1С'),
    'onec_analyst_consultant': (
        'Аналитик-консультант 1С',
        'собирает требования и настраивает решения 1С',
    ),
    'onec_administrator': ('Администратор 1С', 'поддерживает платформу и базы 1С'),
}
SENIORITY_LABELS = {
    'intern': 'Intern',
    'junior': 'Junior',
    'middle': 'Middle',
    'senior': 'Senior',
    'lead': 'Lead',
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
    'onec_development': 'Разработка 1С',
    'onec_analytics': 'Аналитика и консультирование 1С',
    'onec_administration': 'Администрирование 1С',
}
GEOGRAPHY_CHOICES = [
    ('worldwide', 'Весь мир'),
    ('Россия', 'Россия'),
    ('Казахстан', 'Казахстан'),
    ('Беларусь', 'Беларусь'),
    ('ЕС', 'ЕС'),
    ('__custom__', 'Другая страна'),
]
TECHNOLOGY_LABELS = {
    **LANGUAGE_LABELS,
    'typescript': 'TypeScript',
    'ruby': 'Ruby',
    'nodejs': 'Node.js',
    'react_native': 'React Native',
    'embedded_linux': 'Embedded Linux',
    'power_bi': 'Power BI',
    '1c_enterprise': '1С:Предприятие',
    'bsl': 'Язык 1С (BSL)',
    'erp': 'ERP',
    'zup': '1С:ЗУП',
    'accounting': '1С:Бухгалтерия',
    'trade_management': '1С:Управление торговлей',
    'pytorch': 'PyTorch',
    'tensorflow': 'TensorFlow',
    'mlops': 'MLOps',
    'figma': 'Figma',
    'photoshop': 'Photoshop',
    'illustrator': 'Illustrator',
    'indesign': 'InDesign',
    'kubernetes': 'Kubernetes',
    'postgresql': 'PostgreSQL',
    'kafka': 'Kafka',
    'airflow': 'Airflow',
    'terraform': 'Terraform',
    'ansible': 'Ansible',
    'aws': 'AWS',
    'gcp': 'Google Cloud',
    'linux': 'Linux',
    'windows': 'Windows',
    'prometheus': 'Prometheus',
    'grafana': 'Grafana',
    'owasp': 'OWASP',
    'secure_code': 'Безопасный код',
    'incident_response': 'Реагирование на инциденты',
    'people_management': 'Управление командами',
    'hr_analytics': 'HR-аналитика',
}
PAGE_SIZE = 8
DIRECTION_LABELS = {
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
    'onec': 'Разработка и сопровождение 1С',
}


def _technology_label(value: str) -> str:
    if value in TECHNOLOGY_LABELS:
        return TECHNOLOGY_LABELS[value]
    return value.replace('_', ' ').strip().title()


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
    if values.get('name'):
        return values['name']
    direction = DIRECTION_LABELS.get(values.get('direction_id'), 'Профиль')
    preferences = values.get('preferences', {})
    technologies = preferences.get('stacks', [])
    language = preferences.get('primary_language')
    if language:
        technologies = [language, *technologies]
    if technologies:
        return f'{direction} · {_technology_label(technologies[0])}'
    return direction


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
        search: CandidateSearch | None = None,
    ) -> None:
        self.api = api
        self.store = store
        self.allowed_user_ids = allowed_user_ids
        self.registry = registry
        self.search = search

    def _keyboard(self, user_id: int) -> dict:
        days = self.store.get_older_days(user_id)
        # Age-bucket screens read the legacy Go projection; profile matches
        # come exclusively from the shared registry via the personal screen.
        first_row = ['Go · Новые · 24 часа', f'Go · Ранее · за {days} дней']
        second_row = ['Сохранённые', 'Go · Без даты', 'Для меня · активный профиль']
        return {
            'keyboard': [first_row, second_row, ['Профили']],
            'resize_keyboard': True,
        }

    async def _personal_feed(self, user_id: int) -> None:
        state = self.store.get_active_profile_state(user_id)
        if state != 'single':
            message = (
                'Выберите один активный профиль: раньше было активно несколько.'
                if state == 'ambiguous'
                else 'Сначала создайте профиль и выберите его активным.'
            )
            await self.api.send_message(
                user_id, message, reply_markup={'inline_keyboard': []}
            )
            await self._profiles(user_id)
            return
        await self._show_browser(user_id, 'for_me')

    async def _show_age_choices(self, user_id: int) -> None:
        rows = [
            [{'text': _days_label(days), 'callback_data': f'f:days:{days}'}]
            for days in (3, 7, 14, 30)
        ]
        rows.append([{'text': 'Ввести свой срок', 'callback_data': 'f:custom'}])
        await self.api.send_message(
            user_id,
            f"Выберите срок общей Go-ленты. «Ранее» покажет Go-объявления старше 24 часов и не старше выбранного срока. Сейчас доступно от 2 до {self.store.max_older_days()} дней — по самой старой дате в Go-ленте.",
            reply_markup={'inline_keyboard': rows},
        )

    async def _show_browser(
        self, user_id: int, bucket: str, index: int = 0, message_id: int | None = None
    ) -> bool:
        """Show one current card; navigation edits only the owner's private message."""
        if bucket == 'for_me' and self.registry is None:
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
                        'new': 'В общей Go-ленте за последние 24 часа нет вакансий с подтверждённой датой публикации. Проверьте более ранние Go-вакансии, объявления без даты или «Для меня».',
                        'older': f"В общей Go-ленте нет вакансий старше 24 часов и не старше {_days_label(self.store.get_older_days(user_id))}. Можно выбрать другой срок или открыть «Для меня».",
                        'undated': 'В общей Go-ленте нет вакансий без достоверной даты публикации. Такие объявления не включаются в «Новые».',
                        'saved': 'Сохранённых вакансий пока нет. Сохраняйте интересные карточки кнопкой «Сохранить».',
                    }[bucket]
                ),
                reply_markup=self._keyboard(user_id),
            )
            return True
        if index < 0 or index >= len(cards):
            return False
        content, callback_key = cards[index]
        feed_title = {
            'for_me': 'Для меня · активный профиль',
            'new': 'Общая Go-лента · новые',
            'older': 'Общая Go-лента · ранее',
            'undated': 'Общая Go-лента · без даты',
            'saved': 'Сохранённые вакансии',
        }[bucket]
        content = f'<i>{feed_title} · {index + 1} / {len(cards)}</i>\n\n{content}'
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
        await self.api.set_my_commands(
            [
                {'command': 'start', 'description': 'Открыть главное меню'},
                {'command': 'help', 'description': 'Показать команды'},
                {'command': 'new', 'description': 'Вакансии за последние 24 часа'},
                {'command': 'older', 'description': 'Выбрать срок ранней ленты'},
                {'command': 'saved', 'description': 'Сохранённые вакансии'},
                {'command': 'undated', 'description': 'Объявления без даты'},
                {'command': 'forme', 'description': 'Поиск по активному профилю'},
                {'command': 'profiles', 'description': 'Профили и активный профиль'},
                {'command': 'cancel', 'description': 'Отменить текущую настройку'},
            ]
        )
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
                (
                    'Выберите общую Go-ленту или совпадения активного профиля. '
                    'Даты берутся из публикации источника; объявления без даты отдельно.'
                    if text == '/start'
                    else 'Команды: /new — общая Go-лента за 24 часа; /older — срок ранней Go-ленты; '
                    '/saved — сохранённые; /undated — без даты общей Go-ленты; /forme — по активному профилю; '
                    '/profiles — профили; /cancel — отменить мастер.'
                ),
                reply_markup=self._keyboard(user_id),
            )
            return
        if text in {'Для меня', 'Для меня · активный профиль', '/forme'}:
            await self._personal_feed(user_id)
            return
        if text in {'Профили', '/profiles'}:
            await self._profiles(user_id)
            return
        if text in {'/new', 'Новые · 24 часа', 'Go · Новые · 24 часа'}:
            await self._show_browser(user_id, 'new')
            return
        if text in {'/older'}:
            await self._show_age_choices(user_id)
            return
        if text in {'/saved', 'Сохранённые'}:
            await self._show_browser(user_id, 'saved')
            return
        if text in {'/undated', 'Без даты', 'Go · Без даты'}:
            await self._show_browser(user_id, 'undated')
            return
        if text in {'Отмена', '/cancel'} and self.store.get_profile_draft(user_id):
            draft = self.store.get_profile_draft(user_id)
            self.store.finish_profile_draft(
                user_id, draft['token'], draft['revision'], save=False
            )
            if isinstance(draft.get('message_id'), int):
                try:
                    await self.api.edit_message_text(
                        user_id,
                        draft['message_id'],
                        'Настройка профиля отменена.',
                        reply_markup={'inline_keyboard': []},
                    )
                except BotApiError:
                    await self.api.send_message(user_id, 'Настройка профиля отменена.')
            else:
                await self.api.send_message(user_id, 'Настройка профиля отменена.')
            return
        if text.startswith(('Ранее', 'Go · Ранее')):
            await self._show_age_choices(user_id)
            return
        if text in {'Новые', '/new', 'Go · Новые · 24 часа'}:
            await self._show_browser(user_id, 'new')
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
            'Go · Новые · 24 часа',
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
                'Используйте «Go · Новые/Ранее» для общей Go-ленты, «Для меня» для совпадений активного профиля, а также «Сохранённые» и «Профили».',
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
        if user_id in self.allowed_user_ids and self.registry is not None:
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
            await self.api.answer_callback_query(callback_id, 'Обновляю экран.')
            try:
                source_message = callback.get('message') or {}
                if parts == ['p', 'resume'] and isinstance(
                    source_message.get('message_id'), int
                ):
                    draft = self.store.get_profile_draft(user_id)
                    if draft is not None:
                        draft['message_id'] = source_message['message_id']
                        self.store.put_profile_draft(user_id, draft, draft['revision'])
                valid = await self._profile_callback(user_id, parts)
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid and parts[0] == 'w' and self.store.get_profile_draft(user_id):
                await self._show_draft(user_id)
            elif not valid:
                await self.api.send_message(
                    user_id,
                    'Эта кнопка устарела. Откройте актуальный профиль в разделе «Профили».',
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
            return [
                (d['id'], DIRECTION_LABELS.get(d['id'], d['synonyms'][0]))
                for d in directions
            ]
        elif step == 'stacks':
            return [
                (item, _technology_label(item))
                for item in direction_stacks(values['direction_id'])
            ]
        elif step in {'primary_language', 'additional_languages'}:
            if step == 'primary_language':
                return _language_choices(values)
            if step == 'additional_languages':
                main = values.get('preferences', {}).get('primary_language')
                return [
                    choice for choice in _language_choices(values) if choice[0] != main
                ]
        elif step == 'legacy_stacks':
            role = _role_for_values(values)
            return [(value, value) for value in (role or {}).get('stacks', [])]
        elif step == 'geography':
            return GEOGRAPHY_CHOICES
        else:
            return {
                'seniority': [(k, SENIORITY_LABELS[k]) for k in SENIORITY_LABELS],
                'formats': [(k, FORMAT_LABELS[k]) for k in FORMAT_LABELS],
                'vacancy_languages': [
                    ('ru', 'Русский'),
                    ('en', 'Английский'),
                    ('__all__', 'Любой'),
                ],
            }.get(step, [])
        return []

    @staticmethod
    def _profile_text(values: dict) -> str:
        prefs = values.get('preferences', {})
        lines = [
            f"Профиль: {_profile_name(values)}",
            f"Направление: {DIRECTION_LABELS.get(values.get('direction_id'), '—')}",
            (
                'Активность: выбран'
                if values.get('is_active')
                else 'Активность: не выбран'
            ),
        ]
        selected_stacks = list(prefs.get('stacks', []))
        selected_stacks.extend(
            item
            for item in [
                prefs.get('primary_language'),
                *prefs.get('additional_languages', []),
            ]
            if item and item not in selected_stacks
        )
        lines.append(
            'Языки и технологии: '
            + (
                ', '.join(_technology_label(item) for item in selected_stacks)
                or 'любые'
            )
        )
        if prefs.get('seniority'):
            lines.append(
                'Грейд: '
                + ', '.join(
                    SENIORITY_LABELS.get(item, item) for item in prefs['seniority']
                )
            )
        else:
            lines.append('Грейд: любой')
        if prefs.get('formats'):
            lines.append(
                'Формат: '
                + ', '.join(FORMAT_LABELS.get(item, item) for item in prefs['formats'])
            )
        else:
            lines.append('Формат: любой')
        lines.extend(
            f"{STEP_LABELS.get(key, key)}: {', '.join(_technology_label(item) for item in value) if isinstance(value, list) else value}"
            for key, value in prefs.items()
            if key
            not in {
                'timezone',
                'delivery_mode',
                'premium_template_id',
                'stacks',
                'seniority',
                'formats',
                'geography',
            }
            and value
        )
        return '\n'.join(lines)[:3500]

    async def _profiles(
        self, user_id: int, *, message_id: int | None = None, notice: str = ''
    ) -> None:
        profiles = self.store.list_profiles(user_id)
        rows = []
        for profile in profiles:
            rows.append(
                [
                    {
                        'text': f'{"●" if profile.is_active else "○"} {profile.name}',
                        'callback_data': f'p:view:{profile.profile_id}:{profile.version}',
                    },
                    {
                        'text': 'Выбрать активным',
                        'callback_data': f'p:activate:{profile.profile_id}:{profile.version}',
                    },
                ]
            )
        rows.append([{'text': 'Создать профиль', 'callback_data': 'p:new'}])
        if self.store.get_profile_draft(user_id):
            rows.append([{'text': 'Продолжить черновик', 'callback_data': 'p:resume'}])
        state = self.store.get_active_profile_state(user_id)
        status = {
            'ambiguous': 'Раньше были активны несколько профилей. Выберите один активный профиль ниже.',
            'none': 'Активный профиль не выбран. Это не ограничивает общий сбор вакансий.',
            'single': 'Активный профиль определяет персональную выдачу и сопоставление.',
        }[state]
        text = notice + 'Профили\n' + status
        markup = {'inline_keyboard': rows}
        if message_id is not None:
            try:
                await self.api.edit_message_text(
                    user_id, message_id, text, reply_markup=markup
                )
                return
            except BotApiError:
                pass
        await self.api.send_message(user_id, text, reply_markup=markup)

    async def _show_profile(self, user_id: int, profile: CandidateProfile) -> None:
        rows = [
            [
                {
                    'text': (
                        'Снять активность' if profile.is_active else 'Выбрать активным'
                    ),
                    'callback_data': f'p:toggle:{profile.profile_id}:{profile.version}',
                }
            ],
            [
                {
                    'text': 'Редактировать',
                    'callback_data': f'p:edit:{profile.profile_id}:{profile.version}',
                }
            ],
            [
                {
                    'text': 'Дополнительные настройки',
                    'callback_data': f'p:advanced:{profile.profile_id}:{profile.version}',
                }
            ],
        ]
        if self.store.get_active_profile(user_id) == profile and (
            profile.role_id or profile.direction_id == 'onec'
        ):
            if list_templates(profile):
                rows.append(
                    [
                        {
                            'text': 'Premium-шаблоны',
                            'callback_data': f'p:templates:{profile.profile_id}:{profile.version}',
                        }
                    ]
                )
            if self.search is not None and self.search._available(user_id):
                rows.append(
                    [
                        {
                            'text': 'Искать',
                            'callback_data': f'p:search:{profile.profile_id}:{profile.version}',
                        }
                    ]
                )
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
                    'specialization_id': '',
                    'role_id': '',
                    'preferences': {
                        'stacks': [],
                        'delivery_mode': 'manual',
                        'timezone': 'Europe/Moscow',
                    },
                    'is_active': False,
                }
            )
            values = {
                **values,
                'preferences': dict(values.get('preferences', {})),
            }
            if profile is not None:
                preferences = values['preferences']
                visible = list(preferences.get('stacks', []))
                for item in [
                    preferences.get('primary_language'),
                    *preferences.get('additional_languages', []),
                ]:
                    if item and item not in visible:
                        visible.append(item)
                preferences['stacks'] = visible
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
        if (
            values.get('preferences', {}).get('stacks')
            and values.get('specialization_id')
            and values.get('role_id')
        ):
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
        return list(PROFILE_STEPS)

    async def _show_draft(self, user_id: int) -> None:
        draft = self.store.get_profile_draft(user_id)
        if draft is None:
            await self._profiles(user_id)
            return
        step = draft['step']
        prefix = f'w:{draft["token"]}:{draft["revision"]}:'

        def button(label, action):
            return {'text': label, 'callback_data': prefix + action}

        rows: list[list[dict[str, str]]] = []
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
                'stacks': 'Выберите любые подходящие языки и технологии из каталога. Пример: Go, PostgreSQL, Docker. Для HR и дизайна будут свои варианты.',
                'seniority': 'Можно выбрать несколько уровней. Например: Middle и Senior.',
                'formats': 'Можно выбрать несколько форматов. Например: удалённо и гибридно.',
                'geography': 'Где ищете работу? Можно выбрать несколько стран или регионов.',
                'additional_languages': 'Можно добавить языки помимо основного. Например: Python.',
                'required_skills': self._required_skills_hint(draft['values']),
                'excluded_skills': 'Какие технологии или условия исключить? Введите через запятую или пропустите.',
                'vacancy_languages': 'Язык текста объявления — русский, английский или любой. Это язык объявления, а не язык программирования.',
                'legacy_stacks': 'Технологии из старых настроек. Это может быть Docker, PostgreSQL или язык; список уже участвует в matching.',
            }
            text = f"{STEP_LABELS.get(step, step)}\n{hints.get(step, 'Выберите подходящее или пропустите.')}"
            if draft.get('input_prompt'):
                text += '\n' + draft['input_prompt']
            if draft.get('error'):
                text += '\n' + draft['error']
            choices = self._choices(draft)
            preferences = draft['values']['preferences']
            selected = preferences.get(
                'stacks' if step in {'stacks', 'legacy_stacks'} else step,
                draft['values'].get(step, []),
            )
            if not isinstance(selected, list):
                selected = [selected] if selected else []
            page_count = max(1, (len(choices) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = max(0, min(int(draft.get('page', 0)), page_count - 1))
            draft['page'] = page
            visible_choices = choices[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            rows = [
                [
                    button(
                        ('✓ ' if key in selected else '') + label,
                        f'choose:{page * PAGE_SIZE + i}',
                    )
                ]
                for i, (key, label) in enumerate(visible_choices)
            ]
            if page_count > 1:
                page_controls = []
                if page > 0:
                    page_controls.append(button('←', f'page:{page - 1}'))
                page_controls.append(button(f'{page + 1}/{page_count}', 'page:stay'))
                if page + 1 < page_count:
                    page_controls.append(button('→', f'page:{page + 1}'))
                rows.append(page_controls)
            if step != 'direction_id':
                if step in {'required_skills', 'excluded_skills'}:
                    rows.append([button('Ввести через запятую', 'input')])
                if step in {
                    'stacks',
                    'seniority',
                    'formats',
                    'geography',
                    'additional_languages',
                    'legacy_stacks',
                }:
                    rows.append([button('Готово', 'next')])
                if step in {'seniority', 'formats'}:
                    rows.append([button('Все', 'all')])
                if step in {
                    'vacancy_languages',
                    'primary_language',
                    'required_skills',
                    'excluded_skills',
                }:
                    rows.append([button('Пропустить', 'skip')])
            if selected:
                selected_labels = [
                    dict(choices).get(item, _technology_label(item))
                    for item in selected
                ]
                text += f"\nВыбрано: {', '.join(selected_labels)}"
            elif step in {'seniority', 'formats'}:
                text += '\nВыбрано: Все (без ограничения)'
            elif step == 'stacks':
                text += '\nВыбрано: без фильтра по языкам и технологиям'
        order = (
            self._advanced_steps(draft['values'])
            if draft.get('wizard') == 'advanced'
            else self._basic_steps(draft['values'])
        )
        if step != order[0]:
            rows.append([button('Назад', 'back')])
        rows.append([button('Отмена', 'cancel')])
        reply_markup = {'inline_keyboard': rows}
        message_id = draft.get('message_id')
        try:
            if isinstance(message_id, int):
                await self.api.edit_message_text(
                    user_id,
                    message_id,
                    text,
                    reply_markup=reply_markup,
                )
            else:
                sent = await self.api.send_message(
                    user_id, text, reply_markup=reply_markup
                )
                message_id = sent.get('message_id')
                if isinstance(message_id, int):
                    draft['message_id'] = message_id
                    self.store.put_profile_draft(user_id, draft, draft['revision'])
        except BotApiError:
            sent = await self.api.send_message(user_id, text, reply_markup=reply_markup)
            message_id = sent.get('message_id')
            if isinstance(message_id, int):
                draft['message_id'] = message_id
                self.store.put_profile_draft(user_id, draft, draft['revision'])

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
        preferences = values.get('preferences', {})
        language = preferences.get('primary_language') or next(
            (item for item in preferences.get('stacks', []) if item in examples), None
        )
        example = examples.get(language, 'например, SQL или Docker')
        return f"Необязательно. Пример для ориентира: {example}. Это не добавит навыки автоматически; введите свои или пропустите."

    def _set_draft_value(self, draft: dict, value: str | list[str]) -> None:
        step, values = draft['step'], draft['values']
        if step == 'direction_id':
            if values.get('direction_id') != value:
                values['specialization_id'] = ''
                values['role_id'] = ''
                values['preferences']['stacks'] = []
                values['preferences'].pop('primary_language', None)
                values['preferences'].pop('additional_languages', None)
                values['preferences'].pop('premium_template_id', None)
                values['name'] = ''
            values['direction_id'] = value
            draft['page'] = 0
        elif step in {'stacks', 'seniority', 'formats'}:
            key = step
            selected = list(values['preferences'].get(key, []))
            if value == '__all__':
                selected = []
            elif isinstance(value, str):
                if value in selected:
                    selected.remove(value)
                else:
                    selected.append(value)
            else:
                selected = list(value)
            values['preferences'][key] = list(dict.fromkeys(selected))
            if step == 'stacks':
                values['preferences'].pop('primary_language', None)
                values['preferences'].pop('additional_languages', None)
                values['preferences'].pop('premium_template_id', None)
                self.store._validate_profile_path(
                    values['direction_id'],
                    values.get('specialization_id', ''),
                    values.get('role_id', ''),
                    selected,
                )
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
            if step == 'vacancy_languages' and value == ['__all__']:
                value = []
            preferences = {**values['preferences'], pref_step: value}
            if pref_step == 'stacks':
                self.store._validate_profile_path(
                    values['direction_id'],
                    values.get('specialization_id', ''),
                    values.get('role_id', ''),
                    value,
                )
                self.store._clear_invalid_template(
                    values['role_id'], preferences, values['direction_id']
                )
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
            if step in {
                'direction_id',
                'stacks',
                'seniority',
                'formats',
                'preview',
            }:
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
            draft['error'] = ''
            draft['input_prompt'] = ''
            revision = draft['revision']
            draft['revision'] += 1
            self.store.put_profile_draft(user_id, draft, revision)
        except ValueError as error:
            revision = draft['revision']
            draft['error'] = str(error)
            draft['revision'] += 1
            self.store.put_profile_draft(user_id, draft, revision)
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
                    if draft.get('profile_id') is None:
                        draft['values']['name'] = _profile_name(draft['values'])
                    if not self.store.put_profile_draft(user_id, draft, revision):
                        return False
                result = self.store.finish_profile_draft(
                    user_id, parts[1], revision, save=action == 'save'
                )
                if not result:
                    return False
                notice = (
                    f"Профиль «{_profile_name(draft['values'])}» сохранён.\n"
                    if action == 'save'
                    else 'Настройка профиля отменена.\n'
                )
                await self._profiles(
                    user_id,
                    message_id=draft.get('message_id'),
                    notice=notice,
                )
                return True
            if action == 'choose' and len(parts) == 5:
                choices = self._choices(draft)
                index = int(parts[4])
                if index < 0 or index >= len(choices):
                    return False
                value = choices[index][0]
                if step == 'direction_id':
                    self._set_draft_value(draft, value)
                    self._advance(draft)
                elif step in {'primary_language', 'vacancy_languages'}:
                    self._set_draft_value(draft, value)
                    self._advance(draft)
                elif step == 'geography' and value == '__custom__':
                    selected = draft['values']['preferences'].get('geography', [])
                    draft['values']['preferences']['geography'] = list(
                        dict.fromkeys([*selected, '__custom__'])
                    )
                    draft['input_prompt'] = (
                        'Введите страну или регион. Можно указать точное значение.'
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
                elif step in {
                    'stacks',
                    'seniority',
                    'formats',
                    'additional_languages',
                    'legacy_stacks',
                }:
                    self._set_draft_value(draft, value)
                else:
                    pref_step = 'stacks' if step == 'legacy_stacks' else step
                    selected = list(draft['values']['preferences'].get(pref_step, []))
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.append(value)
                    self._set_draft_value(draft, selected)
            elif len(parts) == 5 and action == 'page':
                choices = self._choices(draft)
                page_count = max(1, (len(choices) + PAGE_SIZE - 1) // PAGE_SIZE)
                if parts[4] == 'stay':
                    pass
                else:
                    page = int(parts[4])
                    if page < 0 or page >= page_count:
                        return False
                    draft['page'] = page
            elif len(parts) != 4:
                return False
            elif action == 'back':
                self._back(draft)
            elif action == 'input' and step in {'required_skills', 'excluded_skills'}:
                draft['input_prompt'] = (
                    'Введите свои значения через запятую. Примеры не добавляются автоматически.'
                )
            elif action == 'all' and step in {'seniority', 'formats'}:
                self._set_draft_value(draft, '__all__')
                self._advance(draft)
            elif action == 'skip' and step in {
                'primary_language',
                'vacancy_languages',
                'required_skills',
                'excluded_skills',
            }:
                if step == 'vacancy_languages':
                    draft['values']['preferences'].pop('vacancy_languages', None)
                elif draft.get('profile_id') is None and step == 'primary_language':
                    draft['values']['preferences'].pop('primary_language', None)
                self._advance(draft)
            elif action == 'next' and step in {
                'stacks',
                'seniority',
                'formats',
                'geography',
                'additional_languages',
                'legacy_stacks',
            }:
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
        if action == 'activate':
            selected = self.store.activate_profile(user_id, profile.profile_id)
            if selected is None:
                return False
            await self.api.send_message(
                user_id,
                f'Активен профиль «{selected.name}». Только он используется для персонального поиска и выдачи.',
            )
            await self._profiles(user_id)
            return True
        if action == 'template' and len(parts) == 5:
            if self.store.get_active_profile(user_id) != profile:
                return False
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
            profile = (
                self.store.update_profile(
                    user_id,
                    profile.profile_id,
                    expected_version=profile.version,
                    is_active=False,
                )
                if profile.is_active
                and self.store.get_active_profile_state(user_id) == 'single'
                else self.store.activate_profile(user_id, profile.profile_id)
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
            if self.store.get_active_profile(user_id) != profile:
                return False
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
