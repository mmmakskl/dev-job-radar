import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitForAction } from './api';

const response = (status:string) => Promise.resolve({
  ok: true,
  json: async () => ({
    id: 'action-1', action: 'sync_channels', status,
    received: 2, created: 1, updated: 1, skipped: 0, failed: 0,
  }),
} as Response);

describe('waitForAction', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('polls until a terminal action and returns progress counts', async () => {
    vi.useFakeTimers();
    const fetch = vi.fn()
      .mockImplementationOnce(() => response('pending'))
      .mockImplementationOnce(() => response('running'))
      .mockImplementationOnce(() => response('succeeded'));
    vi.stubGlobal('fetch', fetch);

    const result = waitForAction('action-1');
    await vi.runAllTimersAsync();

    await expect(result).resolves.toMatchObject({status:'succeeded', received:2});
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('stops after the bounded polling window', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => response('pending')));

    const result = waitForAction('action-1', 2);
    const expectation = expect(result).rejects.toThrow('дольше ожидаемого');
    await vi.runAllTimersAsync();

    await expectation;
  });
});
