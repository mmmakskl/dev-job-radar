import { mkdir, rm, writeFile } from 'node:fs/promises';

const dataDir = '/tmp/dev-job-radar-e2e/admin';

export default async function globalSetup(): Promise<void> {
  await rm('/tmp/dev-job-radar-e2e', { recursive: true, force: true });
  await mkdir(dataDir, { recursive: true });
  const now = new Date().toISOString();
  await writeFile(
    `${dataDir}/errors.json`,
    JSON.stringify([{
      id: 'e2e-safe-error', fingerprint: 'e2e-safe-error', component: 'llm',
      summary: 'Тестовая безопасная ошибка LLM', details: 'Очищенные технические детали.',
      status: 'new', count: 1, first_seen_at: now, last_seen_at: now,
    }]),
  );
  await writeFile(
    `${dataDir}/settings.json`,
    JSON.stringify({
      schema_version: 3,
      revision: 1,
      updated_at: now,
      telegram: {
        managed_sources: [{
          identifier: 'layout_regression_source_with_long_name_123456789',
          enabled: true,
          added_at: now,
          verification_status: 'verified',
        }],
      },
    }),
  );
}
