# Telegram Vacancy Parser

Умный Telegram-парсер вакансий: отслеживает заданные каналы, предварительно фильтрует сообщения по Go/Golang, отбрасывает резюме/профили кандидатов, анализирует вакансии через Mistral AI и сохраняет подходящие результаты в Google Sheets. В optional private beta новый live-экспорт публикуется одной интерактивной Bot API-карточкой в общий канал. Live-мониторинг принимает сообщения через ограниченную asyncio.Queue и передаёт их последовательному worker, а обработка истории остаётся последовательной. Ответ Mistral проверяется по строгой схеме, а временные API/JSON-ошибки получают не более двух попыток. Двухуровневая дедупликация хранит ссылки бессрочно, а SHA-256 нормализованного текста — 30 дней в append-only JSONL. В state попадают только релевантные вакансии после успешной записи в Google Sheets.

## Стек технологий

- Python 3.12 (версия Docker production-образа и CI)
- Telethon — Telegram-клиент
- Mistral API через OpenAI Python SDK — AI-анализ вакансий
- gspread и Google Auth — запись в Google Sheets
- python-dotenv — конфигурация через переменные окружения
- Make — стандартизированные команды установки и запуска

## Предварительные требования

Перед установкой подготовьте:

1. Python 3.12 и `make`.
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
TELEGRAM_CHANNELS_FOLDER=Вакансии
MISTRAL_API_KEY=your_mistral_api_key
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...
OUTPUT_TIMEZONE=Europe/Moscow
GOOGLE_SHEET_FULL_TITLE=Вакансии — полные
GOOGLE_SHEET_SHORT_TITLE=Вакансии — кратко
STATE_FILE_PATH=data/state.jsonl
TEXT_HASH_TTL_DAYS=30
VACANCY_GROUP_WINDOW_DAYS=14
LIVE_QUEUE_MAXSIZE=1000
LIVE_WORKERS=1
TELEGRAM_NOTIFY_ENABLED=false
TELEGRAM_NOTIFY_TARGET=@my_channel
TELEGRAM_NOTIFY_HISTORY=false
CANDIDATE_BOT_ENABLED=false
CANDIDATE_BOT_TOKEN=
CANDIDATE_BOT_CHANNEL=@my_beta_vacancies_channel
CANDIDATE_BOT_ALLOWED_USER_IDS=123456789
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
make candidate-bot # личный Bot API интерфейс кандидата (long polling)
make history   # обработать сообщения за последнюю неделю
make discover  # найти каналы среди текущих Telegram dialogs
make channels  # alias для make discover
make sync-channels # добавить чаты из Telegram-папки в TARGET_CHANNELS
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
PYTHONPATH=src venv/bin/python scripts/run_candidate_bot.py
PYTHONPATH=src venv/bin/python scripts/parse_history.py
PYTHONPATH=src venv/bin/python scripts/auth.py
PYTHONPATH=src venv/bin/python scripts/discover_channels.py
PYTHONPATH=src venv/bin/python scripts/sync_channels.py
```

Discovery по умолчанию ищет каналы и группы, в названии которых есть `job`,
`ваканс`, `вакансия`, `вакансии`, `career`, `hr`, `work`, `remote`, `go`,
`golang`, `it` или `работа`. Для приватной беседы с другим названием укажите
поиск явно:

```bash
PYTHONPATH=src venv/bin/python scripts/discover_channels.py --query "Ханти"
```

Скрипт печатает `username` или числовой `id`, который можно добавить в
`TARGET_CHANNELS`, и сохраняет найденные диалоги в
`DATA_DIR/found_channels.json` (`data/found_channels.json` локально).

### Автоматическое добавление источников из Telegram-папки

Создайте в Telegram папку **«Вакансии»** и добавляйте в неё каналы и группы,
которые нужно мониторить. `make sync-channels` читает эту папку и атомарно
добавляет отсутствующие чаты в `TARGET_CHANNELS` в `.env`. Новые источники
записываются как числовые Telegram ID: это не зависит от последующего
переименования или смены `@username`. Уже настроенные источники не удаляются.

Название папки задаёт `TELEGRAM_CHANNELS_FOLDER` (по умолчанию `Вакансии`):

```bash
TELEGRAM_CHANNELS_FOLDER="Go вакансии" make sync-channels
```

Скрипт должен быть единственным процессом, использующим этот `SESSION_NAME`:
перед локальным запуском остановите `make run`. После изменения `.env`
перезапустите live-бот, чтобы Telethon начал слушать добавленные чаты.

На Docker-стенде используйте `scripts/sync_channels_on_stand.sh`, а не
`docker compose run` вручную. Скрипт корректно останавливает bot, освобождает
Telegram session, запускает синхронизацию в одноразовом контейнере, обновляет
host `.env` и пересоздаёт bot с новой конфигурацией. При любой ошибке trap
запускает bot обратно:

```bash
cd /home/deploy/apps/dev-job-radar
bash scripts/sync_channels_on_stand.sh
```

Для запуска каждые 30 минут установите приложенные systemd unit и timer
(путь и пользователь `deploy` в unit соответствуют инструкции развёртывания):

```bash
sudo install -m 0644 deploy/systemd/dev-job-radar-channel-sync.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/dev-job-radar-channel-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dev-job-radar-channel-sync.timer
systemctl list-timers dev-job-radar-channel-sync.timer
```

Логи синхронизации доступны через
`journalctl -u dev-job-radar-channel-sync.service`. Таймер добавляет небольшую
случайную задержку до двух минут; это уменьшает одновременные обращения к
Telegram после перезагрузки сервера.

При старте приложение восстанавливает дедупликацию из `data/state.jsonl` и
загружает legacy-ссылки из старого первого листа. Ссылки и стабильные ID
блокируют повторную обработку бессрочно, текстовые хэши — в течение настроенного
TTL. ID строится из Telegram-канала и ID сообщения, например
`remotegeekjob_40909`, а не из номера строки таблицы. JSONL отмечается только
после успешной записи в оба новых листа. Если полный лист записался, а краткий
нет, следующая обработка обнаружит полный ID и восстановит только краткую
строку. Старые листы и данные не изменяются.

### Консервативные группы репостов

Точная JSONL-дедупликация по Telegram-ссылке, стабильному ID и текстовому
отпечатку остаётся первым барьером. Для новых, уже извлечённых вакансий после
него включена локальная SQLite-группировка репостов. Она не вызывает LLM и не
мигрирует исторические строки Sheets или JSONL.

Группа создаётся только в пределах `VACANCY_GROUP_WINDOW_DAYS` (по умолчанию
14 дней) и только при одном из строгих условий: совпадает нормализованный
контакт, совпадает application URL (после удаления tracking-параметров), либо
одновременно совпадают нормализованные компания и название должности. Похожий
текст, стек, грейд или одна компания сами по себе никогда не объединяют
карточки. Поэтому приоритет — не допустить ложного объединения.

Каноническая публикация продолжает попадать в оба Google Sheets и Telegram-
уведомления. Для подтверждённого репоста новая карточка не создаётся; его
ссылка, канал, причина объединения и время сохраняются в
`data/vacancy_groups.sqlite3`. В админ-панели на экране «Группы» доступна
безопасная история источников и действие «Разъединить». Оно делает публикацию
новой канонической группой и запрещает автоматическое повторное объединение
этой пары. Данные исторических вакансий не обрабатываются, пока не будет
выполнен отдельный явный импорт.

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

## Telegram-уведомления

`TELEGRAM_NOTIFY_*` сохранены для эксплуатационных алертов и прочих служебных
сообщений Telethon. Они больше не публикуют вакансии: legacy-текстовый пост
отключён, поэтому новая вакансия не может появиться второй копией. Обработка
истории также не публикует вакансии в канал.

Пользовательские карточки настраиваются отдельно через `CANDIDATE_BOT_*` ниже.
Если Bot API недоступен, экспорт в Sheets и exact-дедупликация продолжаются, а
в telemetry записывается `notification_error`; legacy-копия как fallback не
отправляется.

## Личный Telegram-интерфейс кандидата (private beta)

Для действий кандидатов используется отдельный Telegram Bot API-бот. Он не
заменяет Telethon userbot: тот по-прежнему читает источники и экспортирует
вакансии. Bot API-бот публикует интерактивную карточку в общий beta-канал и
ведёт личные статусы пользователей в локальном SQLite. Он работает через long
polling, поэтому публичный домен и HTTPS не нужны.

Создайте бота через BotFather, добавьте его администратором в отдельный общий
канал вакансий и включите только перечисленных тестировщиков:

```dotenv
CANDIDATE_BOT_ENABLED=true
CANDIDATE_BOT_TOKEN=token_from_botfather
CANDIDATE_BOT_CHANNEL=@my_beta_vacancies_channel
CANDIDATE_BOT_ALLOWED_USER_IDS=123456789,987654321
# Необязательно: по умолчанию data/candidate_bot.sqlite3
CANDIDATE_BOT_DB_PATH=data/candidate_bot.sqlite3
```

Токен — секрет: храните его только в `.env`, не передавайте в команды и не
добавляйте в Git. `CANDIDATE_BOT_ALLOWED_USER_IDS` содержит именно числовые
Telegram ID; при пустом списке beta-доступ не выдаётся. При включённом режиме
live-процесс отправляет в `CANDIDATE_BOT_CHANNEL` карточки с кнопками
«Открыть», «Сохранить», «Откликнулся», «Не подходит» и «Пожаловаться».
Для одного стабильного vacancy ID карточка заявляется в SQLite до отправки и
публикуется не более одного раза, в том числе после retry или restart.

Запустите long polling вторым процессом:

```bash
make candidate-bot
```

В Docker сервис находится в отдельном profile и не запускается по умолчанию:

```bash
docker compose --profile candidate up -d candidate-bot
```

После `/start` beta-кандидат видит «Новые», «Мои отклики», «Сохранённые» и
«Скрытые». Каждый раздел открывает одну редактируемую карточку с индикатором
например «Новые · 3 из 18» и навигацией «‹ Назад» / «Вперёд ›»; переходы не
создают новых сообщений. Кнопки в личном чате позволяют менять этап на отклик,
ответ, интервью, оффер или отказ и оставляют кандидата в текущей позиции.
Нажатия на сообщение общего канала меняют только строку этого пользователя и
не редактируют карточку для остальных.

«Пожаловаться» предлагает нейтральные причины: подозрительная вакансия,
дубликат, неактуальна или другое. Сигнал сохраняется локально для
администратора и никогда не публикуется другим кандидатам как отзыв о
компании.

## Структура проекта

```text
src/
  tg_vacancy_bot/
    __init__.py
    config.py                 # загрузка настроек
    channel_sync.py           # сравнение папки Telegram и TARGET_CHANNELS
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
      notifier.py             # HTML-уведомления в Telegram-канал
      bot_api.py              # минимальный async Telegram Bot API клиент
      candidate_notifier.py   # интерактивные карточки beta-канала
      candidate_bot.py        # long polling и личный workflow кандидата
      candidate_store.py      # идемпотентная SQLite-схема действий/жалоб
    storage/
      sheets.py               # экспорт Google Sheets
      vacancy_groups.py       # строгие группы репостов и источники SQLite
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
  run_candidate_bot.py        # личный Bot API интерфейс кандидата
  parse_history.py            # обработка истории за семь дней
  auth.py                     # Telegram-авторизация по QR-коду
  discover_channels.py        # поиск каналов по ключевым словам
  sync_channels.py            # разовое обновление .env из Telegram-папки
  sync_channels_on_stand.sh   # безопасная синхронизация на Docker-стенде
