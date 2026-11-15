# Dev Job Radar

Dev Job Radar collects job posts from Telegram, analyzes them with Mistral, and helps candidates find relevant vacancies. A Telegram bot and Mini App provide a personal feed; an admin panel manages sources and operations. Confirmed Go vacancies can also be exported to Google Sheets.

## What it does

- Monitors configured Telegram channels and can process historical messages.
- Classifies vacancies across a catalog of roles, keeps the original text, and deduplicates posts in a shared registry.
- Matches candidates by role, skills, seniority, work format, region, and the language of the vacancy. Unspecified filter values do not exclude a vacancy.
- Offers a personal feed, saved vacancies, and immediate or hourly Telegram delivery when enabled. The Mini App lets users manage profiles and read the source text.
- Provides an admin panel, optional Premium and Threads search, and full and concise Google Sheets exports for confirmed Go vacancies.

The project uses Python 3.12, Telethon, Mistral, SQLite, Google Sheets, and a Next.js frontend.

## Requirements

- Python 3.12, `make`, and Node.js 22 for frontend development.
- Telegram API credentials from [my.telegram.org](https://my.telegram.org/), and a Telegram account for the userbot.
- A [Mistral API key](https://console.mistral.ai/).
- A Google service account with the Sheets API enabled and editor access to the target spreadsheet.
- Docker Engine and Compose for container deployment. A separate Telegram Bot API token is needed for Candidate Bot.

## Quick start

```bash
git clone https://github.com/mmmakskl/dev-job-radar.git
cd dev-job-radar
make init
```

`make init` creates a virtual environment, installs runtime and development dependencies, creates `.env` if missing, and prepares `data/`. Edit `.env` and set at least `API_ID`, `API_HASH`, `MISTRAL_API_KEY`, and `GOOGLE_SHEET_URL`. Place the Google service-account JSON at `credentials.json`, or set `GOOGLE_CREDENTIALS_PATH` to its location.

```bash
make auth       # authorize the Telegram userbot via QR code
make channels   # discover accessible Telegram channels
make run        # monitor configured sources
```

To configure sources locally, set `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, and `ADMIN_COOKIE_SECURE=false` in `.env`, then start the admin panel:

```bash
make web-check
ADMIN_STATIC_DIR="$PWD/web/out" PYTHONPATH=src venv/bin/python -m uvicorn tg_vacancy_bot.admin.api:app --reload
```

Open `http://127.0.0.1:8000`, sign in, and configure the Telegram sources. `TARGET_CHANNELS` is an optional one-time bootstrap for an empty settings database. Run `make history` to process past messages. Do not run authorization, history, and live monitoring concurrently against the same Telegram session.

In Premium search results, choosing “Добавить канал в источники” queues the source action immediately and shows its status on the vacancy. The channel becomes available to monitoring after the required restart.

## Candidate Bot and Mini App

1. Create a bot through Telegram, add it as an administrator to the configured channel, and set `CANDIDATE_BOT_ENABLED=true`, `CANDIDATE_BOT_TOKEN`, `CANDIDATE_BOT_CHANNEL`, and `CANDIDATE_BOT_ALLOWED_USER_IDS` in `.env`.
2. Set `CANDIDATE_PROFILE_DELIVERY_ENABLED=true` to enable automatic personal delivery. A profile must be active and use immediate or hourly delivery; manual mode only shows the feed. Review the backlog before enabling delivery on an existing database.
3. Run `make candidate-bot` alongside the live collector. Start the bot in Telegram, create and activate a profile, then use its feed or Mini App.
4. For the Mini App, configure the public HTTPS `CANDIDATE_MINIAPP_URL` and the admin/candidate web service. Browser login can also use a one-time code issued by the bot.

The Mini App and bot use the same profile and registry. In the Mini App, temporary feed filters narrow the active profile's matches without changing its settings or notifications; persistent filters are edited in the profile. Missing source text is shown as unavailable; the application does not invent a description.

## Configuration

[`.env.example`](.env.example) lists the available environment variables and their defaults. The most important groups are:

| Purpose | Variables |
| --- | --- |
| Telegram userbot | `API_ID`, `API_HASH`, `SESSION_NAME` |
| Analysis and export | `MISTRAL_API_KEY`, `MISTRAL_DAILY_LIMIT`, `GOOGLE_SHEET_URL`, `GOOGLE_CREDENTIALS_PATH` |
| Candidate Bot | `CANDIDATE_BOT_ENABLED`, `CANDIDATE_BOT_TOKEN`, `CANDIDATE_BOT_CHANNEL`, `CANDIDATE_BOT_ALLOWED_USER_IDS`, `CANDIDATE_PROFILE_DELIVERY_ENABLED` |
| Web access | `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `ADMIN_ALLOWED_HOSTS`, `ADMIN_COOKIE_SECURE`, `CANDIDATE_MINIAPP_URL` |
| Optional search | `CANDIDATE_CATALOG_SEARCH_ENABLED`, `PREMIUM_GLOBAL_SEARCH_ENABLED`, `THREADS_ENABLED` |

Runtime source settings and operational policies are stored in SQLite and managed through the admin panel. `.env`, credentials, Telegram sessions, and `data/` contain secrets or user data and must stay out of Git.

## Run with Docker

Prepare `.env`, `data/`, and `secrets/google-credentials.json`; the Compose configuration mounts the service-account file read-only. Authorize the Telegram session in the container before starting live monitoring. The `candidate` profile enables the personal bot.

```bash
docker compose build
docker compose run --rm bot python scripts/auth.py
docker compose --profile candidate up -d
docker compose --profile candidate ps
```

Compose exposes the admin service on `127.0.0.1:8080`; Caddy provides the HTTPS reverse proxy. Set the allowed hosts, public domain, and secure cookie settings for the deployment. See the [deployment guide](docs/deployment.md) before using this configuration on a server.

## Development and checks

```bash
make check           # Python syntax and offline tests
make lint            # Ruff
make format-check    # Black
make security        # audit runtime dependencies
make web-check       # frontend typecheck, unit tests, and build
npm --prefix web run test:e2e
```

`make ci` also builds the Docker image. Ordinary tests use mocks and do not require live API access. `make smoke` contacts Telegram and requires a valid session.
The browser tests use the exported frontend and Playwright Chromium; run `make web-build` and `npm --prefix web exec -- playwright install chromium` first if needed.

## Project layout

```text
src/tg_vacancy_bot/  Python application: pipeline, registry, integrations, and APIs
scripts/             entry points and operational commands
web/                 admin panel and Candidate Mini App
tests/               offline Python tests
docs/                architecture, operations, and historical documentation
deploy/              reverse proxy and systemd configuration
```

See the [documentation index](docs/README.md) for deployment, registry operations, and architectural decisions. GitHub Actions runs checks on changes; pushes to `master` run the configured production deployment after checks pass.
