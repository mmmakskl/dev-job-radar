"""
Конфигурация системы агрегации вакансий из Telegram.
"""

import os
from dotenv import load_dotenv

from tg_vacancy_bot.admin.settings import load_runtime_settings
from tg_vacancy_bot.llm.prompts import build_system_prompt
from tg_vacancy_bot.paths import resolve_session_path, resolve_state_path

load_dotenv()


def parse_bool_env(value: str | None) -> bool:
    """Возвращает True только для явно включающих значений env."""
    return value.strip().casefold() in {"1", "true", "yes", "on"} if value else False


def parse_telegram_target(value: str | None) -> str | int:
    """Преобразует числовой Telegram ID из env в int."""
    target = (value or '').strip()
    if target.isdigit() or (target.startswith('-') and target[1:].isdigit()):
        return int(target)
    return target


# Telegram API credentials
try:
    API_ID = int(os.getenv('API_ID') or os.getenv('TG_API_ID', '0'))
except ValueError:
    API_ID = 0
API_HASH = os.getenv('API_HASH') or os.getenv('TG_API_HASH', '')
DATA_DIR = os.getenv('DATA_DIR') or None
_MANAGED = load_runtime_settings(DATA_DIR)
SESSION_NAME = resolve_session_path(
    os.getenv('SESSION_NAME', 'my_account'),
    data_dir=DATA_DIR,
    session_path=os.getenv('SESSION_PATH') or None,
)
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY', '')
GOOGLE_SHEET_URL = os.getenv('GOOGLE_SHEET_URL', '')
GOOGLE_CREDENTIALS_PATH = os.getenv(
    'GOOGLE_CREDENTIALS_PATH',
    'credentials.json',
)
OUTPUT_TIMEZONE = (
    _MANAGED.sheets.output_timezone
    if _MANAGED
    else os.getenv('OUTPUT_TIMEZONE', 'Europe/Moscow')
)
GOOGLE_SHEET_FULL_TITLE = (
    _MANAGED.sheets.full_title
    if _MANAGED
    else os.getenv('GOOGLE_SHEET_FULL_TITLE', 'Вакансии — полные')
)
GOOGLE_SHEET_SHORT_TITLE = (
    _MANAGED.sheets.short_title
    if _MANAGED
    else os.getenv('GOOGLE_SHEET_SHORT_TITLE', 'Вакансии — кратко')
)

# Список каналов для мониторинга
_raw_channels = os.getenv('TARGET_CHANNELS', 'devs_it,job_for_programmers').split(',')
TARGET_CHANNELS = []
for ch in _raw_channels:
    ch = ch.strip()
    if ch:
        if ch.isdigit() or (ch.startswith('-') and ch[1:].isdigit()):
            TARGET_CHANNELS.append(int(ch))
        else:
            TARGET_CHANNELS.append(ch)
for channel in _MANAGED.telegram.additional_channels if _MANAGED else []:
    if channel not in TARGET_CHANNELS:
        TARGET_CHANNELS.append(channel)
for source in _MANAGED.telegram.managed_sources if _MANAGED else []:
    if source.enabled and source.identifier not in TARGET_CHANNELS:
        TARGET_CHANNELS.append(source.identifier)
for channel in _MANAGED.telegram.folder_channels if _MANAGED else []:
    parsed_channel = parse_telegram_target(channel)
    if parsed_channel not in TARGET_CHANNELS:
        TARGET_CHANNELS.append(parsed_channel)
TARGET_CHANNELS = [
    channel
    for channel in TARGET_CHANNELS
    if str(channel) not in (_MANAGED.telegram.disabled_channels if _MANAGED else [])
]

# Название папки Telegram, чьи чаты автоматически добавляются в TARGET_CHANNELS.
TELEGRAM_CHANNELS_FOLDER = (
    _MANAGED.telegram.folder_name
    if _MANAGED
    else os.getenv('TELEGRAM_CHANNELS_FOLDER', 'Вакансии')
)
MONITORING_ENABLED = _MANAGED.telegram.monitoring_enabled if _MANAGED else True
HISTORY_DAYS = _MANAGED.telegram.history_days if _MANAGED else 7