deploy/
  systemd/                    # timer для периодической синхронизации на VPS
tests/                        # изолированные автоматические тесты
  test_pipeline.py            # префильтр и текстовые отпечатки
  test_dedupe_state.py        # долговременная дедупликация JSONL
  test_processor.py           # контракты экспорта общего pipeline
  test_notifier.py            # формат и отправка Telegram-уведомлений
  test_llm_schemas.py         # схема и устойчивость JSON-анализа
  test_models.py              # грейды, зарплата, условия и стек
  test_sheets.py              # полная/краткая строки и частичный сбой
data/
  .gitkeep
```

## Веб-панель управления

Панель позволяет управлять не секретными настройками без ручного редактирования
`.env`: источниками из Telegram-папки, включением мониторинга и уведомлений,
окном history, ключевыми словами, дедупликацией, параметрами Mistral и
названиями листов. Изменение сохраняется в `data/admin/settings.json` с
версионностью. Все значения, которые читает live-процесс при старте (включая
источники, мониторинг, retention и алерты), применяются после явного действия
**Перезапуск**. Это необходимо, поскольку Telethon handler формируется при
старте процесса.

В веб-интерфейс и API **никогда** не попадают `API_HASH`, `MISTRAL_API_KEY`,
Google Service Account JSON, Telegram session, URL таблицы с credential query
или числовые Telegram chat IDs. Для Mistral и Google показывается лишь статус
настройки. Их ротация выполняется через production secrets и перезапуск.

### Локальный запуск панели

Добавьте в локальный `.env` отдельные значения (не коммитьте их):

```dotenv
ADMIN_PASSWORD=choose-a-long-unique-password
ADMIN_SESSION_SECRET=generate-with-secrets-token-urlsafe-48
ADMIN_COOKIE_SECURE=false
```

Затем соберите frontend и запустите FastAPI:

```bash
make web-check
ADMIN_STATIC_DIR="$PWD/web/out" PYTHONPATH=src \
  venv/bin/python -m uvicorn tg_vacancy_bot.admin.api:app --reload
