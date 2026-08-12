#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="/home/deploy/apps/dev-job-radar"

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "ERROR: expected Git checkout at ${APP_DIR}" >&2
    exit 1
fi

cd "${APP_DIR}"

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

echo "Building production image..."
if ! docker compose build --pull; then
    image_name="$(docker compose config --images | sed -n '1p')"
    if [[ -z "${image_name}" ]]; then
        echo "ERROR: could not resolve the bot image name for cached build" >&2
        exit 1
    fi
    if ! docker image inspect "${image_name}" >/dev/null 2>&1; then
        echo "ERROR: Docker Hub build failed and no cached bot image exists" >&2
        exit 1
    fi

    echo "Docker Hub is unavailable; rebuilding from cached image ${image_name}..."
    docker build \
        --pull=false \
        --build-arg "BASE_IMAGE=${image_name}" \
        --file Dockerfile.cached-base \
        --tag "${image_name}" \
        .
fi

echo "Starting bot service..."
docker compose up -d --remove-orphans
sleep 5

container_id="$(docker compose ps -q bot)"
if [[ -z "${container_id}" ]]; then
    echo "ERROR: bot container was not created" >&2
    exit 1
fi

if [[ "$(docker inspect --format '{{.State.Running}}' "${container_id}")" != "true" ]]; then
    echo "ERROR: bot container is not running" >&2
    docker compose ps
    exit 1
fi

echo "Deployment status:"
docker compose ps bot

echo "Removing dangling Docker images only..."
docker image prune --force

echo "Deployment completed."
