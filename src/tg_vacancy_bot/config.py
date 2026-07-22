"""
Конфигурация системы агрегации вакансий из Telegram.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram API credentials
try:
    API_ID = int(os.getenv('API_ID') or os.getenv('TG_API_ID', '0'))
except ValueError:
    API_ID = 0
API_HASH = os.getenv('API_HASH') or os.getenv('TG_API_HASH', '')
SESSION_NAME = os.getenv('SESSION_NAME', 'my_account')
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY', '')
GOOGLE_SHEET_URL = os.getenv('GOOGLE_SHEET_URL', '')
OUTPUT_TIMEZONE = os.getenv('OUTPUT_TIMEZONE', 'Europe/Moscow')
GOOGLE_SHEET_FULL_TITLE = os.getenv(
    'GOOGLE_SHEET_FULL_TITLE',
    'Вакансии — полные',
)
GOOGLE_SHEET_SHORT_TITLE = os.getenv(
    'GOOGLE_SHEET_SHORT_TITLE',
    'Вакансии — кратко',
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

# Префильтр для экономии токенов
KEYWORD_FILTER = ['go', 'golang']  # Регистронезависимый поиск

# Долговременная дедупликация успешно экспортированных вакансий
STATE_FILE_PATH = os.getenv('STATE_FILE_PATH', 'data/state.jsonl')
TEXT_HASH_TTL_DAYS = int(os.getenv('TEXT_HASH_TTL_DAYS', '30'))

# Очередь live listener. Для последовательного rate limit рекомендуется 1 worker.
LIVE_QUEUE_MAXSIZE = int(os.getenv('LIVE_QUEUE_MAXSIZE', '1000'))
LIVE_WORKERS = int(os.getenv('LIVE_WORKERS', '1'))


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

    if missing:
        names = ', '.join(missing)
        raise RuntimeError(
            f"Не настроены обязательные переменные: {names}. "
            "Добавьте их в .env и повторите запуск."
        )
