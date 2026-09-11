#!/usr/bin/env bash
# Synchronise Telegram folder metadata directly into the persistent SQLite volume.
set -Eeuo pipefail

readonly APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCK_FILE="/tmp/dev-job-radar-channel-sync.lock"
cd "${APP_DIR}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "Channel synchronisation is already running; skipping."
    exit 0
fi

bot_stopped=false
start_bot() {
    if [[ "${bot_stopped}" == true ]]; then
        docker compose up -d bot
    fi
}
trap start_bot EXIT

docker compose stop --timeout 45 bot
bot_stopped=true
docker compose run --rm --no-deps -T bot python scripts/sync_channels.py
