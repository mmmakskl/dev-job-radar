#!/usr/bin/env bash
# Synchronise the Telegram folder through the Compose image, then update host .env.
set -Eeuo pipefail

readonly APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCK_FILE="/tmp/dev-job-radar-channel-sync.lock"

cd "${APP_DIR}"

if [[ ! -f .env ]]; then
    echo "ERROR: ${APP_DIR}/.env is missing" >&2
    exit 1
fi

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

echo "Stopping bot to release the Telegram session..."
docker compose stop --timeout 45 bot
bot_stopped=true

echo "Reading the configured Telegram folder..."
target_channels="$({
    docker compose run --rm --no-deps -T bot \
        python scripts/sync_channels.py \
        --report-only
} | tail -n 1)"

if [[ -z "${target_channels}" ]]; then
    echo "ERROR: synchronisation did not return TARGET_CHANNELS" >&2
    exit 1
fi

python3 - .env "${target_channels}" <<'PY'
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

env_path = Path(sys.argv[1])
target_value = sys.argv[2]
content = env_path.read_text(encoding='utf-8')
pattern = re.compile(r'^(?P<prefix>\s*(?:export\s+)?TARGET_CHANNELS\s*=)[^\r\n]*(?P<ending>\r?\n)?$')
lines = content.splitlines(keepends=True)
for index, line in enumerate(lines):
    match = pattern.match(line)
    if match:
        lines[index] = f"{match.group('prefix')}{target_value}{match.group('ending') or ''}"
        break
else:
    if content and not content.endswith(('\n', '\r')):
        content += '\n'
    lines = [content, f'TARGET_CHANNELS={target_value}\n']

updated = ''.join(lines)
if updated == content:
    print('TARGET_CHANNELS already up to date.')
    raise SystemExit(0)

descriptor, temporary_name = tempfile.mkstemp(dir=env_path.parent, prefix=f'.{env_path.name}.')
temporary_path = Path(temporary_name)
try:
    with os.fdopen(descriptor, 'w', encoding='utf-8') as temporary_file:
        temporary_file.write(updated)
    os.chmod(temporary_path, stat.S_IMODE(env_path.stat().st_mode))
    os.replace(temporary_path, env_path)
except Exception:
    temporary_path.unlink(missing_ok=True)
    raise
print('Updated TARGET_CHANNELS in .env.')
PY
