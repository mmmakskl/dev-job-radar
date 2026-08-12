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
}
