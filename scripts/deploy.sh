#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="/home/deploy/apps/dev-job-radar"
readonly IMAGE_NAME="dev-job-radar-bot:latest"
readonly PREVIOUS_IMAGE="dev-job-radar-bot:previous"
readonly DEPLOY_REVISION="${DEPLOY_REVISION:-}"
readonly SKIP_IMAGE_BUILD="${SKIP_IMAGE_BUILD:-0}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "ERROR: expected Git checkout at ${APP_DIR}" >&2
    exit 1
fi

cd "${APP_DIR}"
build_context=""
services_changed=0

rollback() {
    local exit_code=$?
    if [[ ${exit_code} -eq 0 ]]; then
        return
    fi
    echo "ERROR: deploy failed; restoring the previous image if services were changed." >&2
    if [[ -n "${build_context}" ]]; then
        rm -rf "${build_context}"
    fi
    if [[ "${services_changed}" == "1" ]] \
        && docker image inspect "${PREVIOUS_IMAGE}" >/dev/null 2>&1; then
        docker tag "${PREVIOUS_IMAGE}" "${IMAGE_NAME}"
        if ! docker compose --profile candidate up -d --no-build --remove-orphans; then
            echo "ERROR: rollback could not restart the previous image." >&2
            docker compose ps -a || true
            docker compose logs --tail=100 bot admin candidate-bot || true
        fi
    fi
    exit "${exit_code}"
}
trap rollback EXIT

echo "Fetching origin/master..."
git fetch origin master

if [[ "$(git branch --show-current)" != "master" ]]; then
    echo "ERROR: deployment checkout must stay on master" >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    echo "ERROR: ${APP_DIR}/.env is missing" >&2
    exit 1
fi

if [[ -n "${DEPLOY_REVISION}" ]]; then
    [[ "${DEPLOY_REVISION}" =~ ^[0-9a-f]{40}$ ]] || {
        echo "ERROR: DEPLOY_REVISION must be a full commit SHA" >&2
        exit 1
    }
    git merge-base --is-ancestor "${DEPLOY_REVISION}" origin/master
    git merge --ff-only "${DEPLOY_REVISION}"
    [[ "$(git rev-parse HEAD)" == "${DEPLOY_REVISION}" ]] || {
        echo "ERROR: checkout does not match requested image revision" >&2
        exit 1
    }
else
    git pull --ff-only origin master
fi

mkdir -p data
if [[ ! -d data ]]; then
    echo "ERROR: ${APP_DIR}/data is not a directory" >&2
    exit 1
fi

echo "Validating Docker Compose configuration..."
docker compose config --quiet

if [[ "${SKIP_IMAGE_BUILD}" != "1" ]]; then
    echo "Building production image..."
    build_context="$(mktemp -d)"
    mkdir -p "${build_context}/web"
    cp Dockerfile requirements.txt "${build_context}/"
    cp -R src scripts "${build_context}/"
    cp -R web/app web/components web/lib "${build_context}/web/"
    cp web/package.json web/package-lock.json web/next-env.d.ts \
        web/next.config.ts web/postcss.config.mjs web/tailwind.config.ts \
        web/tsconfig.json "${build_context}/web/"
    docker build --pull --tag "${IMAGE_NAME}" "${build_context}"
    rm -rf "${build_context}"
    build_context=""
else
    echo "Using production image loaded by CI."
    docker image inspect "${IMAGE_NAME}" >/dev/null
    if [[ -n "${DEPLOY_REVISION}" ]]; then
        image_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${IMAGE_NAME}")"
        [[ "${image_revision}" == "${DEPLOY_REVISION}" ]] || {
            echo "ERROR: loaded image revision does not match checkout" >&2
            exit 1
        }
    fi
fi

# Read as the image UID; deploy cannot traverse secrets/ with mode 0700.
# This process does not open the Telegram session or call external APIs.
docker compose run --rm --no-deps bot python -c '
import os
from pathlib import Path
from tg_vacancy_bot import config
config.validate_required_settings()
assert Path(config.GOOGLE_CREDENTIALS_PATH).is_file(), "Google credentials missing"
with open(config.GOOGLE_CREDENTIALS_PATH, "rb") as credentials:
    assert credentials.read(1), "Google credentials empty"
