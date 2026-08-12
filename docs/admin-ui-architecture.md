# Архитектура панели управления

## Текущее состояние

Приложение — один Python/Telethon процесс `scripts/run_live.py`: Telegram,
prefilter, Mistral, JSONL-дедупликация и Google Sheets. История запускается
отдельным `scripts/parse_history.py`. Оба используют один Telethon session-файл
и не могут работать одновременно. На стенде Compose запускает `bot`; systemd
timer раз в 30 минут синхронизирует папку Telegram, кратко останавливая bot.

Секреты (`API_HASH`, `MISTRAL_API_KEY`, Google credentials и session) остаются
вне Git. `.env` сохраняется для инфраструктурных и секретных значений.

## Выбранная архитектура

```text
Browser (Next.js static export) → FastAPI /api/v1 → data/admin/settings.json
                                      │              data/admin/control.json
                                      └──────────→  bot graceful restart
```

`web/` — Next.js App Router, TypeScript, Tailwind и shadcn-совместимые локальные
примитивы. Он экспортируется в статику на этапе production build, которую
раздаёт FastAPI. Поэтому на VPS нет Node.js runtime. FastAPI органично
использует существующую Python-логику и её persistent volume.

Production image собирается GitHub Actions и передаётся на VPS через `docker
load`: серверу не нужно скачивать Docker Hub (его доступ уже был нестабилен) и
не нужен отдельный registry.

## Managed configuration and migration

`data/admin/settings.json` — атомарно записываемый versioned JSON с правами
`0600`. В нём только редактируемые значения: Telegram folder и public
additional channels, режим мониторинга/history/notifications, include/exclude
keywords, TTL/queue, модель/temperature/retries Mistral, titles Sheets и
timezone. Никогда не хранятся API keys, Google credentials, session или URL
таблицы с credential query.

Runtime объединяет `.env` с managed configuration: секреты и пути всегда
берутся из `.env`, управляемые поля переопределяются JSON. `TARGET_CHANNELS`
остаётся источником для синхронизации папки Telegram. UI добавляет только
public usernames, а скрывает numeric channel IDs; отключение существующего
источника выполняется opaque server token.

Сохранение увеличивает `revision` и пишет redacted audit event. Подтверждённый
restart кладёт команду в control-file; bot заканчивает очередь, disconnects,
Docker `unless-stopped` поднимает его с новым revision. Telethon filters не
изменяются на лету намеренно.

## Наблюдаемость, prompt и источники

В `data/admin/` добавлены служебные файлы с правами каталога данных:
`metrics.jsonl`, `app-logs.jsonl` и `errors.json`. Это не экспорт вакансий: в
метриках хранится только время, тип события и компонент. Дашборд считает
календарный день в `OUTPUT_TIMEZONE`: обработанные посты, успешные записи,
пропуски (дубликат/нерелевантное/невалидное) и ошибки. Счётчики переживают
перезапуск, но начинают накапливаться после установки версии с наблюдаемостью.

Лог handler записывает только INFO/WARNING/ERROR и очищает до записи значения,
похожие на API key, token, password, session cookie, Bearer header, query
credentials и числовые Telegram ID. API возвращает не более 100 строк за
запрос и максимум последние 3000 записей для фильтрации. ERROR создаёт или
обновляет безопасную карточку attention; повтор объединяется, а решённой
ошибка становится лишь после явного подтверждённого действия администратора.

Редактор LLM управляет только `vacancy_instructions` в managed configuration.
Шаблон разделён на редактируемые инструкции и неизменяемую strict JSON schema
в Python-коде; Pydantic validation не доступна браузеру. Backend отклоняет
инструкции с credential-shaped содержимым. По умолчанию и до первого
сохранения используется нынешний встроенный prompt. Изменение prompt или
источника применяется после подтверждённого restart.

UI-добавленные public Telegram usernames сохраняются как `managed_sources` с
датой и флагом enabled. `TARGET_CHANNELS`, синхронизированная папка и прежние
`additional_channels` продолжают работать и имеют обратную совместимость;
выключение доступно для всех, удаление — только для UI-managed источника.
Удаление меняет только future subscription и не трогает историю вакансий,
метрики или логи.

## Authentication and secrets

`ADMIN_PASSWORD` и `ADMIN_SESSION_SECRET` — production env variables. Логин
использует constant-time comparison, короткую HMAC-signed HttpOnly
`Secure`/`SameSite=Strict` cookie и отдельную CSRF cookie. Изменяющие запросы
проверяют `X-CSRF-Token` against session and cookie. API и audit log redacts
numeric Telegram IDs, query credentials и secret-shaped values.

Смена ключей Mistral/Google специально не доступна из браузера: это увеличило
бы поверхность атаки. UI показывает лишь «настроено»/«не настроено»; rotation
происходит в secret store и завершается контролируемым restart.

## Operations, rollback and network

Bot записывает heartbeat (mode, queue, counters, revision) и redacted history
operations. Dashboard читает эти файлы без внешних API. Подтверждаемыми
действиями являются restart, pause/resume, folder sync и history; history
заменяет live process, исключая одновременно открытый session.

Deploy сохраняет backup managed configuration, загружает новый image, применяет
Compose, проверяет `/healthz` и при ошибке возвращает previous image/config.

На VPS свободно около 226 MB RAM, а 443 занят VPN-службой и DNS name не задан.
Стандартный binding панели — `127.0.0.1:8080:8080`, доступ через `ssh -L
8080:127.0.0.1:8080 deploy@server`. Это безопаснее, чем открывать HTTP в
Internet. Конфиг Nginx для отдельного domain/TLS будет готов, но включается
только после подтверждения владельцем маршрута 443 и DNS.

## UI concept and wireframe

Светлая/тёмная, responsive Russian UI с semantic labels, focus rings,
keyboard navigation, contrast и inline errors.

```text
┌──────────────┬─────────────────────────────────────────────┐
│ Go Radar     │ Дашборд                  [Тема] [Выйти]     │
│ ● Бот активен├─────────────────────────────────────────────┤
│ Дашборд      │ [Активен] [Источники] [revision]            │
│ Telegram     │ Последняя синхронизация · Ошибки · Очередь   │
│ Фильтрация   │ [Синхронизировать] [История] [Перезапуск]   │
│ Mistral/LLM  ├─────────────────────────────────────────────┤
│ Sheets       │ Последние безопасные операции                │
│ Расписание   │ 12:41 Конфигурация сохранена                │
│ Логи         │ 12:40 Bot heartbeat                          │
└──────────────┴─────────────────────────────────────────────┘
```

## Риски и ограничения

- Telethon session stateful: UI не обходит single-process lock.
- Runtime metrics process-local, audit trail не analytics warehouse.
- Public HTTPS требует domain и согласованного маршрута 443.
- API health остаётся available, но login безопасно disabled, пока отсутствуют
  `ADMIN_PASSWORD` и `ADMIN_SESSION_SECRET`.
