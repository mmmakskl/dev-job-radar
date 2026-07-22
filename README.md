# Telegram Vacancy Parser

Умный Telegram-парсер вакансий: отслеживает заданные каналы, предварительно фильтрует сообщения по Go/Golang, анализирует вакансии через Mistral AI и сохраняет подходящие результаты в Google Sheets. Live-мониторинг принимает сообщения через ограниченную asyncio.Queue и передаёт их последовательному worker, а обработка истории остаётся последовательной. Ответ Mistral проверяется по строгой схеме, а временные API/JSON-ошибки получают не более двух попыток. Двухуровневая дедупликация хранит ссылки бессрочно, а SHA-256 нормализованного текста — 30 дней в append-only JSONL. В state попадают только релевантные вакансии после успешной записи в Google Sheets.

## Стек технологий

- Python 3.10+
- Telethon — Telegram-клиент
- Mistral API через OpenAI Python SDK — AI-анализ вакансий
- gspread и Google Auth — запись в Google Sheets
- python-dotenv — конфигурация через переменные окружения
- Make — стандартизированные команды установки и запуска

## Предварительные требования

Перед установкой подготовьте:

1. Python 3.10 или новее и `make`.
2. `API_ID` и `API_HASH`, созданные в разделе **API development tools** на [my.telegram.org](https://my.telegram.org/).
3. `MISTRAL_API_KEY` из [Mistral AI Console](https://console.mistral.ai/).
4. Google Cloud Service Account с включённым Google Sheets API. Скачайте его JSON-ключ и предоставьте email сервисного аккаунта права редактора на целевую таблицу.

## Установка

```bash
git clone <repository-url>
cd TgUserBotWork
make init
```

Команда создаёт локальное окружение `venv/`, устанавливает зависимости,
создаёт `.env` из шаблона без перезаписи существующего файла и подготавливает
директорию `data/`. Отдельные этапы доступны через `make venv`, `make install`
и `make env`.

## Настройка

Создайте рабочую конфигурацию из шаблона:

```bash
cp .env.example .env
```

Заполните `.env`:

```dotenv
API_ID=12345678
API_HASH=your_telegram_api_hash
SESSION_NAME=my_account
TARGET_CHANNELS=channel_username,-1001234567890
MISTRAL_API_KEY=your_mistral_api_key
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...
OUTPUT_TIMEZONE=Europe/Moscow
GOOGLE_SHEET_FULL_TITLE=Вакансии — полные
GOOGLE_SHEET_SHORT_TITLE=Вакансии — кратко
STATE_FILE_PATH=data/state.jsonl
TEXT_HASH_TTL_DAYS=30
LIVE_QUEUE_MAXSIZE=1000
LIVE_WORKERS=1
```

Сохраните ключ Service Account как `credentials.json` в корне проекта. Файлы `.env`, `credentials.json` и `*.session` содержат секреты и не должны попадать в Git.

Для первой авторизации Telegram выполните:

```bash
make auth
```

Для явного удаления существующей session и повторной авторизации используйте
`make auth-force`. Обычный `make auth` session не удаляет.

Отсканируйте QR-код в Telegram. После успешного входа рядом появится локальный файл `my_account.session`.

Если указанная сессия уже авторизована, `make auth` покажет пользователя и завершится, не удаляя файл. Для намеренного сброса сессии и повторного входа используйте:

```bash
PYTHONPATH=src venv/bin/python scripts/auth.py --force-relogin
```

Перед удалением session-файла скрипт выводит явное предупреждение. Live и history проверяют наличие настроек Telegram, Mistral и Google Sheets до запуска; auth и discovery требуют только Telegram-настройки. Значения секретов в ошибках не выводятся.

## Запуск

Запустите обработку новых сообщений:

```bash
make run
```

Live handler только извлекает text, raw_text и ссылку поста, после чего
неблокирующе ставит job в очередь. Worker последовательно выполняет
дедупликацию, keyword-префильтр, Mistral-анализ и экспорт в Google Sheets.
Рекомендуется оставлять LIVE_WORKERS=1 для последовательного rate limit.
При заполнении очереди на 80% пишется warning; при полном заполнении новое
сообщение пропускается с error, но listener продолжает работу. При остановке
приложение прекращает приём сообщений и до 30 секунд ожидает завершения очереди.

Дополнительные команды:

```bash
make init      # подготовить venv, зависимости, .env и data/
make live      # alias для make run
make history   # обработать сообщения за последнюю неделю
make discover  # найти каналы среди текущих Telegram dialogs
make channels  # alias для make discover
make compile   # проверить синтаксис без обращения к внешним API
make test      # запустить изолированные pytest-тесты
make check     # выполнить compile и test
make doctor    # локально проверить окружение без API
make state-info # показать локальную информацию о JSONL state
make clean-cache # удалить только безопасные кэши
```

Telethon хранит авторизацию в SQLite session-файле и не допускает одновременную
запись из нескольких процессов. Перед `make history` остановите `make run`,
если оба сценария используют один `SESSION_NAME`. При необходимости параллельного
запуска задайте разные имена сессий.

Те же точки входа можно запускать напрямую из корня проекта:

```bash
PYTHONPATH=src venv/bin/python scripts/run_live.py
PYTHONPATH=src venv/bin/python scripts/parse_history.py
PYTHONPATH=src venv/bin/python scripts/auth.py
PYTHONPATH=src venv/bin/python scripts/discover_channels.py
```

При старте приложение восстанавливает дедупликацию из `data/state.jsonl` и
загружает legacy-ссылки из старого первого листа. Ссылки и стабильные ID
блокируют повторную обработку бессрочно, текстовые хэши — в течение настроенного
TTL. ID строится из Telegram-канала и ID сообщения, например
`remotegeekjob_40909`, а не из номера строки таблицы. JSONL отмечается только
после успешной записи в оба новых листа. Если полный лист записался, а краткий
нет, следующая обработка обнаружит полный ID и восстановит только краткую
строку. Старые листы и данные не изменяются.

## Формат Google Sheets

При первом экспорте автоматически создаются два листа. Если они уже существуют,
бот использует их и не добавляет строку заголовков повторно. Заголовки
закрепляются, получают фильтр и оформление; длинные значения переносятся.

### Вакансии — полные

Полный лист содержит 37 структурированных колонок: стабильный ID, даты
публикации и добавления, название и компанию, диапазон грейда, ответственность,
роли, специализации и сферу продукта, опыт и условия работы, географию,
типизированную зарплату, обязательный и желательный стек, контакты и ссылки,
Telegram-источник, описание, обязанности, требования, статус и качество данных.
Числовые значения опыта и зарплаты записываются числами; отсутствующие числовые
поля остаются пустыми. Категориальные поля без данных получают `Не указано`.

### Вакансии — кратко

Краткий лист содержит только 13 пользовательских колонок:

Дата | Вакансия | Компания | Грейд | Роль | Сфера | Формат | Локация | Зарплата | Ключевой стек | Краткое описание | Ссылка | Статус

Грейд и зарплата собираются в читаемый диапазон, локация объединяется без
повторов, ключевой стек ограничен шестью технологиями, а описание — 250
символами. Ссылка ведёт на прямой отклик, а при его отсутствии — на Telegram.
Стабильный ID краткой строки хранится в note ячейки ссылки, не добавляя
техническую колонку в пользовательский лист.

Дата публикации берётся из metadata Telegram-сообщения: `event.message.date`
в live-режиме и `message.date` при обработке истории. Дата добавления
выставляется ботом непосредственно при записи строки. Обе даты переводятся в
`OUTPUT_TIMEZONE` и форматируются как `YYYY-MM-DD HH:MM`.

## Структура проекта

```text
src/
  tg_vacancy_bot/
    __init__.py
    config.py                 # загрузка настроек
    logging_config.py         # настройка логирования
    models.py                 # модель и нормализация вакансии
    pipeline/
      __init__.py
      processor.py            # общий pipeline обработки вакансии
      prefilter.py            # поиск ключевых слов
      fingerprints.py         # нормализация текста и SHA-256
      dedupe_state.py         # append-only JSONL state с TTL
    telegram/
      __init__.py
      links.py                # формирование ссылок Telegram
    llm/
      __init__.py
      mistral.py              # интеграция с Mistral
      prompts.py              # system prompt анализа вакансий
      schemas.py              # проверка и нормализация JSON-ответа
    storage/
      __init__.py
      sheets.py               # чтение и запись Google Sheets
scripts/
  run_live.py                 # мониторинг новых сообщений
  parse_history.py            # обработка истории за семь дней
  auth.py                     # Telegram-авторизация по QR-коду
  discover_channels.py        # поиск каналов по ключевым словам
tests/                        # изолированные автоматические тесты
  test_pipeline.py            # префильтр и текстовые отпечатки
  test_dedupe_state.py        # долговременная дедупликация JSONL
  test_processor.py           # контракты экспорта общего pipeline
  test_llm_schemas.py         # схема и устойчивость JSON-анализа
  test_models.py              # грейды, зарплата, условия и стек
  test_sheets.py              # полная/краткая строки и частичный сбой
data/
  .gitkeep
```

Файлы `.env`, `credentials.json`, Telegram-сессии и сгенерированные данные остаются локальными и игнорируются Git.
