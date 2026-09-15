import { afterEach, expect, it, vi } from 'vitest';
import { searchApi } from './api';
afterEach(() => vi.unstubAllGlobals());
it('sends selected sources, confirmation and CSRF on search mutations', async () => {
  document.cookie = 'admin_csrf=test-csrf';
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'new-run' }) });
  vi.stubGlobal('fetch', fetch);
  await searchApi.create({ query: 'Go developer', sources: ['threads'], track: 'go', mode: 'preview', result_limit: 25, period_days: 3, include_review: true });
  expect(fetch).toHaveBeenCalledWith('/api/v1/search/runs', expect.objectContaining({ method: 'POST', credentials: 'same-origin', headers: expect.objectContaining({ 'X-CSRF-Token': 'test-csrf' }), body: JSON.stringify({ query: 'Go developer', sources: ['threads'], track: 'go', mode: 'preview', result_limit: 25, period_days: 3, include_review: true, confirmed: true }) }));
  await searchApi.action('result/1', 'publish');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/search/results/result%2F1/actions', expect.objectContaining({ method: 'POST', body: JSON.stringify({ action: 'publish', confirmed: true }) }));
});
it('encodes run identifiers and paginates review results', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
  vi.stubGlobal('fetch', fetch);
  await searchApi.results('run/1', 25, false);
  expect(fetch).toHaveBeenCalledWith('/api/v1/search/runs/run%2F1/results?offset=25&limit=25&include_review=false', expect.anything());
});
