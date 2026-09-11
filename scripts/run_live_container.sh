#!/bin/sh
set -eu
python -c 'from datetime import datetime, timezone; open("/tmp/bot-started-at", "w", encoding="utf-8").write(datetime.now(timezone.utc).isoformat())'
exec python scripts/run_live.py