```

Откройте `http://127.0.0.1:8000`. `ADMIN_COOKIE_SECURE=false` допустим только
для localhost или SSH-туннеля. Для HTTPS он должен быть `true`.

### Панель на текущем VPS

Compose запускает `admin` только на `127.0.0.1:8080`: панель не открыта в
интернет. Доступ с рабочего компьютера:

```bash
ssh -i ~/.ssh/dev-job-radar-vps -L 8080:127.0.0.1:8080 deploy@95.85.250.171
```

После этого откройте `http://127.0.0.1:8080`. SSH-туннель шифрует соединение.
На сервере уже занят порт 443 VPN-службой, поэтому не устанавливайте Nginx или
Caddy поверх него. Когда появятся выделенный DNS-домен и согласованный маршрут
HTTPS, используйте [пример Nginx](deploy/nginx/go-radar-admin.conf.example) и
задайте `ADMIN_COOKIE_SECURE=true`.

После входа доступны прямые private-ссылки: `/` (дашборд), `/sources`,
`/settings`, `/prompt`, `/logs` и `/errors`. Фильтры логов сохраняются в URL,
а светлая/тёмная тема переключается только в браузере администратора.

### Действия панели

- **Сохранить настройки** — валидирует и атомарно обновляет managed JSON;
- **Синхронизировать** — bot освобождает session, читает Telegram-папку и
  сохраняет её состав в persistent settings;
