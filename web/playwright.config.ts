import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const repositoryRoot = resolve(fileURLToPath(import.meta.url), '../..');

export default defineConfig({
  testDir: './tests/e2e',
  globalSetup: './tests/e2e/global-setup.ts',
  use: { baseURL: 'http://127.0.0.1:8093' },
  webServer: {
    command: `cd "${repositoryRoot}" && DATA_DIR=/tmp/dev-job-radar-e2e ADMIN_PASSWORD=e2e-password ADMIN_SESSION_SECRET=e2e-session-secret-at-least-24-bytes ADMIN_COOKIE_SECURE=false ADMIN_STATIC_DIR="${repositoryRoot}/web/out" PYTHONPATH=src venv/bin/python -m uvicorn tg_vacancy_bot.admin.api:app --host 127.0.0.1 --port 8093`,
    url: 'http://127.0.0.1:8093/healthz',
    reuseExistingServer: !process.env.CI,
  },
});
