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

test('private screens use the full content width at every supported viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await page.getByLabel('Пароль администратора').fill('e2e-password');
  await page.getByRole('button', { name: 'Войти' }).click();

  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 1024, height: 900 },
    { width: 768, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);

    for (const route of ['/sources', '/settings', '/prompt']) {
      await page.goto(route);
      await expect(page.locator('.page-content > .screen')).toBeVisible();
      const dimensions = await page.locator('.page-content').evaluate((content) => {
        const screen = content.querySelector<HTMLElement>(':scope > .screen');
        if (!screen) throw new Error('Private screen is missing');
        return {
          content: content.getBoundingClientRect().width,
          screen: screen.getBoundingClientRect().width,
        };
      });
      expect(dimensions.screen).toBeGreaterThanOrEqual(dimensions.content - 1);
    }

    await page.goto('/sources');
    await expect(page.getByRole('heading', { name: '@layout_regression_source_with_long_name_123456789' })).toBeVisible();
    const dimensions = await page.locator('.source-grid').evaluate((grid) => {
      const card = grid.querySelector<HTMLElement>('.source-card');
      if (!card) throw new Error('Regression source card is missing');
      return {
        grid: grid.getBoundingClientRect().width,
        card: card.getBoundingClientRect().width,
      };
    });
    expect(dimensions.card).toBeGreaterThanOrEqual(Math.min(280, dimensions.grid - 1));
    if (dimensions.grid >= 600) {
      // A twelve-column parent used to compress every card to about 1/12 width.
      expect(dimensions.card).toBeGreaterThan(dimensions.grid / 4);
    } else {
      expect(dimensions.card).toBeGreaterThanOrEqual(dimensions.grid - 1);
    }
  }
});