- **История** — временно заменяет live-процесс history-задачей, после её
  завершения Compose снова поднимает live-bot;
- **Перезапуск**, **История** и **Синхронизировать** требуют видимого
  подтверждения. Переключатель monitoring mode сохраняется обычной формой, но
  включается или выключается только после последующего подтверждённого
  **Перезапуска**.

Каждое действие попадает в очищенную историю операций. API использует
HttpOnly signed session и double-submit CSRF token; опасное действие без
`confirmed=true` отвергается сервером.

### Метрики, ошибки и логи

Раздел «Сегодня» показывает точные накопленные события за текущую календарную
дату в часовом поясе `OUTPUT_TIMEZONE`: посты, дошедшие до pipeline, успешно
записанные вакансии, пропуски и ошибки обработки. Для каждого пропуска или
ошибки сохраняется только безопасная причина: пустой текст, дубль ссылки/ID,
дубль fingerprint, include/exclude-prefilter, резюме кандидата, полная
очередь, отказ LLM, ошибка LLM или ошибка экспорта. В
`data/admin/metrics.jsonl` не сохраняется текст поста, поэтому
счётчики переживают restart. Для нового журнала значения появятся после
обновления на эту версию; старые сообщения задним числом не оцениваются.

«Требуют внимания» появляется только для неразрешённых ошибок за последние
семь дней. Повторы объединяются, технические детали очищаются, а «Обновить
статус» требует явного действия администратора и фиксирует решение. Логи
открываются отдельной кнопкой: доступны фильтры уровня, компонента, периода и
поиска, постраничная выдача ограничена 100 строками. Перед записью в
`data/admin/app-logs.jsonl` скрываются ключи, токены, пароли, Bearer headers,
cookie/session данные, credentials и numeric Telegram IDs.

