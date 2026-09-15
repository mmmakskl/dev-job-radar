# План архитектуры для расширяемых источников вакансий

## Назначение и границы

Этот документ описывает постепенный переход от Telegram-центричной обработки к
архитектуре, в которую можно добавлять новые источники вакансий без изменения
правил обработки, публикации и пользовательских сценариев. Большой переход
к реестру и dual-write ниже отложен и не является prerequisite для новых
источников. На 15 сентября 2026 года общий поиск и официальный Threads API
реализованы минимальным дополнением к существующей архитектуре. Greenhouse,
Lever и Ashby не реализованы.

Отложенное предложение для будущей реализации: SQLite-реестр вакансий станет источником
истины. Google Sheets останется идемпотентной проекцией реестра, а Candidate
Bot — проекцией опубликованных вакансий и пользовательских действий. Переход
выполняется поэтапно с сохранением существующих Telegram ID, ссылок,
callbacks, команд и recovery-семантики.

## Реализованное дополнение: общий поиск и Threads

Вместо обязательной миграции registry/dual-write добавлены:

```text
Admin /search или личный Candidate /search
  -> search/store.py: дополнительные таблицы существующей admin.sqlite3
  -> search/service.py внутри существующего live-процесса
     -> Telegram: read-only published title/company/summary из Candidate cache
     -> Premium: существующий PremiumSearchStore/Service и его история
     -> Threads: официальный keyword_search, rules и Mistral для неоднозначного
  -> частичные результаты и отдельные состояния источников
  -> admin save/publish: существующий processor -> оба Sheets -> JSONL -> publisher
```

Candidate запускает только preview. Админ может выбрать preview, save или
save_publish с подтверждением; в preview запись и публикация не выполняются.
Периодический сбор Threads раз в шесть часов также использует preview, его
checkpoint и бюджет хранятся в admin SQLite. Отдельного фонового сервиса и
новой основной базы вакансий нет. Telegram ID и ссылки сохраняются;
Threads использует явный `threads:<id>` и оригинальную ссылку поста.

