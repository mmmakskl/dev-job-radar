import { expect, test } from '@playwright/test';

test('login, dashboard, logs, sources and LLM settings', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Пароль администратора').fill('e2e-password');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page.getByText('Безопасное управление сбором вакансий')).toBeVisible();
  await expect(page.getByText('Постов обработано сегодня')).toBeVisible();
  await expect(page.getByText('Тестовая безопасная ошибка LLM')).toBeVisible();
  await page.getByRole('button', { name: 'Детали' }).click();
  await expect(page.getByText('Очищенные технические детали.')).toBeVisible();
  await page.getByRole('button', { name: 'Обновить статус' }).click();

  await page.getByLabel('Ключевые слова через запятую').fill('');
  await page.getByRole('button', { name: 'Сохранить настройки' }).click();
  await expect(page.getByText('Укажите хотя бы одно ключевое слово')).toBeVisible();

  await page.getByRole('button', { name: /Источники/ }).click();
  await page.getByLabel('Новый источник').fill('@e2e_go_jobs');
  await page.getByRole('button', { name: 'Добавить источник' }).click();
  await expect(page.getByRole('heading', { name: '@e2e_go_jobs' })).toBeVisible();
  await page.getByRole('button', { name: 'Выключить' }).click();
  await expect(page.getByRole('button', { name: 'Включить' })).toBeVisible();

  await page.getByRole('button', { name: '← Дашборд' }).click();
  await page.getByRole('button', { name: 'Логи' }).click();
  await page.getByLabel('Уровень лога').selectOption('ERROR');
  await expect(page.getByRole('heading', { name: 'Логи' })).toBeVisible();
  await page.getByRole('button', { name: '← Дашборд' }).click();
  await page.getByRole('button', { name: 'LLM-инструкции' }).click();
  await page.getByLabel('Инструкции для LLM').fill('Определи только вакансии Go и исключай резюме или профили кандидатов.');
  await page.getByRole('button', { name: 'Сохранить инструкции' }).click();
  await page.getByRole('button', { name: 'Восстановить значение по умолчанию' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Отмена' }).click();
  await page.getByRole('button', { name: '← Дашборд' }).click();

  await page.getByRole('button', { name: 'Перезапуск' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Подтвердить' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