### Retention и эксплуатационные алерты

Панель хранит очищенные observability-данные ограниченное число дней: логи,
ошибки, историю операций и агрегированные метрики. Значения задаются в форме
настроек; очистка выполняется при старте live-процесса атомарно и не затрагивает
`state.jsonl`, Telegram session или managed settings. По умолчанию retention:
логи — 30 дней, остальное — 90 дней.

Эксплуатационные алерты выключены по умолчанию. После включения они используют
уже настроенный `TELEGRAM_NOTIFY_TARGET`, поэтому отдельный токен не нужен.
Контролируются задержка heartbeat, опасное заполнение/переполнение очереди,
серии ошибок Mistral и Google Sheets, а также отсутствие успешного экспорта
при работающем monitoring mode. Для каждого инцидента действует настраиваемый
cooldown; после нормализации приходит одно восстановительное уведомление.

### Источники и инструкции LLM

Кнопка **Источники (N)** открывает адаптивный каталог. Добавляйте только
публичные `@username` либо `t.me/username`: сервер нормализует формат,
отклоняет закрытые/invite-ссылки и дубликаты, затем через текущую Telegram
session проверяет, что username доступен и относится к каналу или группе. Если
проверка не выполнилась, источник не сохраняется. Источник можно выключить; удалить
можно только тот, который был добавлен через UI. `.env`, Telegram-папка и
legacy `additional_channels` сохраняют приоритет как входные источники и не
удаляются из браузера. Любое изменение подписки начинает работать после
подтверждённого **Перезапуска** — это ограничение Telethon handler.

В **LLM-инструкции** редактируется текст извлечения вакансии (20–12000
символов). Встроенная строгая JSON-схема и Pydantic validation не выдаются для
редактирования и не могут быть повреждены UI. Поле не принимает секреты и
токены. «Восстановить значение по умолчанию» требует подтверждения; новое
значение применяется после restart.

### Проверки панели

```bash
make compile
make test                         # Python unit/API/auth tests без внешних API
make web-check                    # TypeScript, Vitest, production Next build
npm --prefix web run test:e2e     # Playwright; предварительно установить browser
docker compose config --quiet
docker build -t dev-job-radar-bot:local .
```

Playwright использует test-only credentials и не обращается к Telegram,
Mistral или Google Sheets. Один раз для него нужен browser:
`npm --prefix web exec playwright install chromium`.

### Production deploy и rollback

`master` проходит Python и frontend проверки в GitHub Actions. Workflow собирает
образ на GitHub runner и передаёт его по SSH в `docker load`; это исключает
зависимость VPS от Docker Hub. Перед загрузкой предыдущий image получает тег
`dev-job-radar-bot:previous`. Скрипт деплоя сохраняет managed settings,
проверяет bot и `/healthz` admin-сервиса, а при неуспехе возвращает предыдущий
image и settings.

