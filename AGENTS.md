# Repository Guidelines

## Project Structure & Module Organization

This Python application collects Go vacancies from Telegram. Application modules live under `src/tg_vacancy_bot/`: configuration, structured vacancy models and normalization, the shared pipeline, append-only JSONL deduplication state, Telegram links, strict Mistral schemas/prompts, and two-sheet Google Sheets storage. Executable entry points live under `scripts/`: `run_live.py`, `parse_history.py`, `auth.py`, and `discover_channels.py`. `scripts/test_userbot.py` is an interactive connectivity smoke test, not an automated suite. Generated JSON, spreadsheets, session files, caches, and `venv/` are not source.

## Setup, Run, and Validation Commands

Create and activate an isolated environment, then install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure Telegram, Mistral, and Google Sheets. Use `make auth` for QR authorization, `make run` for live monitoring, `make history` for the previous week's messages, or `make channels` for discovery. Direct script execution requires `PYTHONPATH=src`. Use `PYTHONPATH=src python -m compileall -q src scripts` as a syntax check. `scripts/test_userbot.py` contacts Telegram and requires a valid session; do not use it as an offline CI test.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation. Use `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for configuration constants, and descriptive module names. Keep network workflows asynchronous and move blocking SDK calls into `asyncio.to_thread`, as in `spreadsheet.py`. Add type hints to public helpers and concise docstrings where behavior is not obvious. Ruff and Black are configured through `make lint` and `make format-check`; keep imports grouped and changes compatible with those checks.

## Testing Guidelines

Automated tests use `pytest` and live under `tests/`, named `test_<module>.py`. Run them with `make test`. New logic should include isolated tests and mock Telegram, LLM, and Google APIs. Never make live API access a requirement for ordinary tests. Before submitting, run `make check`, `make test`, and any relevant smoke script in a non-production account.

## Documentation Maintenance

Update `README.md` as part of every substantial project change so that the documentation does not fall behind the actual state of the repository. Keep setup instructions, configuration variables, Make targets and run commands, architecture descriptions, and user-visible behavior consistent with the implementation. Treat the documentation update as part of completing the change, not as optional follow-up work. Small internal changes that do not affect documented behavior do not require a README update.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so use short, imperative commit subjects such as `Add retry handling for Mistral`. Keep commits focused. Pull requests should explain the behavior change, list validation performed, note configuration changes, and link an issue when applicable. Include redacted logs or screenshots only when they clarify runtime behavior.

## Security & Configuration

Never commit `.env`, `credentials.json`, `*.session`, API keys, chat identifiers, or exported vacancy data. Use placeholders in examples, redact logs, and rotate any credential that is accidentally exposed.
