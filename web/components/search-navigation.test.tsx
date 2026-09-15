import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Page from '../app/page';
import { api, premiumApi, searchApi } from '../lib/api';
vi.mock('../lib/api', () => ({ api: { status: vi.fn(), settings: vi.fn(), dashboard: vi.fn(), metrics: vi.fn(), errors: vi.fn() }, premiumApi: { capabilities: vi.fn() }, searchApi: { capabilities: vi.fn(), runs: vi.fn() }, waitForAction: vi.fn() }));
beforeEach(() => {
  Object.defineProperty(window, 'localStorage', { configurable: true, value: { getItem: vi.fn(), setItem: vi.fn() } });
  vi.resetAllMocks();
  vi.mocked(api.status).mockResolvedValue({ configured: true, authenticated: true });
  vi.mocked(api.settings).mockResolvedValue({ revision: 0 } as never);
  vi.mocked(api.dashboard).mockResolvedValue({ settings_revision: 0, channel_count: 0, operations: [] });
  vi.mocked(api.errors).mockResolvedValue([]);
  vi.mocked(premiumApi.capabilities).mockResolvedValue(null);
  vi.mocked(searchApi.capabilities).mockResolvedValue({ publisher_configured: false, sources: [{ key: 'telegram', label: 'Telegram · опубликованные', enabled: true, reason: null }] });
  vi.mocked(searchApi.runs).mockResolvedValue({ items: [] });
  window.history.replaceState({}, '', '/');
});
afterEach(cleanup);
it('opens unified search when Premium and Threads are disabled', async () => {
  render(<Page/>);
  const nav = await screen.findByRole('navigation');
  expect(within(nav).queryByRole('button', { name: 'Premium поиск' })).not.toBeInTheDocument();
  fireEvent.click(within(nav).getByRole('button', { name: 'Общий поиск' }));
  expect(await screen.findByRole('heading', { name: 'Общий поиск вакансий' })).toBeInTheDocument();
  expect(await screen.findByRole('checkbox', { name: 'Telegram · опубликованные' })).toBeEnabled();
});