Нужные GitHub Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`,
`VPS_KNOWN_HOSTS`. Production `.env` остаётся только на VPS и дополнительно
должен содержать `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET` и
`ADMIN_COOKIE_SECURE`. Пароль выбирает владелец; session secret можно создать
на сервере через `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
Не записывайте эти значения в GitHub Actions logs.

Для ручного отката после неудачного релиза:

```bash
cd /home/deploy/apps/dev-job-radar
docker tag dev-job-radar-bot:previous dev-job-radar-bot:latest
docker compose up -d --remove-orphans
docker compose ps
```

Откат исходного кода делайте отдельным `git revert` и push в `master`; не
используйте `git reset --hard` на сервере.

Файлы `.env`, `credentials.json`, Telegram-сессии и сгенерированные данные остаются локальными и игнорируются Git.

## CI/CD

Workflow [CI](.github/workflows/ci.yml) запускается для каждого push в любую
ветку, Pull Request и ручного запуска. Он не использует Telegram, Mistral или
Google Sheets API и выполняет только локальные проверки:

- синтаксис Python (`make compile`);
- изолированные тесты без marker `live` (`make test`);
- lint через Ruff (`make lint`);
- проверку форматирования Black (`make format-check`);
- аудит runtime-зависимостей через `pip-audit` (`make security`);
- production Docker build (`make docker-build-check`).

Главная локальная команда полностью повторяет CI:

```bash
make install
make ci
```

Для отчёта покрытия используйте `make coverage`. Тесты, которым нужны реальные
Telegram, Mistral или Google Sheets credentials, должны быть помечены
`@pytest.mark.live`; обычные `make test`, `make ci` и workflow CI их не
запускают. Live-проверка остаётся отдельной командой `make smoke` и не входит
в branch/PR checks.

Workflow [Deploy production](.github/workflows/deploy.yml) выполняется только
для `master`. Сначала в отдельном job выполняется `make ci`; deploy job зависит
от него и запускается только при успешных проверках и
`github.ref == 'refs/heads/master'`. Push и Pull Request из feature/fix/chore/
dev веток выполняют только CI и никогда не получают deploy.

Production deploy использует GitHub Environment `production` и следующие
Environment Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`,
`VPS_KNOWN_HOSTS`. В них не должны попадать `.env`, Google credentials,
Telegram session или любые API keys: эти файлы остаются только на VPS.

## Production deployment

Production использует один Docker Compose service `bot` без опубликованных
портов. Контейнер работает не от root, автоматически перезапускается политикой
`unless-stopped` и получает SIGTERM напрямую. Постоянные файлы находятся
в `/app/data` и bind-mounted из `./data` на VPS. Google credential
монтируется отдельно и read-only.

### Требования к VPS

- Ubuntu 24.04;
- пользователь `deploy` с рабочим SSH-входом;
- Docker Engine и Docker Compose plugin;
- доступ пользователя `deploy` к Docker;
- каталог `/home/deploy/apps/dev-job-radar`;
- минимум несколько гигабайт свободного диска и настроенный swap.

Проверка:

```bash
ssh deploy@<VPS_HOST>
docker --version
docker compose version
docker info >/dev/null
```

Не добавляйте `deploy` в `sudoers` без необходимости. Для Docker
обычно достаточно членства в группе `docker`:

```bash
sudo usermod -aG docker deploy
newgrp docker
```

После команды нужно заново войти по SSH.

### Read-only Deploy Key: VPS → GitHub

На VPS под пользователем `deploy`:

```bash
install -d -m 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/dev-job-radar-github -C "dev-job-radar deploy key"
```

Файл без `.pub` — приватный и остаётся только на VPS. Содержимое
`~/.ssh/dev-job-radar-github.pub` добавьте в GitHub:
`Repository Settings → Deploy keys → Add deploy key`. Доступ на запись
не включайте.

Настройте отдельный identity:

```sshconfig
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/dev-job-radar-github
    IdentitiesOnly yes
