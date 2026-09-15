# Контекст проекта для передачи работы

## Назначение

Dev Job Radar собирает Go/Golang-вакансии из Telegram. Система принимает
сообщения каналов и групп, отбрасывает нерелевантные тексты и резюме кандидатов,
извлекает структурированные данные через Mistral и сохраняет подходящие
вакансии в полный и краткий Google Sheets-листы. Дополнительно доступны
консервативная группировка репостов, private-beta Candidate Bot и Premium
Search по публичным Telegram-постам. Общий поиск добавляет официальный Threads
API и локальный поиск опубликованных карточек; он доступен в админ-панели и
личном Candidate Bot.

Основная бизнес-логика находится в
`src/tg_vacancy_bot/pipeline/processor.py` (`VacancyProcessor`). Она общая для
live и history Telegram-потоков. Любое расширение источников должно сохранять
эту общность, а не копировать правила фильтрации, дедупликации, сохранения и
публикации.

## Стек

| Область | Технологии |
|---|---|
| Приложение | Python 3.12, asyncio |
| Telegram ingestion и поиск | Telethon, пользовательская Telegram session |
| Анализ | Mistral API через OpenAI Python SDK, строгие схемы и prompts |
| Экспорт | Google Sheets через gspread и Google Auth |
| Админ-интерфейс | FastAPI, Uvicorn, Next.js static export |
| Локальное состояние | JSONL и SQLite с WAL/locking там, где требуется |
| Проверки | pytest, Ruff, Black, Make |

## Основные каталоги

```text
src/tg_vacancy_bot/
  pipeline/        общая обработка, prefilter, fingerprints, JSONL dedupe
  telegram/        Telethon sources/links, Bot API и Candidate storage
  llm/             Mistral client, strict schemas и prompts
  storage/         Google Sheets и группы репостов
  premium_search/  существующие Premium runs, store, service, analyzer и tracks
  search/          общий поиск: настройки, очередь в admin SQLite, worker и личный UI
  threads/         официальный HTTP-адаптер и классификация текстовых постов
  admin/           FastAPI control plane, настройки, действия и telemetry
scripts/           запускаемые entry points
web/               Next.js админ-панель
tests/             изолированные pytest-тесты
docs/              технические документы
```

## Запуск и проверка

```bash
make init
make auth
make run
make history
make candidate-bot
make discover
make sync-channels
make check
make lint
make format-check
```

Для прямого запуска требуется `PYTHONPATH=src`; основные команды —
`scripts/run_live.py`, `scripts/parse_history.py`, `scripts/run_candidate_bot.py`,
`scripts/auth.py`, `scripts/discover_channels.py` и
`scripts/sync_channels.py`. `scripts/test_userbot.py` — интерактивная
проверка с доступом к Telegram, не offline CI-тест.

## Текущая обработка

`run_live.py` принимает новые сообщения Telethon, помещает их в ограниченную
очередь и последовательно вызывает `VacancyProcessor.process_message`.
`parse_history.py` использует тот же processor для исторических сообщений.
Processor применяет keyword/profile prefilter, отправляет подходящий текст в
Mistral, затем сохраняет нормализованную `VacancyAnalysis` в оба листа Google
Sheets. После успешного сохранения обновляется JSONL state; Candidate Bot
сохраняет прежние опубликованные карточки и личные действия кандидатов;
дополнительно личный поиск показывает результаты предпросмотра без публикации.

`telegram/sources.py` содержит работу с Telegram-источниками. Список
активных источников берётся из admin SQLite; discovery и folder sync обновляют
его metadata. Стабильный Telegram vacancy ID строится из канала и ID сообщения,
а прямые links обеспечиваются `telegram/links.py`.

## Premium Search

Premium Search расположен в `src/tg_vacancy_bot/premium_search/`. Его API и
навигация выключены, пока `PREMIUM_GLOBAL_SEARCH_ENABLED=false`. При включении
админ-панель через FastAPI создаёт runs и ручные действия в
`premium_search.sqlite3`; `PremiumSearchService` исполняется внутри live
процесса и использует его Telethon session.

Результаты Premium Search проходят отдельные prefilter/analyzer/ranking шаги,
после чего сервис вызывает внутреннее сохранение `VacancyProcessor`. Это
частичная общая логика, которую при будущем расширении источников нужно
перевести на явные contracts, а не расширять вызовы внутренних методов.

## Хранилища и их владельцы

| Данные | Расположение по умолчанию | Владелец/назначение |
|---|---|---|
| Админ-настройки, источники, actions, telemetry, общие search runs/results/лимиты | `data/admin/admin.sqlite3` | Admin control plane и live worker |
| Premium runs и результаты | `data/premium_search.sqlite3` | Premium Search store/worker |
| Candidate-карточки и действия | `data/candidate_bot.sqlite3` | Отдельный Bot API worker |
| Группы репостов | `data/vacancy_groups.sqlite3` | Основной pipeline |
| Exact dedupe state | `data/state.jsonl` | Append-only зеркало успешного экспорта |
| In-flight claims | `data/state.jsonl.claims.sqlite3` | Конкурентная защита pipeline |
| Пользовательские проекции | Google Sheets | Полный и краткий листы вакансий |
| Авторизация Telegram | `<SESSION_NAME>.session` или путь в `DATA_DIR` | Только Telethon client |