assert os.getenv("ADMIN_PASSWORD"), "ADMIN_PASSWORD missing"
assert os.getenv("ADMIN_SESSION_SECRET"), "ADMIN_SESSION_SECRET missing"
session = Path(config.SESSION_NAME)
if session.suffix != ".session":
    session = Path(str(session) + ".session")
assert session.is_file(), "Telegram session missing; authorize before deploy"
'

echo "Starting bot service..."
services_changed=1
if ! docker compose --profile candidate up -d --no-build --remove-orphans; then
    echo "ERROR: Docker Compose could not start the services." >&2
    docker compose ps -a || true
    docker compose logs --tail=100 bot admin candidate-bot || true
    exit 1
fi

container_id=""
for _ in {1..15}; do
    container_id="$(docker compose ps -aq bot | sed -n '$p')"
    if [[ -n "${container_id}" ]]; then
        break
    fi
    sleep 1
done

if [[ -z "${container_id}" ]]; then
    echo "ERROR: bot container was not created within 15 seconds" >&2
    exit 1
fi

for _ in {1..30}; do
    if [[ "$(docker inspect --format '{{.State.Running}}' "${container_id}")" == "true" ]]; then
        break
    fi
    sleep 1
done

if [[ "$(docker inspect --format '{{.State.Running}}' "${container_id}")" != "true" ]]; then
    echo "ERROR: bot container is not running after 30 seconds" >&2
    docker compose ps -a
    docker compose logs --tail=100 bot
    exit 1
fi

candidate_id=""
for _ in {1..15}; do
    candidate_id="$(docker compose --profile candidate ps -aq candidate-bot | sed -n '$p')"
    if [[ -n "${candidate_id}" ]]; then
        break
    fi
    sleep 1
done
if [[ -z "${candidate_id}" ]]; then
    echo "ERROR: candidate-bot container was not created" >&2
    docker compose --profile candidate ps -a
    docker compose --profile candidate logs --tail=100 candidate-bot || true
    exit 1
fi

echo "Checking admin API health..."
for _ in {1..30}; do
    admin_id="$(docker compose ps -q admin)"
    if [[ -n "${admin_id}" ]] \
        && [[ "$(docker inspect --format '{{.State.Health.Status}}' "${admin_id}" 2>/dev/null || true)" == "healthy" ]]; then
        break
    fi
    sleep 1
done
if [[ -z "${admin_id:-}" ]] \
    || [[ "$(docker inspect --format '{{.State.Health.Status}}' "${admin_id}" 2>/dev/null || true)" != "healthy" ]]; then
    echo "ERROR: admin service did not become healthy" >&2
    docker compose ps -a
    docker compose logs --tail=100 admin
    exit 1
fi

echo "Checking live bot readiness after Telegram and Google Sheets startup..."
ready=0
for _ in {1..90}; do
    started_at="$(docker inspect --format '{{.State.StartedAt}}' "${container_id}")"
    if docker compose exec -T bot python -m tg_vacancy_bot.deploy_health "${started_at}"; then
        ready=1
        break
    fi
    sleep 2
done
if [[ "${ready}" != "1" ]]; then
    echo "ERROR: no fresh running heartbeat from bot" >&2
    exit 1
fi

echo "Checking candidate worker readiness..."
candidate_ready=0
for _ in {1..45}; do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "${candidate_id}" 2>/dev/null || true)" == "healthy" ]]; then
        candidate_ready=1
        break
    fi
    sleep 2
done
if [[ "${candidate_ready}" != "1" ]]; then
    echo "ERROR: candidate-bot did not become healthy" >&2
    docker compose --profile candidate logs --tail=100 candidate-bot || true
    exit 1
fi

docker compose exec -T admin python -c 'import json; from urllib.request import urlopen; assert json.load(urlopen("http://127.0.0.1:8080/api/v1/auth/status"))["configured"], "Admin authentication is not configured"'

echo "Deployment status:"
docker compose ps bot
docker compose ps admin
docker compose --profile candidate ps candidate-bot

echo "Removing dangling Docker images only..."
docker image prune --force

echo "Deployment completed."
trap - EXIT