`THREADS_ENABLED=false` по умолчанию не мешает локальному/Premium поиску.
Требуется официальный пользовательский токен с `threads_basic` и одобренным
`threads_keyword_search`; без одобренного доступа публичных чужих постов не
будет. Локальный Telegram cache не включает старые записи только из Sheets.
Публичный Threads smoke не выполнен без credentials; offline тесты мокируют
HTTP, классификацию, rate limits и ошибки. Полный setup и ограничения находятся
в [README](../README.md#общий-поиск-telegram-premium-и-threads).

Оставшаяся часть документа описывает исходную базу и **отложенную** целевую
миграцию. Её registry, dual-write и перенос всех modules не следует считать
уже реализованными или обязательными для запуска Threads.

## Текущее состояние

### Потоки обработки

Основной поток Telegram проходит через `VacancyProcessor` в
`src/tg_vacancy_bot/pipeline/processor.py`:

```text
Telethon live / история Telegram
  -> VacancyProcessor
  -> keyword и profile prefilter
  -> Mistral со строгой схемой VacancyAnalysis
  -> Google Sheets: полный и краткий листы
  -> JSONL state и SQLite-группы репостов
  -> Candidate Bot для опубликованных live-вакансий
```

`scripts/run_live.py` ставит live-сообщения в ограниченную `asyncio.Queue` и
последовательно обрабатывает их worker-ом. `scripts/parse_history.py`
последовательно проходит историю активных Telegram-источников. Оба сценария
используют тот же `VacancyProcessor`.

Premium Search выполняется в процессе live listener и использует уже
авторизованную Telethon session:

```text
Админ-панель / Premium API
  -> PremiumSearchStore: jobs, результаты и ручные действия
  -> PremiumSearchService
  -> Telegram Premium Search через Telethon
  -> Premium-анализ, ранжирование и решение
  -> внутренний persist_analyzed_message у VacancyProcessor
  -> Sheets / группы / публикация, если выбран соответствующий режим
```

Следовательно, Premium Search уже разделяет часть бизнес-логики с основным
pipeline, но делает это через внутренние методы processor и частично повторяет
этапы анализа и подготовки данных. Это является основным кандидатом на
выделение контрактов и сервисов.

### Точки входа и интерфейсы

| Точка | Назначение |
|---|---|
| `scripts/run_live.py` | Live Telethon listener, очередь, worker Premium Search и выполнение действий control plane |
| `scripts/parse_history.py` | Пакетный импорт истории Telegram |
| `scripts/run_candidate_bot.py` | Отдельный Bot API long-polling worker для кандидатов |
| `scripts/auth.py` | QR-авторизация и управление Telethon session |
| `scripts/discover_channels.py` | Поиск Telegram-каналов и первичный upsert источников |
| `scripts/sync_channels.py` | Синхронизация Telegram-папки с persistent SQLite |
| `src/tg_vacancy_bot/admin/api.py` | FastAPI control plane для админ-панели, источников и Premium jobs |

Next.js-панель управляет источниками и Premium jobs через FastAPI и SQLite. Она
не должна напрямую владеть Telethon session. Candidate Bot является отдельным
Bot API-интерфейсом: прежние разделы работают с опубликованными вакансиями и
действиями кандидатов. Дополнительный личный поиск создаёт preview-задания в
admin SQLite и не выполняет внешние запросы напрямую.

### Текущее хранение

| Хранилище | Текущая роль |
|---|---|
| `data/admin/admin.sqlite3` | Настройки control plane, список и metadata Telegram-источников, административные действия и telemetry |
| `data/premium_search.sqlite3` | Очередь Premium Search, runs, результаты, ранжирование и ручные действия; создаётся только при включённом Premium Search |
| `data/candidate_bot.sqlite3` | Карточки, состояние доставки в канал, персональные действия и жалобы кандидатов |
| `data/vacancy_groups.sqlite3` | Консервативные группы репостов, источники публикаций и ручное разъединение |
| `data/state.jsonl` | Append-only состояние успешного экспорта для дедупликации |
| `data/state.jsonl.claims.sqlite3` | Временные SQLite claims между конкурентными workers |
| Google Sheets | Полная и краткая пользовательские проекции вакансий |

Пути могут переопределяться через `DATA_DIR` и соответствующие настройки.
Telethon session также хранится локально в SQLite-файле, но не является
хранилищем вакансий.

### Дедупликация и публикация

Точная дедупликация в `pipeline/dedupe_state.py` использует три ключа:

- постоянную ссылку Telegram-поста;
- стабильный vacancy ID, построенный из Telegram-канала и ID сообщения;
- SHA-256 нормализованного текста с TTL `TEXT_HASH_TTL_DAYS`.

Ссылка и vacancy ID блокируют повторную обработку бессрочно; текстовый хэш
блокирует её только в пределах TTL. Перед внешними вызовами SQLite claims
атомарно резервируют link/hash/ID для конкурентных workers. После точной
дедупликации `VacancyGroupStore` отдельно объединяет только безопасно
подтверждённые репосты. Это не заменяет exact dedupe и не требует LLM.

JSONL отмечается после успешного экспорта. Экспорт в Sheets считается
завершённым только после записи в оба листа; при частичной записи сохранённый
полный ID позволяет восстановить недостающую краткую строку. Публикация
Candidate Bot следует после сохранения, а её собственное состояние доставки
защищает от повторной отправки карточки.

## Отложенная целевая архитектура

Целевая структура разделяет модели, адаптеры источников, прикладные сервисы и
проекции:

```text
models/        SourceItem, VacancyAnalysis, canonical Vacancy, publication states
sources/       Telegram channel/history adapters and Premium Search adapter
services/      ingestion, analysis, deduplication, publication, search-job orchestration
repositories/  SQLite registry, source metadata, jobs, dedupe, projections, candidate actions
workers/       live ingestion, history batch, Premium job worker
bot/           Candidate Bot handlers and admin API adapters
```

Это логическое разбиение, а не требование одномоментно переместить все файлы.
Старые entry points сохраняются и сначала становятся composition root для новых
сервисов.

### Source-neutral вход

В бизнес-логику должен входить `SourceItem` — независимый от Telegram конверт:

| Поле | Назначение |
|---|---|
| `source_type` | Тип источника, например `telegram_channel` или `premium_search` |
| `external_id` | Стабильный ID записи в исходной системе |
| `url` | Ссылка на запись, если источник её предоставляет |
| `text` | Текст для предварительной обработки и анализа |
| `published_at` | Дата публикации в источнике |
| `metadata` | Метаданные источника: имя, канал, сообщения, автор, исходные признаки и т. п. |

Telegram link не должен быть обязательным входом бизнес-логики. Telegram-
адаптер по-прежнему формирует существующие ссылку и vacancy ID, чтобы не
сломать совместимость. Для источника без URL уникальность строится из
`source_type` и стабильного `external_id`, а не из искусственной Telegram-
ссылки.

`VacancyAnalysis` сохраняется как совместимая строгая модель результата LLM.
После анализа сервис создаёт каноническую `Vacancy`, связанную с исходным
`SourceItem`; это позволяет отделить исходный текст и идентичность источника от
нормализованных полей вакансии.

### Реестр и проекции

Versioned SQLite registry будет хранить как минимум:

- source items и их стабильные внешние идентификаторы;
- канонические вакансии и версию нормализованных данных;
- связи источника, вакансии и публикации;
- dedupe keys и их срок действия;
- состояния экспорта и delivery для каждой проекции.

В реестре нужны явные publication states, например: получено, проанализировано,
принято, экспортируется, экспортировано, публикация ожидается, опубликовано и
ошибка доставки. Точные названия и transitions определяются контрактом до
переключения writers. Состояние проекции должно позволять повторить только
незавершённую доставку, не создавая вторую вакансию.

Google Sheets становится идемпотентной проекцией реестра с сохранением
нынешнего правила двух листов. Candidate Bot получает проекцию публикаций и
сохраняет личные действия через собственный repository. Он не получает доступа
к search services. Admin API создаёт jobs и читает их состояние; исполнение
jobs и Telethon-вызовы остаются worker-ам.

### Источники и Premium Search

Telegram channel/live и history реализуются отдельными source adapters, но
сохраняют текущие порядок обработки, ID и ссылки. Premium Search сохраняет
владение очередью runs, ранжированием и ручными действиями в своём repository,
но переходит на contracts `SourceItem` и сервисов анализа/сохранения вместо
вызова внутренних методов processor.

Premium worker по-прежнему исполняется владельцем live Telethon session. Это
ограничение нельзя обходить отдельным web-процессом или параллельным client.

## Последовательность миграции

1. Добавить контракты, composition root и versioned SQLite registry без
   переключения существующих entry points.
2. Ввести Telegram adapter, сохранив текущие Telegram links, vacancy ID и
   порядок обработки live/history.
3. Включить dual-write: registry и текущие JSONL/Sheets. Завершённый экспорт
   по-прежнему означает успешную запись в оба Sheets-листа.
4. Импортировать из JSONL только известные dedupe keys. Не создавать
   фиктивные вакансии из неполных legacy-событий.
5. Перенести по одному: группировку репостов, Premium jobs и Candidate
   publication. Существующие SQLite-файлы и их recovery-семантика сохраняются.
6. После сверки dual-write перевести dedupe и read paths на registry. JSONL
   оставить append-only совместимым зеркалом до отдельного решения о его
   выводе из эксплуатации.

Каждый этап должен иметь обратимый feature flag или возможность продолжить
текущий путь чтения. Не следует объединять переключение источника истины с
изменением пользовательского API.

## Риски и инварианты

| Риск | Требование к миграции |
|---|---|
| Новый источник не даёт Telegram URL | URL опционален; идентичность определяется source type и external ID |
| Несколько SQLite-файлов и concurrent writers | Установить владельца данных, транзакционные claims и идемпотентные projection writes |
| JSONL содержит неполные legacy payload | Импортировать только dedupe keys, не реконструировать вакансии |
| Сбой Google Sheets или Bot API | Хранить состояния проекции и безопасно повторять незавершённую доставку |
| Telethon session нельзя использовать конкурентно | Live процесс остаётся единственным владельцем Telethon для Premium и sync-сценариев |
| Смена ключей дедупликации | Сохранить exact semantics постоянных ссылок/ID и TTL текстового SHA-256 |
| Premium и Candidate workflows | Сохранить существующие очереди, ручные действия, callbacks и Telegram-команды до отдельной миграции UI |

## Ожидаемые точки изменения

При реализации миграции первыми кандидатами на изменение являются:

- `src/tg_vacancy_bot/config.py`;
- `src/tg_vacancy_bot/pipeline/processor.py`;
- `src/tg_vacancy_bot/pipeline/dedupe_state.py`;
- `src/tg_vacancy_bot/telegram/sources.py`;
- `src/tg_vacancy_bot/premium_search/service.py`;
- `src/tg_vacancy_bot/premium_search/store.py`;
- `src/tg_vacancy_bot/storage/sheets.py`;
- `src/tg_vacancy_bot/storage/vacancy_groups.py`;
- `src/tg_vacancy_bot/telegram/candidate_store.py`;
- `scripts/run_live.py` и `scripts/parse_history.py`;
- `src/tg_vacancy_bot/admin/api.py`;
- соответствующие изолированные тесты.

Новые sources, services, repositories, workers и bot adapters добавляются
рядом с текущими модулями, пока новый composition root не станет полным.

## Проверка будущей миграции

На момент подготовки плана `make check` проходит с 212 тестами; остаётся одно
предупреждение FastAPI/Starlette о deprecated `TestClient`. При каждой
реализации миграционного этапа необходимы:

- contract-тесты source adapters;
- migration- и idempotency-тесты SQLite registry;
- compatibility-тесты Telegram ID и links;
- recovery-тесты Google Sheets и Candidate-проекций;
- регрессионные тесты Premium Search и Candidate flows.

Помимо новых тестов, должны продолжать проходить `make check`, `make lint` и
`make format-check`.
