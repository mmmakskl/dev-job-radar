import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { searchApi, type SearchCapabilities, type SearchResult, type SearchRun } from '../lib/api';
import { SearchPanel } from './search-panel';

vi.mock('../lib/api', () => ({ searchApi: { capabilities: vi.fn(), runs: vi.fn(), run: vi.fn(), results: vi.fn(), create: vi.fn(), cancel: vi.fn(), action: vi.fn() } }));
const capabilities: SearchCapabilities = { publisher_configured: true, sources: [{ key: 'telegram', label: 'Telegram · опубликованные', enabled: true, reason: null }, { key: 'premium', label: 'Telegram Premium', enabled: true, reason: null }, { key: 'threads', label: 'Threads', enabled: true, reason: null }] };
const run: SearchRun = { id: 'run1', query: 'Golang', sources: ['telegram', 'premium', 'threads'], track: 'go', mode: 'preview', result_limit: 50, period_days: 7, include_review: true, status: 'completed', created_at: '2026-09-15T12:00:00Z', source_states: { telegram: { status: 'completed' }, premium: { status: 'completed' }, threads: { status: 'completed' } } };
const result: SearchResult = { id: 'result1', source: 'threads', external_id: 'post1', text: 'Go backend vacancy', username: 'jobs', permalink: 'https://www.threads.net/@jobs/post/123', timestamp: run.created_at, title: 'Go developer', company: 'Company', summary: 'Backend role', language: 'en', classification: 'review', confidence: 0.7, can_persist: true, action_state: 'idle', action_error: null, publication_status: 'preview', found_queries: ['Golang', 'Go backend'] };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(searchApi.capabilities).mockResolvedValue(capabilities);
  vi.mocked(searchApi.runs).mockResolvedValue({ items: [run] });
  vi.mocked(searchApi.run).mockResolvedValue(run);
  vi.mocked(searchApi.results).mockResolvedValue({ items: [result], total: 1, offset: 0, limit: 25 });
  vi.mocked(searchApi.create).mockResolvedValue({ ...run, id: 'run2' });
  vi.mocked(searchApi.action).mockResolvedValue({ ok: true });
  vi.mocked(searchApi.cancel).mockResolvedValue({ ...run, status: 'cancelled' });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('unified search', () => {
  it('shows source identities, provenance and original links without adding Threads as a channel', async () => {
    render(<SearchPanel/>);
    expect(await screen.findByRole('link', { name: 'Открыть оригинал ↗' })).toHaveAttribute('href', result.permalink);
    expect(screen.getByText('Threads · @jobs')).toBeInTheDocument();
    expect(screen.getByText('Уверенность: 70%')).toBeInTheDocument();
    expect(screen.getByText('Найдено по запросам: Golang, Go backend')).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Добавить канал/ })).not.toBeInTheDocument();
  });
  it.each(['premium', 'threads'])('keeps search usable when %s is disabled', async (key) => {
    vi.mocked(searchApi.capabilities).mockResolvedValue({ ...capabilities, sources: capabilities.sources.map(source => source.key === key ? { ...source, enabled: false, reason: 'feature_disabled' } : source) });
    render(<SearchPanel/>);
    const source = capabilities.sources.find(source => source.key === key)!;
    expect(await screen.findByRole('checkbox', { name: source.label })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: source.label })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Telegram · опубликованные' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Запустить поиск' })).toBeEnabled();
  });
  it('confirms and submits selected sources and search parameters', async () => {
    render(<SearchPanel/>);
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Telegram Premium' }));
    fireEvent.change(screen.getByLabelText('Поисковый запрос'), { target: { value: 'Go backend' } });
    fireEvent.click(screen.getByRole('button', { name: 'Запустить поиск' }));
    expect(searchApi.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(searchApi.create).toHaveBeenCalledWith({ query: 'Go backend', sources: ['telegram', 'threads'], track: 'go', mode: 'preview', result_limit: 50, period_days: 7, include_review: true }));
  });
  it('keeps partial results visible next to an independent source failure and retry time', async () => {
    vi.mocked(searchApi.run).mockResolvedValue({ ...run, status: 'completed_with_errors', source_states: { telegram: { status: 'completed' }, premium: { status: 'failed', reason: 'free_quota_exhausted' }, threads: { status: 'rate_limited', retry_at: '2026-09-15T13:00:00Z' } } });
    render(<SearchPanel/>);
    expect(await screen.findByText('Go developer')).toBeInTheDocument();
    expect(within(screen.getByLabelText('Статус Telegram Premium')).getByText('Ошибка источника')).toBeInTheDocument();
    expect(within(screen.getByLabelText('Статус Threads')).getByText(/Повтор после/)).toBeInTheDocument();
    expect(screen.getByText('Завершён с частичными ошибками')).toBeInTheDocument();
  });
  it('polls partial results every two seconds and stops on unmount', async () => {
    vi.useFakeTimers(); vi.mocked(searchApi.run).mockResolvedValue({ ...run, status: 'running' });
    let view: ReturnType<typeof render>;
    await act(async () => { view = render(<SearchPanel/>); });
    expect(searchApi.results).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(searchApi.results).toHaveBeenCalledTimes(2);
    view!.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(searchApi.results).toHaveBeenCalledTimes(2);
  });
  it('refresh creates a new run using historical parameters', async () => {
    render(<SearchPanel/>);
    fireEvent.click(await screen.findByRole('button', { name: 'Обновить поиск' }));
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(searchApi.create).toHaveBeenCalledWith(expect.objectContaining({ query: run.query, sources: run.sources, mode: run.mode, period_days: 7, result_limit: 50 })));
  });
  it.each(['save', 'publish', 'reject', 'mark_duplicate'])('confirms %s and waits for server state', async action => {
    render(<SearchPanel/>);
    fireEvent.change(await screen.findByRole('combobox', { name: 'Действия с вакансией «Go developer»' }), { target: { value: action } });
    expect(searchApi.action).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(searchApi.action).toHaveBeenCalledWith('result1', action));
    expect(screen.queryByText('Опубликован')).not.toBeInTheDocument();
  });
  it('filters review results independently of the next launch settings', async () => {
    render(<SearchPanel/>); await screen.findByText('Go developer');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Показывать неуверенные результаты' }));
    await waitFor(() => expect(searchApi.results).toHaveBeenCalledWith('run1', 0, false));
  });
  it('disables publication when publisher is unavailable', async () => {
    vi.mocked(searchApi.capabilities).mockResolvedValue({ ...capabilities, publisher_configured: false });
    render(<SearchPanel/>); await screen.findByText('Go developer');
    expect(screen.getByRole('option', { name: 'Опубликовать в Candidate Bot' })).toBeDisabled();
    expect(screen.getByRole('option', { name: 'Сохранить и опубликовать' })).toBeDisabled();
  });
  it('never renders raw source errors, action errors or unsafe links', async () => {
    vi.mocked(searchApi.run).mockResolvedValue({ ...run, source_states: { threads: { status: 'failed', reason: 'access_token=secret' } } });
    vi.mocked(searchApi.results).mockResolvedValue({ items: [{ ...result, action_error: 'access_token=secret', permalink: 'javascript:alert(1)' }], total: 1, offset: 0, limit: 25 });
    render(<SearchPanel/>); await screen.findByText('Go developer');
    expect(screen.queryByText(/access_token/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('Не удалось выполнить действие. Повторите позже.')).toBeInTheDocument();
  });
});