```

Затем:

```bash
chmod 600 ~/.ssh/config ~/.ssh/dev-job-radar-github
ssh -T git@github.com
```

GitHub обычно отвечает, что аутентификация успешна, но shell access не
предоставляется.

### Первое клонирование и локальные production-файлы

```bash
mkdir -p /home/deploy/apps
cd /home/deploy/apps
git clone git@github.com:mmmakskl/dev-job-radar.git
cd dev-job-radar
git branch --show-current
```

Последняя команда должна вывести `master`.

Создайте production `.env` непосредственно на VPS:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Не коммитьте и не выводите его содержимое. Compose принудительно задаёт
`DATA_DIR=/app/data`, `STATE_FILE_PATH=/app/data/state.jsonl` и
`GOOGLE_CREDENTIALS_PATH=/run/secrets/google-credentials.json`.

Подготовьте постоянные каталоги для non-root UID контейнера `10001`:

```bash
mkdir -p data secrets
sudo chown -R 10001:10001 data secrets
sudo chmod 700 data secrets
```

Передайте Google service-account JSON на VPS безопасным каналом во временный
файл, затем установите его без вывода содержимого:

```bash
sudo install -o 10001 -g 10001 -m 0400 \
  /path/to/uploaded/google-credentials.json \
  /home/deploy/apps/dev-job-radar/secrets/google-credentials.json
```

Исходный временный файл после проверки можно удалить вручную. Production-файл
никогда не добавляется в Git.

### Telegram session и миграция state

Рекомендуемый первый вход выполняется прямо в одноразовом Compose container:

```bash
cd /home/deploy/apps/dev-job-radar
docker compose config --quiet
docker compose build
docker compose run --rm bot python scripts/auth.py
```

QR-код появится в терминале, а session сохранится в
`./data/my_account.session`. Не запускайте одновременно auth, history и
live с одной session.

Если нужно перенести старые локальные файлы, сначала скопируйте их на VPS под
временными именами, затем:

```bash
sudo install -o 10001 -g 10001 -m 0600 \
  /tmp/my_account.session \
  /home/deploy/apps/dev-job-radar/data/my_account.session
sudo install -o 10001 -g 10001 -m 0600 \
  /tmp/state.jsonl \
  /home/deploy/apps/dev-job-radar/data/state.jsonl
```

Не переносите `.session-journal` во время работающего Telethon-процесса.
Сначала корректно остановите локальный bot.

### Первый ручной запуск

```bash
cd /home/deploy/apps/dev-job-radar
docker compose config --quiet
docker compose build
docker compose up -d --remove-orphans
docker compose ps
docker compose logs --tail=100 bot
```

Убедитесь, что `bot` имеет статус `running` и в логах есть успешная
Telegram-авторизация. Только после ручной проверки production можно считать
запущенным.

Управление:

```bash
make docker-status
make docker-logs
docker compose restart bot
make docker-down
make docker-up
```

Для последующих ручных fast-forward deploy используется та же логика, что и в
GitHub Actions:

```bash
cd /home/deploy/apps/dev-job-radar
bash scripts/deploy.sh
```

Обычный production deploy получает уже собранный image из GitHub Actions через
SSH и поэтому не зависит от Docker Hub на VPS. Локальный ручной `deploy.sh`
может собрать image сам, но для этого ему нужен доступ к registry.

`docker compose down` не удаляет bind-mounted `data`. Никогда не
используйте `down -v` и не удаляйте каталог `data`: там находятся
Telegram session и дедупликация.

### GitHub Actions → VPS

Создайте отдельный SSH-ключ C на доверенной машине. Его нельзя переиспользовать
как личный ключ или GitHub Deploy Key:

```bash
ssh-keygen -t ed25519 \
  -f ~/.ssh/github-actions-dev-job-radar \
  -C "github-actions dev-job-radar"
