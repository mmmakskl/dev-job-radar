import { expect, test } from '@playwright/test';

test('admin works through direct URLs and confirms consequential actions', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Пароль администратора').fill('e2e-password');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: 'Состояние обработки' })).toBeVisible();
  await expect(page.getByText('Постов обработано сегодня')).toBeVisible();
  await expect(page.getByText('Тестовая безопасная ошибка LLM')).toBeVisible();
  await page.getByRole('button', { name: 'Безопасные детали' }).click();
  await expect(page.getByText('Очищенные технические детали.')).toBeVisible();

  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'Настройки обработки' })).toBeVisible();
  await page.getByLabel('Ключевые слова через запятую').fill('');
  await page.getByRole('button', { name: 'Сохранить изменения' }).click();
  await expect(page.getByText('Укажите хотя бы одно ключевое слово')).toBeVisible();
  await page.getByLabel('Ключевые слова через запятую').fill('go, golang, backend');
  await expect(page.getByText('Есть несохранённые изменения')).toBeVisible();
  await page.getByRole('button', { name: 'Сохранить изменения' }).click();
  await expect(page.getByRole('dialog')).toContainText('live-процесс продолжит работать');
  await page.getByRole('button', { name: 'Отмена' }).click();

  await page.goto('/sources');
  await expect(page.getByRole('heading', { name: /Источники/ })).toBeVisible();
  await page.getByLabel('Новый источник').fill('https://t.me/+private');
  await page.getByRole('button', { name: 'Проверить и добавить' }).click();
  await expect(page.getByText('Введите публичный @username или ссылку t.me/username. Invite-ссылки не поддерживаются.')).toBeVisible();

  await page.goto('/logs?level=ERROR');
  await page.getByLabel('Уровень лога').selectOption('ERROR');
  await expect(page.getByRole('heading', { name: 'Логи' })).toBeVisible();
  await expect(page).toHaveURL(/\/logs\?level=ERROR/);

  await page.goto('/errors');
  await expect(page.getByRole('heading', { name: 'Активные ошибки' })).toBeVisible();
  await expect(page.getByText('Следующее действие:')).toBeVisible();

  await page.goto('/');
  await page.getByRole('button', { name: 'Перезапустить бота' }).click();
  await expect(page.getByRole('dialog')).toContainText('применит сохранённые настройки');
  await page.getByRole('button', { name: 'Отмена' }).click();
});
