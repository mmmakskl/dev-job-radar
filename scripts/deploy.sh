#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="/home/deploy/apps/dev-job-radar"
readonly IMAGE_NAME="dev-job-radar-bot:latest"
readonly PREVIOUS_IMAGE="dev-job-radar-bot:previous"
readonly SKIP_IMAGE_BUILD="${SKIP_IMAGE_BUILD:-0}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "ERROR: expected Git checkout at ${APP_DIR}" >&2
    exit 1
fi

cd "${APP_DIR}"
settings_backup=""
if [[ -f data/admin/settings.json ]]; then
    settings_backup="$(mktemp)"
    cp data/admin/settings.json "${settings_backup}"
fi

rollback() {
    local exit_code=$?
    if [[ ${exit_code} -eq 0 ]]; then
        return
    fi
    echo "ERROR: deploy failed; restoring the previous image and managed settings." >&2
    if [[ -n "${settings_backup}" && -f "${settings_backup}" ]]; then
        mkdir -p data/admin
        cp "${settings_backup}" data/admin/settings.json
    fi
    if docker image inspect "${PREVIOUS_IMAGE}" >/dev/null 2>&1; then
        docker tag "${PREVIOUS_IMAGE}" "${IMAGE_NAME}"
        docker compose up -d --remove-orphans || true
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

if [[ ! -f secrets/google-credentials.json ]]; then
    echo "ERROR: secrets/google-credentials.json is missing" >&2
    exit 1
fi

mkdir -p data

git pull --ff-only origin master

echo "Validating Docker Compose configuration..."
docker compose config --quiet

if [[ "${SKIP_IMAGE_BUILD}" != "1" ]]; then
    echo "Building production image..."
    docker compose build --pull
else
    echo "Using production image loaded by CI."
    docker image inspect "${IMAGE_NAME}" >/dev/null
fi

echo "Starting bot service..."
docker compose up -d --remove-orphans

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

echo "Deployment status:"
docker compose ps bot
docker compose ps admin

echo "Removing dangling Docker images only..."
docker image prune --force

echo "Deployment completed."
trap - EXIT
[[ -z "${settings_backup}" ]] || rm -f "${settings_backup}"
