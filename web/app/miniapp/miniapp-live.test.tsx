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

it('shows one-time-code sign in outside Telegram without sample vacancies', async () => {
  delete (window as Window & { Telegram?: unknown }).Telegram;
  const fetchMock = vi.fn(() => response({ authenticated: false }));
  vi.stubGlobal('fetch', fetchMock);

  render(<MiniappPage/>);

  expect(await screen.findByRole('heading', { name: /Подходящие вакансии.*В одном месте/ })).toBeInTheDocument();
  expect(screen.getByLabelText('Код из Telegram')).toBeInTheDocument();
  expect(screen.queryByText('Go Backend Developer')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('opens browser profile with a code, sends CSRF for edits, and logs out', async () => {
  delete (window as Window & { Telegram?: unknown }).Telegram;
  let loggedIn = false;
  let csrfUsed = false;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/auth/session')) return response({ authenticated: loggedIn, csrf_token: loggedIn ? 'csrf-value' : undefined });
    if (input.endsWith('/auth/code')) { loggedIn = true; return response({ ok: true, csrf_token: 'csrf-value' }); }
    if (input.endsWith('/auth/logout')) { loggedIn = false; return response({ ok: true }); }
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [], total: 0 });
    if (input.endsWith('/saved')) return response({ items: [] });
    if (input.endsWith('/premium/templates') || input.endsWith('/premium/runs')) return response({ items: [] });
    if (input.endsWith('/profiles') && init?.method === 'POST') {
      csrfUsed = (init.headers as Record<string, string>)['X-Candidate-CSRF'] === 'csrf-value';
      return response({ ...liveProfile, profile_id: 'p2' }, 201);
    }
    return response({}, 404);
  }));

  render(<MiniappPage/>);
  fireEvent.change(await screen.findByLabelText('Код из Telegram'), { target: { value: '1234567890' } });
  fireEvent.click(screen.getByRole('button', { name: /Войти на сайт/ }));
  await screen.findByText('Live Profile');
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Навигация' })).getByRole('button', { name: /профили/i }));
  fireEvent.click(screen.getByRole('button', { name: 'Новый профиль' }));
  fireEvent.change(screen.getByPlaceholderText('Например, Go Backend'), { target: { value: 'Browser Role' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Вид разработки' }), { target: { value: 'backend_developer' } });
  fireEvent.click(screen.getByRole('button', { name: /Сохранить профиль/ }));
  await waitFor(() => expect(csrfUsed).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'Выйти' }));
  expect(await screen.findByLabelText('Код из Telegram')).toBeInTheDocument();
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

it('shows live Premium search status and starts a run from the active profile template', async () => {
  let started = false;
  const startedAt = '2026-11-12T10:15:00+00:00';
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved')) return response({ items: [] });
    if (input.endsWith('/premium/templates')) return response({ items: [{ id: 'role:backend_developer', label: 'По роли', query: 'Go backend вакансии' }] });
    if (input.endsWith('/premium/runs') && init?.method === 'POST') { started = true; return response({ id: 'run-1', status: 'queued', created_at: startedAt, query: 'Go backend вакансии', confirmed_count: 0, metrics: { scanned: 12, found: 2, pending: 1, accepted: 0, rejected: 0, errors: 0 }, source_states: { premium: { status: 'queued' } } }, 202); }
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
  expect(await screen.findByRole('heading', { name: 'Go backend вакансии' })).toBeInTheDocument();
  expect(screen.getByText('Просмотрено')).toBeInTheDocument();
  expect(screen.getByText('12')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Последние запуски' })).toBeInTheDocument();
  expect(screen.getAllByText('В очереди')).toHaveLength(3);
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
  fireEvent.change(screen.getByRole('combobox', { name: 'Вид разработки' }), { target: { value: 'frontend_developer' } });
  fireEvent.click(screen.getByRole('button', { name: /Сохранить профиль/ }));
  await waitFor(() => expect(created).toBe(true));
  await screen.findByText('Live vacancy');
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Навигация' })).getByRole('button', { name: 'Мои профили' }));
  expect(await screen.findByText('Frontend Role')).toBeInTheDocument();
});

it('starts first-time users in profile setup and opens the feed after saving', async () => {
  let created = false;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({
      user: { id: 1, first_name: 'Tester' }, profiles: created ? [liveProfile] : [],
      active_profile_id: created ? 'p1' : null, premium_enabled: true,
    });
    if (input.endsWith('/profiles') && init?.method === 'POST') { created = true; return response(liveProfile, 201); }
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: created ? [liveVacancy] : [], total: created ? 1 : 0 });
    if (input.endsWith('/saved') || input.endsWith('/premium/templates') || input.endsWith('/premium/runs')) return response({ items: [] });
    return response({}, 404);
  }));
  render(<MiniappPage/>);
  expect(await screen.findByRole('dialog', { name: 'Что вам подходит?' })).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Название профиля' }), { target: { value: 'Go Backend' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Вид разработки' }), { target: { value: 'backend_developer' } });
  fireEvent.click(screen.getByRole('button', { name: /Открыть вакансии/ }));
  expect(await screen.findByText('Live vacancy')).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'Что вам подходит?' })).not.toBeInTheDocument();
});

