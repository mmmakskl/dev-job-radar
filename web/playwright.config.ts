import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  use: { baseURL: 'http://127.0.0.1:8081' },
  webServer: {
    command: 'cd .. && DATA_DIR=/tmp/dev-job-radar-e2e ADMIN_PASSWORD=e2e-password ADMIN_SESSION_SECRET=e2e-session-secret ADMIN_COOKIE_SECURE=false ADMIN_STATIC_DIR=$PWD/web/out PYTHONPATH=src venv/bin/python -m uvicorn tg_vacancy_bot.admin.api:app --host 127.0.0.1 --port 8081',
    url: 'http://127.0.0.1:8081/healthz',
    reuseExistingServer: !process.env.CI,
  },
});
