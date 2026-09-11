# Архитектура панели управления

## Control plane

Панель — статический Next.js frontend поверх FastAPI. Единый persistent
control plane находится в `data/admin/admin.sqlite3`. SQLite работает в WAL,
с foreign keys, busy timeout, constraints, indexes и транзакционными
миграциями.

База хранит revisioned non-secret settings, Telegram sources, их origins и
durable admin actions. Источник имеет внутренний UUID, стабильный marked
Telegram ID, optional public username, title, тип чата, enabled/verification
state и timestamps. API никогда не возвращает numeric Telegram IDs.

При первом открытии база идемпотентно импортирует `TARGET_CHANNELS` и старый
`data/admin/settings.json`. Эти legacy inputs остаются нетронутыми для rollback,
но после миграции не читаются как live configuration.

## Telegram synchronization

Live bot владеет Telethon session. FastAPI только ставит действия в SQLite;
bot атомарно claims их, выполняет Telegram calls, записывает terminal result и
перезапускает listener с новым набором подписок. Повторный запрос возвращает
существующее действие; конфликтующая session-mutating операция получает `409`.

Folder sync сначала полностью получает и проверяет папку, затем одной
транзакцией upsert-ит metadata и reconciles только origin `folder`. Удаление из
папки не удаляет admin/discovery origins. Ошибка fetch или write сохраняет
last-known-good active set. Discovery использует тот же upsert path и сразу
включает найденные источники.

На startup bot делает bounded folder sync, а при сбое подписывается на
последний сохранённый набор. Scheduled systemd sync кратко освобождает session,
пишет SQLite volume напрямую и не редактирует `.env`.

## API and frontend

Settings writes обязаны передавать текущую revision; stale write получает
`409`. Source responses включают opaque token, title, chat type, origins и
`last_seen_at`. Queued actions возвращают `202` и status URL доступен
аутентифицированному администратору. Dashboard содержит active action, поэтому
frontend продолжает polling после reload/navigation, показывает counts/error и
refetch-ит dashboard/settings/sources после terminal state.

Cookies secure by default and SameSite Strict. Login ограничен пятью ошибками
за 15 минут на client address. Trusted hosts задаются `ADMIN_ALLOWED_HOSTS`, а
Caddy добавляет HSTS. Секреты, session, Google credentials, private Telegram
IDs и raw post text не выдаются через API или operational telemetry.

## Runtime data integrity

JSONL telemetry writes используют один cross-process locked write на record.
Pipeline claims сообщения до внешних calls, а Sheets check/append сериализован
между threads/processes. Vacancy-controlled cells отправляются с
`value_input_option=RAW`, чтобы Google Sheets не интерпретировал формулы.
History imports never publish candidate cards.

Compose сначала запускает non-destructive `data-init`, исправляющий ownership
persistent volume без удаления файлов. Bot healthcheck принимает только
heartbeat, созданный текущим container instance; admin healthcheck проверяет
доступность SQLite и возвращает instance ID. Optional candidate-worker также
имеет отдельный heartbeat, привязанный к текущему container start.
