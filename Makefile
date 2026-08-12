PYTHON ?= python3
VENV ?= venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PIP_AUDIT := $(VENV)/bin/pip-audit
PYTHONPATH := src
DOCKER_COMPOSE ?= docker compose

.DEFAULT_GOAL := help

.PHONY: help init venv install env data auth auth-force run live history \
	discover channels sync-channels smoke compile test check lint format-check coverage security ci \
	doctor state-info clean-cache clean docker-build docker-build-check docker-up \
	docker-down docker-logs docker-status docker-check

help:
	@echo "Telegram Go Vacancy Bot"
	@echo ""
	@echo "Setup:"
	@echo "  make init          создать venv, установить зависимости, подготовить .env и data/"
	@echo "  make venv          создать virtualenv, если он отсутствует"
	@echo "  make install       обновить pip и установить runtime/dev зависимости"
	@echo "  make env           создать .env из .env.example без перезаписи существующего"
	@echo ""
	@echo "Run (используют внешние API):"
	@echo "  make auth          авторизация Telegram без удаления существующей session"
	@echo "  make auth-force    удалить session через --force-relogin и авторизоваться заново"
	@echo "  make run           запустить live monitoring"
	@echo "  make live          alias для make run"
	@echo "  make history       обработать историю Telegram-каналов"
	@echo "  make discover      найти каналы среди текущих Telegram dialogs"
	@echo "  make channels      alias для make discover"
	@echo "  make sync-channels добавить чаты из Telegram-папки в TARGET_CHANNELS"
	@echo "  make smoke         live smoke test; требует Telegram session/API"
	@echo ""
	@echo "Validation and diagnostics (без API-запросов):"
	@echo "  make compile       проверить синтаксис src и scripts"
	@echo "  make test          запустить изолированные pytest-тесты без live"
	@echo "  make check         выполнить compile и test"
	@echo "  make lint          проверить код через ruff"
	@echo "  make format-check  проверить форматирование через black"
	@echo "  make coverage      запустить тесты с pytest-cov"
	@echo "  make security      проверить runtime зависимости через pip-audit"
	@echo "  make ci            выполнить все обязательные CI-проверки"
	@echo "  make doctor        проверить локальное окружение"
	@echo "  make state-info    показать информацию о data/state.jsonl"
	@echo "  make clean-cache   удалить только безопасные Python/tool caches"
	@echo ""
	@echo "Docker Compose:"
	@echo "  make docker-build  собрать production-образ"
	@echo "  make docker-up     собрать и запустить bot в фоне"
	@echo "  make docker-down   остановить Compose без удаления persistent data"
	@echo "  make docker-logs   следить за логами bot"
	@echo "  make docker-status показать состояние Compose"
	@echo "  make docker-check  проверить compose.yaml без запуска"

venv:
	@test -x "$(PY)" || $(PYTHON) -m venv "$(VENV)"

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

env:
	@if [ -f .env ]; then \
		echo "[ok] .env already exists"; \
	else \
		cp .env.example .env; \
		echo "[ok] created .env from .env.example"; \
	fi

data:
	mkdir -p data

init: install env data

auth: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/auth.py

auth-force: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/auth.py --force-relogin

run: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/run_live.py

live: run

history: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/parse_history.py

discover: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/discover_channels.py

channels: discover

sync-channels: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/sync_channels.py

smoke: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/test_userbot.py

compile: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) -m compileall -q src scripts

test: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q -m "not live"

check: compile test

lint: venv
	$(PY) -m ruff check src scripts tests

format-check: venv
	$(PY) -m black --check src scripts tests

coverage: venv
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest --cov=tg_vacancy_bot --cov-report=term-missing -m "not live"

security: venv
	$(PIP_AUDIT) -r requirements.txt

docker-build-check:
	docker build --tag tg-vacancy-bot:ci .

ci: compile test lint format-check security docker-build-check

doctor:
	@for item in .env .env.example requirements.txt credentials.json; do \
		if [ -f "$$item" ]; then echo "[ok] $$item"; else echo "[missing] $$item"; fi; \
	done
	@if [ -d "$(VENV)" ]; then echo "[ok] $(VENV)/"; else echo "[missing] $(VENV)/"; fi
	@if [ -d src/tg_vacancy_bot ]; then echo "[ok] src/tg_vacancy_bot"; else echo "[missing] src/tg_vacancy_bot"; fi
	@for item in scripts/run_live.py scripts/parse_history.py scripts/auth.py; do \
		if [ -f "$$item" ]; then echo "[ok] $$item"; else echo "[missing] $$item"; fi; \
	done
	@if find . -maxdepth 1 -type f -name '*.session' -print -quit | grep -q .; then \
		echo "[ok] *.session"; \
	else \
		echo "[warn] *.session not found"; \
	fi
	@if [ -f data/state.jsonl ]; then \
		echo "[ok] data/state.jsonl"; \
	else \
		echo "[warn] data/state.jsonl not created yet"; \
	fi

state-info:
	@if [ -f data/state.jsonl ]; then \
		lines=$$(wc -l < data/state.jsonl | tr -d ' '); \
		bytes=$$(wc -c < data/state.jsonl | tr -d ' '); \
		echo "data/state.jsonl: $$lines lines, $$bytes bytes"; \
		echo "Last 3 lines:"; \
		tail -n 3 data/state.jsonl; \
	else \
		echo "data/state.jsonl not found yet. It will be created after first successful export."; \
	fi

clean-cache:
	@find src scripts tests -type d -name '__pycache__' -prune -exec rm -rf {} +
	@find src scripts tests -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "Safe Python/tool caches removed"

clean: clean-cache

docker-build:
	$(DOCKER_COMPOSE) build

docker-up:
	$(DOCKER_COMPOSE) up -d --build --remove-orphans

docker-down:
	$(DOCKER_COMPOSE) down

docker-logs:
	$(DOCKER_COMPOSE) logs --tail=200 -f bot

docker-status:
	$(DOCKER_COMPOSE) ps

docker-check:
	$(DOCKER_COMPOSE) config --quiet