ssh-copy-id -i ~/.ssh/github-actions-dev-job-radar.pub deploy@<VPS_HOST>
```

Приватный файл `~/.ssh/github-actions-dev-job-radar` добавляется только в
GitHub Secret `VPS_SSH_KEY`. Удобнее сделать это без печати:

```bash
gh secret set VPS_SSH_KEY < ~/.ssh/github-actions-dev-job-radar
gh secret set VPS_HOST
gh secret set VPS_USER
```

Для `VPS_USER` используйте `deploy`. IP/hostname хранится только в
`VPS_HOST`.

### VPS_KNOWN_HOSTS и проверка fingerprint

Сначала на VPS через уже доверенное соединение получите fingerprint host key:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

На Mac получите публичный key, не отключая host verification:

```bash
ssh-keyscan -t ed25519 <VPS_HOST> > /tmp/dev-job-radar-known-hosts
ssh-keygen -lf /tmp/dev-job-radar-known-hosts
```

Сравните SHA256 fingerprints по двум независимым каналам. Только при полном
совпадении добавьте файл в Secret:

```bash
gh secret set VPS_KNOWN_HOSTS < /tmp/dev-job-radar-known-hosts
```

Итого workflow использует четыре Secrets:

- `VPS_HOST`;
- `VPS_USER`;
- `VPS_SSH_KEY`;
- `VPS_KNOWN_HOSTS`.

Workflow запускается после push в `master` и вручную через
`workflow_dispatch`, если выбран `master`. Перед SSH deploy он выполняет
`make ci`, TypeScript, Vitest, Next production build и Docker build. Затем
передаёт image по SSH в `docker load`, делает fast-forward pull, проверяет
Compose, bot и admin healthcheck. `.env`, credentials, session и `data` не
передаются из GitHub Actions.

После успешного первого ручного запуска сделайте следующий push в `master`
или откройте `Actions → Deploy production → Run workflow`, затем проверьте
job и на VPS:

```bash
cd /home/deploy/apps/dev-job-radar
docker compose ps
docker compose logs --tail=100 bot
```

### Три SSH-ключа: не перепутайте

**Ключ A — Mac → VPS**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/dev-job-radar-vps -C "Mac to VPS"
ssh-copy-id -i ~/.ssh/dev-job-radar-vps.pub deploy@<VPS_HOST>
ssh -i ~/.ssh/dev-job-radar-vps deploy@<VPS_HOST>
```

- приватный: `~/.ssh/dev-job-radar-vps`, остаётся на Mac;
- публичный: файл с `.pub`, попадает в
  `/home/deploy/.ssh/authorized_keys`.

**Ключ B — VPS → GitHub Deploy Key**

- создаётся на VPS как `~/.ssh/dev-job-radar-github`;
- публичный `.pub` добавляется в Repository Settings → Deploy keys;
- приватный остаётся только на VPS;
- Deploy Key только read-only;
- проверка: `ssh -T git@github.com`.

**Ключ C — GitHub Actions → VPS**

- отдельный ключ `github-actions-dev-job-radar`;
- публичный ключ добавляется в
  `/home/deploy/.ssh/authorized_keys`;
- приватный добавляется в GitHub Secret `VPS_SSH_KEY`;
- приватный ключ не отправляется в чат и не коммитится.

### Recovery и контроль диска

При неудачном deploy сначала смотрите:

```bash
docker compose ps
docker compose logs --tail=200 bot
git status --short
git log -5 --oneline
```

Исправление выпускается новым commit либо безопасным `git revert` с
последующим push в `master`. Не используйте `git reset --hard` или
`git clean` на сервере.

Контроль места:

```bash
df -h /
docker system df
docker image ls
docker image prune -f
```

`docker image prune -f` удаляет только dangling images. Не запускайте
`docker system prune --volumes`: volumes и bind-mounted данные должны
сохраняться между сборками.
