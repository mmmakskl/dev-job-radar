# Telegram Vacancy Parser

Умный Telegram-парсер вакансий: отслеживает заданные каналы, предварительно фильтрует сообщения по Go/Golang, отбрасывает резюме/профили кандидатов, анализирует вакансии через Mistral AI и сохраняет подходящие результаты в Google Sheets. При включённом флаге он также публикует краткие уведомления в отдельный Telegram-канал. Live-мониторинг принимает сообщения через ограниченную asyncio.Queue и передаёт их последовательному worker, а обработка истории остаётся последовательной. Ответ Mistral проверяется по строгой схеме, а временные API/JSON-ошибки получают не более двух попыток. Двухуровневая дедупликация хранит ссылки бессрочно, а SHA-256 нормализованного текста — 30 дней в append-only JSONL. В state попадают только релевантные вакансии после успешной записи в Google Sheets.

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
TELEGRAM_CHANNELS_FOLDER=Вакансии
MISTRAL_API_KEY=your_mistral_api_key
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...
OUTPUT_TIMEZONE=Europe/Moscow
GOOGLE_SHEET_FULL_TITLE=Вакансии — полные
GOOGLE_SHEET_SHORT_TITLE=Вакансии — кратко
STATE_FILE_PATH=data/state.jsonl
TEXT_HASH_TTL_DAYS=30
LIVE_QUEUE_MAXSIZE=1000
LIVE_WORKERS=1
TELEGRAM_NOTIFY_ENABLED=false
TELEGRAM_NOTIFY_TARGET=@my_channel
TELEGRAM_NOTIFY_HISTORY=false
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

После нахождения и успешного сохранения релевантной вакансии в Google Sheets
бот может отправлять краткий пост в ваш Telegram-канал. Уведомления выключены
по умолчанию; ошибка отправки не отменяет запись в Sheets и не мешает дедупликации.

Создайте приватный или публичный канал, добавьте в него Telegram-аккаунт,
авторизованный для Telethon userbot через `make auth`, и дайте ему право
публиковать сообщения, если для канала нужна роль администратора. Это не Bot API:
пишет тот же пользовательский аккаунт, который работает в проекте.

Включите уведомления в `.env`:

```env
TELEGRAM_NOTIFY_ENABLED=true
TELEGRAM_NOTIFY_TARGET=@my_channel
TELEGRAM_NOTIFY_HISTORY=false
```

В `TELEGRAM_NOTIFY_TARGET` укажите публичный username (`@my_channel`) либо
приватный/числовой ID, если он известен и доступен этому аккаунту. Чтобы
выключить отправку, задайте `TELEGRAM_NOTIFY_ENABLED=false` и перезапустите
live-процесс. При включённом флаге без target запуск завершается с понятной
ошибкой конфигурации.

Обработка истории не публикует вакансии по умолчанию, даже когда live-
уведомления включены. Чтобы намеренно отправлять и историю, добавьте:

```env
TELEGRAM_NOTIFY_HISTORY=true
```

Включайте этот флаг осторожно: `make history` может отправить пачку старых
вакансий.

Минимальная последовательность запуска:

```bash
cp .env.example .env
# заполнить Telegram/Mistral/Google
make auth
# включить TELEGRAM_NOTIFY_ENABLED и TELEGRAM_NOTIFY_TARGET
make run
```

Если вакансии появляются в Sheets, но не публикуются в канале, проверьте
`TELEGRAM_NOTIFY_ENABLED`, `TELEGRAM_NOTIFY_TARGET` и права аккаунта в канале.
Если запуск сообщает об `TELEGRAM_NOTIFY_TARGET`, заполните target или выключите
фичу. Если history заспамил канал, выключите `TELEGRAM_NOTIFY_HISTORY`.

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

Deploy сначала пересобирает image с актуальным базовым образом из Docker Hub.
Если Docker Hub временно недоступен на VPS, но там есть предыдущий image бота,
скрипт автоматически пересобирает его с актуальными `src/` и `scripts/` без
сетевой загрузки. При первом развёртывании fallback невозможен: VPS должен
иметь доступ к Docker Hub хотя бы один раз.

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
`workflow_dispatch`, если выбран `master`. Перед SSH deploy он обязан
успешно выполнить полный `make ci`; ручные запуски из других веток пропускают
все deploy jobs. После этого workflow делает только fast-forward pull,
проверяет Compose, собирает image, запускает service, проверяет running status
и удаляет только dangling images. `.env`, credentials, session и `data` не
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