it('sends selected backend stacks and multiple grades when saving a profile', async () => {
  let submitted: Record<string, unknown> | null = null;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/profiles') && init?.method === 'POST') {
      submitted = JSON.parse(String(init.body));
      return response(liveProfile, 201);
    }
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
  fireEvent.change(screen.getByRole('textbox', { name: 'Название профиля' }), { target: { value: 'Backend Role' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Вид разработки' }), { target: { value: 'backend_developer' } });
  fireEvent.click(screen.getByRole('button', { name: 'Язык разработки' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Go' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'C++' }));
  fireEvent.click(screen.getByRole('button', { name: 'Грейды' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Middle' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Senior' }));
  fireEvent.click(screen.getByRole('button', { name: /Сохранить профиль/ }));

  await waitFor(() => expect(submitted).toMatchObject({
    stacks: ['go', 'cpp'], seniority: ['middle', 'senior'],
  }));
});

it('edits profile filters and loads the complete vacancy text', async () => {
  let submitted: Record<string, unknown> | undefined;
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/profiles/p1') && init?.method === 'PUT') { submitted = JSON.parse(String(init.body)); return response(liveProfile); }
    if (input.endsWith('/vacancies/live-1/text')) return response({ text: 'Полный текст\nВторая строка' });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved') || input.endsWith('/premium/templates') || input.endsWith('/premium/runs')) return response({ items: [] });
    return response({}, 404);
  }));
  render(<MiniappPage/>);
  await screen.findByText('Live vacancy');
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Навигация' })).getByRole('button', { name: 'Мои профили' }));
  fireEvent.click(screen.getByRole('button', { name: /Изменить/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Формат работы' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Удалённо' }));
  fireEvent.click(screen.getByRole('button', { name: 'Регион' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Европа' }));
  fireEvent.click(screen.getByRole('button', { name: 'Язык вакансии' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Английский' }));
  fireEvent.click(screen.getByRole('button', { name: /Сохранить профиль/ }));
  await waitFor(() => expect(submitted).toMatchObject({ formats: ['remote'], geography: ['europe'], vacancy_languages: ['en'] }));
  fireEvent.click(await screen.findByRole('button', { name: 'Live vacancy' }));
  expect(await screen.findByText(/Вторая строка/)).toBeInTheDocument();
});

it('filters the feed without changing the profile', async () => {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${input}`);
    if (input.endsWith('/me')) return response({ user: { id: 1, first_name: 'Tester' }, profiles: [liveProfile], active_profile_id: 'p1', premium_enabled: true });
    if (input.endsWith('/feed/settings')) return response({ older_days: 7, max_older_days: 30 });
    if (input.includes('/feed')) return response({ items: [liveVacancy], total: 1 });
    if (input.endsWith('/saved') || input.endsWith('/premium/templates') || input.endsWith('/premium/runs')) return response({ items: [] });
    return response({}, 404);
  }));
  render(<MiniappPage/>);
  await screen.findByText('Live vacancy');
  expect(screen.queryByText('Фильтры профиля')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Фильтры' }));
  fireEvent.click(screen.getByRole('button', { name: 'Формат работы' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Удалённо' }));
  fireEvent.click(screen.getByRole('button', { name: 'Показать вакансии' }));
  await waitFor(() => expect(calls.some(call => call.includes('formats=remote'))).toBe(true));
  expect(calls.some(call => call.startsWith('PUT ') && call.includes('/profiles/'))).toBe(false);
});
