import json
from datetime import datetime, timezone

import pytest

from tg_vacancy_bot.deploy_health import bot_is_ready


@pytest.mark.parametrize(
    ('status', 'updated', 'expected'),
    [
        ('running', '2026-09-10T12:00:30+00:00', True),
        ('running', '2026-09-10T11:59:59+00:00', False),
        ('running', '2026-09-10T11:58:00+00:00', False),
        ('running', '2026-09-10T12:02:00+00:00', False),
        ('paused', '2026-09-10T12:00:30+00:00', False),
        ('stopped', '2026-09-10T12:00:30+00:00', False),
    ],
)
def test_readiness_rejects_old_or_inactive_heartbeat(
    tmp_path, status, updated, expected
):
    path = tmp_path / 'heartbeat.json'
    path.write_text(json.dumps({'status': status, 'updated_at': updated}))
    assert (
        bot_is_ready(
            path,
            '2026-09-10T12:00:00.123456789Z',
            now=datetime(2026, 9, 10, 12, 1, tzinfo=timezone.utc),
        )
        is expected
    )


@pytest.mark.parametrize('contents', [None, '{', '[]', '{}', '{"updated_at": null}'])
def test_readiness_handles_missing_or_invalid_state(tmp_path, contents):
    path = tmp_path / 'heartbeat.json'
    if contents is not None:
        path.write_text(contents)
    assert not bot_is_ready(path, '2026-09-10T12:00:00Z')
