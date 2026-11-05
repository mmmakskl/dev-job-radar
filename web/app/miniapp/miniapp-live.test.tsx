import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import MiniappPage from './page';

const liveProfile = {
  profile_id: 'p1', name: 'Live Profile', direction_id: 'development',
  role_id: 'backend_developer', is_active: true, version: 1,
  preferences: { seniority: ['middle'], required_skills: ['go'], excluded_skills: [], delivery_mode: 'immediate' },
};
const liveVacancy = {
  vacancy_id: 'live-1', title: 'Live vacancy', company: 'Acme',
  summary: 'A real vacancy from the candidate registry.',
  post_link: 'https://t.me/jobs/1', published_at: '2026-11-05T10:00:00+00:00',
  grade: 'Middle', salary: '250 000 ₽', location: 'Россия', format: 'Удалённо',
  tags: ['Go'], saved: false,
};

const response = (value: unknown, status = 200) => Promise.resolve({
  ok: status >= 200 && status < 300, status, json: async () => value,
} as Response);

beforeEach(() => {
  Object.defineProperty(window, 'Telegram', {
    configurable: true,
    value: { WebApp: { initData: 'signed-init-data', ready: vi.fn(), expand: vi.fn() } },
  });
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: { getItem: vi.fn(), setItem: vi.fn() },
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as Window & { Telegram?: unknown }).Telegram; });

it('never shows sample vacancies outside Telegram', async () => {
  delete (window as Window & { Telegram?: unknown }).Telegram;
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);

  render(<MiniappPage/>);

  expect(await screen.findByText(/Откройте Mini App через бота в Telegram/)).toBeInTheDocument();
  expect(screen.queryByText('Go Backend Developer')).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

it('shows the real profile and registry vacancy when launched by Telegram', async () => {
  vi.stubGlobal('fetch', vi.fn((input: string) => {
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved')) return response({ items: [] });
    if (input.endsWith('/premium/templates')) return response({ items: [] });
    if (input.endsWith('/premium/runs')) return response({ items: [] });
    return response({}, 404);
  }));

  render(<MiniappPage/>);

  await waitFor(() => expect(screen.getByText('Live vacancy')).toBeInTheDocument());
  expect(screen.getByText('Live Profile')).toBeInTheDocument();
  expect(screen.queryByText('Go Backend Developer')).not.toBeInTheDocument();
});

it('saves a live vacancy through the candidate API and shows it in the archive', async () => {
  let saved = false;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved/live-1') && init?.method === 'PUT') { saved = true; return response({ saved: true }); }
    if (input.endsWith('/saved')) return response({ items: saved ? [{ ...liveVacancy, saved: true }] : [] });
    if (input.endsWith('/premium/templates')) return response({ items: [] });
    if (input.endsWith('/premium/runs')) return response({ items: [] });
    return response({}, 404);
  }));
  render(<MiniappPage/>);
  await screen.findByText('Live vacancy');

  fireEvent.click(screen.getByRole('button', { name: 'Сохранить вакансию' }));
  await waitFor(() => expect(saved).toBe(true));
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Навигация' })).getByRole('button', { name: /Сохранённые/ }));
  await waitFor(() => expect(screen.getByText('Live vacancy')).toBeInTheDocument());
});

it('launches Premium search with a backend template for the active profile', async () => {
  let started = false;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved')) return response({ items: [] });
    if (input.endsWith('/premium/templates')) return response({ items: [{ id: 'role:backend_developer', label: 'По роли', query: 'Go backend вакансии' }] });
    if (input.endsWith('/premium/runs') && init?.method === 'POST') { started = true; return response({ id: 'run-1', status: 'queued' }, 202); }
    if (input.endsWith('/premium/runs')) return response({ items: [] });
    if (input.endsWith('/premium/runs/run-1')) return response({ id: 'run-1', status: 'queued' });
    if (input.endsWith('/premium/runs/run-1/results')) return response({ items: [], total: 0 });
    return response({}, 404);
  }));
  render(<MiniappPage/>);
  await screen.findByText('Live vacancy');
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Навигация' })).getByRole('button', { name: /Premium поиск/ }));
  expect(await screen.findByText('Go backend вакансии')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Запустить поиск' }));
  await waitFor(() => expect(started).toBe(true));
});

it('creates a profile in the shared bot store before showing it in the Mini App', async () => {
  let created = false;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({
      user: { id: 1, first_name: 'Tester' },
      profiles: created ? [liveProfile, { ...liveProfile, profile_id: 'p2', name: 'Frontend Role', is_active: false }] : [liveProfile],
      active_profile_id: 'p1', premium_enabled: true,
    });
    if (input.endsWith('/profiles') && init?.method === 'POST') { created = true; return response({ ...liveProfile, profile_id: 'p2', name: 'Frontend Role', is_active: false }, 201); }
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved')) return response({ items: [] });
    if (input.endsWith('/premium/templates')) return response({ items: [] });
    if (input.endsWith('/premium/runs')) return response({ items: [] });
    return response({}, 404);
  }));
  render(<MiniappPage/>);
  await screen.findByText('Live vacancy');
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Навигация' })).getByRole('button', { name: 'Мои профили' }));
  fireEvent.click(screen.getByRole('button', { name: 'Новый профиль' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Название профиля' }), { target: { value: 'Frontend Role' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Роль' }), { target: { value: 'frontend_developer' } });
  fireEvent.click(screen.getByRole('button', { name: /Сохранить профиль/ }));
  await waitFor(() => expect(created).toBe(true));
  expect(await screen.findByText('Frontend Role')).toBeInTheDocument();
});
