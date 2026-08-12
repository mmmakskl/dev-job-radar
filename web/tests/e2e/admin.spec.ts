import { expect, test } from '@playwright/test';

test('login, dashboard, invalid form and confirmed action', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Пароль администратора').fill('e2e-password');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page.getByText('Безопасное управление сбором вакансий')).toBeVisible();

  await page.getByLabel('Ключевые слова через запятую').fill('');
  await page.getByRole('button', { name: 'Сохранить настройки' }).click();
  await expect(page.getByText('Укажите хотя бы одно ключевое слово')).toBeVisible();

  await page.getByRole('button', { name: 'Перезапуск' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Подтвердить' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