# Префильтр для экономии токенов
KEYWORD_FILTER = _MANAGED.filters.keywords if _MANAGED else ['go', 'golang']
EXCLUDE_KEYWORDS = _MANAGED.filters.exclude_keywords if _MANAGED else []

# Долговременная дедупликация успешно экспортированных вакансий
STATE_FILE_PATH = resolve_state_path(
    data_dir=DATA_DIR,
    state_path=os.getenv('STATE_FILE_PATH') or None,
)
TEXT_HASH_TTL_DAYS = (
    _MANAGED.filters.text_hash_ttl_days
    if _MANAGED
    else int(os.getenv('TEXT_HASH_TTL_DAYS', '30'))
)

# Очередь live listener. Для последовательного rate limit рекомендуется 1 worker.
LIVE_QUEUE_MAXSIZE = (
    _MANAGED.filters.queue_maxsize
    if _MANAGED
    else int(os.getenv('LIVE_QUEUE_MAXSIZE', '1000'))
)
LIVE_WORKERS = (
    _MANAGED.filters.workers if _MANAGED else int(os.getenv('LIVE_WORKERS', '1'))
)

# Уведомления о сохранённых вакансиях в Telegram-канал.
TELEGRAM_NOTIFY_ENABLED = (
    _MANAGED.telegram.notify_enabled
    if _MANAGED
    else parse_bool_env(os.getenv('TELEGRAM_NOTIFY_ENABLED'))
)
TELEGRAM_NOTIFY_TARGET = parse_telegram_target(
    _MANAGED.telegram.notify_target if _MANAGED else os.getenv('TELEGRAM_NOTIFY_TARGET')
)
TELEGRAM_NOTIFY_HISTORY = parse_bool_env(os.getenv('TELEGRAM_NOTIFY_HISTORY'))

# Mistral behaviour is non-secret and may be changed through the panel.
MISTRAL_MODEL = _MANAGED.mistral.model if _MANAGED else 'mistral-small-latest'
MISTRAL_TEMPERATURE = _MANAGED.mistral.temperature if _MANAGED else 0.1
MISTRAL_MAX_ATTEMPTS = _MANAGED.mistral.max_attempts if _MANAGED else 2
MISTRAL_SYSTEM_PROMPT = build_system_prompt(
    _MANAGED.mistral.vacancy_instructions if _MANAGED else None
)
SETTINGS_REVISION = _MANAGED.revision if _MANAGED else 0

# Alert delivery uses the existing Telegram notification target and has no
# independent credentials. It is disabled until explicitly enabled in managed
# settings.
ALERTS_ENABLED = _MANAGED.alerts.enabled if _MANAGED else False
ALERT_HEARTBEAT_STALE_SECONDS = (
    _MANAGED.alerts.heartbeat_stale_seconds if _MANAGED else 120
)
ALERT_QUEUE_WARNING_PERCENT = _MANAGED.alerts.queue_warning_percent if _MANAGED else 80
ALERT_ERROR_STREAK_THRESHOLD = _MANAGED.alerts.error_streak_threshold if _MANAGED else 3
ALERT_ERROR_WINDOW_SECONDS = _MANAGED.alerts.error_window_seconds if _MANAGED else 900
ALERT_NO_EXPORT_SECONDS = _MANAGED.alerts.no_export_seconds if _MANAGED else 21600
ALERT_COOLDOWN_SECONDS = _MANAGED.alerts.cooldown_seconds if _MANAGED else 3600


def validate_required_settings(
    *,
    require_mistral: bool = True,
    require_google_sheets: bool = True,
) -> None:
    """Проверяет обязательные настройки, не раскрывая их значения."""
    missing = []
    if API_ID <= 0:
        missing.append('API_ID')
    if not API_HASH:
        missing.append('API_HASH')
    if require_mistral and not MISTRAL_API_KEY:
        missing.append('MISTRAL_API_KEY')
    if require_google_sheets and not GOOGLE_SHEET_URL:
        missing.append('GOOGLE_SHEET_URL')
    if TELEGRAM_NOTIFY_ENABLED and not TELEGRAM_NOTIFY_TARGET:
        missing.append('TELEGRAM_NOTIFY_TARGET')

    if missing:
        names = ', '.join(missing)
        raise RuntimeError(
            f"Не настроены обязательные переменные: {names}. "
            "Добавьте их в .env и повторите запуск."
        )