`DATA_DIR` и специальные переменные путей могут менять расположение. Не
коммитить `.env`, `credentials.json`, session-файлы, API-ключи, chat IDs,
экспортированные вакансии и локальные базы данных.

## Ограничения совместимости

- Telethon session не допускает конкурентного использования несколькими
  процессами. Live worker остаётся её владельцем; Premium Search зависит от
  него. Перед history или sync с тем же `SESSION_NAME` live нужно остановить.
- Exact dedupe: постоянные Telegram links и vacancy IDs, плюс TTL для
  SHA-256 нормализованного текста. Это поведение нельзя менять неявно.
- Sheets считаются сохранёнными только после записи в оба листа. Частичный
  экспорт должен быть восстанавливаемым, а не приводить к дубликату.
- Группировка репостов отдельна от exact dedupe и должна оставаться
  консервативной: ложное объединение хуже пропуска репоста.
- Локальный Telegram-поиск читает опубликованные title/company/summary из
  Candidate cache; старые Sheets-only записи недоступны. Личный Candidate Bot
  дополнительно создаёт изолированные preview-запуски по включённым источникам.
- Admin API управляет настройками, источниками и jobs, но не владеет Telethon
  session и не выполняет поиск напрямую.

## Ориентир для следующего архитектурного этапа

Большая миграция к source-neutral contracts, versioned SQLite registry и
dual-write из [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) отложена. Общий поиск
и Threads добавлены поверх существующих хранилищ и workers без обязательного
предварительного рефакторинга. Два листа Sheets, JSONL, Telegram ID, Premium
история и Candidate-действия сохраняют прежнюю роль; Threads ID — `threads:<id>`.


## Реализованный общий поиск (15 сентября 2026)

`search/service.py` исполняет независимые задания Telegram/Premium/Threads в
существующем live-процессе; admin и Candidate Bot только ставят задания.
`search/store.py` добавляет таблицы runs, результатов, действий и лимитов в
существующую admin SQLite, а не создаёт новую основную базу вакансий. Локальный
Telegram-поиск читает только опубликованные карточки Candidate cache и не
сканирует Sheets. Premium подключён через существующие store/service: прежний
раздел `/premium-search`, API, история и тесты сохранены.

Threads выключен по умолчанию. Его официальный `keyword_search` использует
пользовательский токен `threads_basic` + одобренный `threads_keyword_search`.
Без approved public-search scope доступны только свои посты. Параметры доступа,
все девять `THREADS_*` переменных, диапазоны, ручное получение/обновление токена
и ссылки Meta приведены в [README](../README.md#доступ-к-официальному-threads-api).
Токен не показывается в UI/логах. OAuth server, scraping, OCR и ATS crawling
не реализованы. Языковой и географический фильтр Meta отсутствуют; ru/en/und
определяются локально. Правила отсекают очевидные неподходящие тексты, Mistral
обрабатывает неоднозначные.

Фоновые Go-запросы создаются внутри live-процесса раз в шесть часов и работают
в preview; `THREADS_COLLECTION_INTERVAL_HOURS=0` отключает их. По умолчанию
локальный бюджет — 1000 запросов/сутки против максимальных 2200 у Meta.
Админ `/search` предлагает preview/save/save_publish, независимые статусы,
частичные результаты, историю, новый запуск при обновлении и подтверждённые
действия. Candidate `/search`/«Поиск» использует только личный preview и скрывает
review-результаты. Отсутствие Threads credentials не блокирует другие источники.

### Карта добавленных и изменённых файлов

- Источник и анализ: новые `src/tg_vacancy_bot/threads/{source,rules}.py` и
  `src/tg_vacancy_bot/search/terms.py` — официальный API, нормализация,
  курсоры/retry и варианты запросов.
- Общая очередь и исполнение: новые `src/tg_vacancy_bot/search/{settings,store,service,bot}.py`;
  интеграция в `scripts/run_live.py`, `scripts/run_candidate_bot.py`,
  `src/tg_vacancy_bot/admin/api.py` и `telegram/candidate_bot.py`.
- Публикация: `pipeline/processor.py` использует существующее сохранение с
  явным ID Threads; `telegram/candidate_notifier.py` и
  `telegram/vacancy_channel_formatter.py` формируют карточку с исходной ссылкой.
- Веб: новые `web/components/search-panel.tsx`, `search-panel.test.tsx`,
  `search-navigation.test.tsx`, `web/lib/search-api.test.ts`; дополнения в
  `web/app/page.tsx`, `web/app/styles.css`, `web/lib/api.ts`.
- Тесты: `tests/test_threads_source.py`, `test_threads_rules.py`,
  `test_candidate_search.py` и проверки store/service/API общего поиска;
  регрессии существующих Telegram/Premium/publication сценариев.
- Документация и настройки: `.env.example`, `README.md`, этот контекст и
  `ARCHITECTURE_PLAN.md`.

Проверки: `make check`, `make lint`, `make format-check`, `make web-check`.
Обычные тесты используют моки без внешних API; `make smoke` остаётся отдельным
Telegram connectivity test. Публичный Threads smoke без credentials не
выполнялся. Ручной smoke — только отдельное non-production окружение, одобренный
токен и preview с небольшим лимитом; он описан в README.
