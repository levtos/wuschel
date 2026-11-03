import { IDBFactory } from 'fake-indexeddb';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.unmock('@/lib/request-budget');
beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('indexedDB', new IDBFactory());
});
afterEach(() => vi.useRealTimers());

it('shares the 100/61s budget across two React clients, invalidations and a reload', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  const { api } = await import('./api');
  const budget = await import('./request-budget');
  const waits: boolean[] = [];
  const unsubscribe = budget.subscribeBudgetWait(() => waits.push(budget.budgetWaiting()));
  const fetch = vi.fn(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  function Client({ name }: { name: string }) {
    const [done, setDone] = useState(0);
    return <><button onClick={() => { void Promise.all(Array.from({ length: 55 }, () => api('info'))).then(() => setDone(55)); }}>{name}</button><output>{name}:{done}</output></>;
  }
  const view = render(<><Client name="Tab A" /><Client name="Tab B" /></>);
  fireEvent.click(screen.getByText('Tab A'));
  fireEvent.click(screen.getByText('Tab B'));
  // fake-indexeddb dispatches transactions on the real event loop.
  await act(async () => { for (let i = 0; i < 800; i++) await new Promise((r) => setImmediate(r)); });
  expect(fetch).toHaveBeenCalledTimes(100);
  expect(budget.budgetWaiting()).toBe(true);
  view.unmount();
  vi.resetModules();
  const reloaded = await import('./api');
  let reloadDone = false;
  void reloaded.api('info').then(() => { reloadDone = true; });
  await act(async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); });
  expect(fetch).toHaveBeenCalledTimes(100);
  expect(reloadDone).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(61_000); for (let i = 0; i < 100; i++) await new Promise((r) => setImmediate(r)); });
  expect(fetch).toHaveBeenCalledTimes(111);
  expect(reloadDone).toBe(true);
  expect(budget.budgetWaiting()).toBe(false);
  expect(waits).toContain(true);
  expect(waits.at(-1)).toBe(false);
  unsubscribe();
});

it.each(['registry/drafts/a/activate', 'registry/rollback', 'commands', 'tokens/rotate'])('never replays %s after HTTP 429', async (path) => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  const { api } = await import('./api');
  const fetch = vi.fn(async () => new Response(JSON.stringify({ error: 'rate_limited' }), { status: 429, headers: { 'Retry-After': '90' } }));
  vi.stubGlobal('fetch', fetch);
  function Action() {
    const [result, set] = useState('ready');
    return <><button onClick={() => { void api(path, { method: 'POST', body: { role: 'admin' } }).catch(() => set('limited')); }}>Action</button><output>{result}</output></>;
  }
  render(<Action />);
  fireEvent.click(screen.getByText('Action'));
  await act(async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); });
  expect(screen.getByText('limited')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  const controller = new AbortController();
  const reading = api('info', { signal: controller.signal }).catch((e) => e);
  await act(async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); });
  expect(fetch).toHaveBeenCalledTimes(1); // shared cooldown applies to a different request
  controller.abort();
  expect((await reading).name).toBe('AbortError');
  await vi.advanceTimersByTimeAsync(100_000);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('reserves clock and restart together before constructing the unchanged 30s window', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.setSystemTime(new Date('2026-10-23T10:00:00Z'));
  const { api, apiWithFreshServerTime } = await import('./api');
  const calls: { path: string; at: number; body?: { issued_at: string; valid_until: string } }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(String(init.body)) as { issued_at: string; valid_until: string } : undefined;
    calls.push({ path, at: Date.now(), body });
    return new Response(JSON.stringify(body ? { status: Date.parse(body.valid_until) <= Date.now() ? 'expired' : 'accepted' } : { server_time: new Date().toISOString() }));
  }));
  const flush = async () => { for (let i = 0; i < 800; i++) await new Promise(resolve => setImmediate(resolve)); };
  const initial = Promise.all(Array.from({ length: 99 }, () => api('info')));
  await flush(); await initial;
  const prepare = vi.fn((serverTime: string) => ({ issued_at: serverTime, valid_until: new Date(Date.parse(serverTime) + 30_000).toISOString() }));
  const sending = apiWithFreshServerTime('manual-restarts', prepare);
  await flush();
  expect(calls).toHaveLength(99); // One free slot cannot admit half the pair.
  expect(prepare).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(60_999); await flush();
  expect(calls).toHaveLength(99);
  await vi.advanceTimersByTimeAsync(1); await flush();
  expect(await sending).toEqual({ status: 'accepted' });
  expect(calls).toHaveLength(101);
  expect(calls.at(-2)!.path).toBe('./api/v1/info');
  const post = calls.at(-1)!;
  expect(post.path).toBe('./api/v1/manual-restarts');
  expect(post.at - Date.parse(post.body!.issued_at)).toBe(0);
  expect(Date.parse(post.body!.valid_until) - Date.parse(post.body!.issued_at)).toBe(30_000);
  // Both slots count: 98 more reads exhaust the new window, the 99th waits.
  const rest = Promise.all(Array.from({ length: 99 }, () => api('info')));
  await flush(); expect(calls).toHaveLength(199);
  await vi.advanceTimersByTimeAsync(61_000); await flush(); await rest;
  expect(calls).toHaveLength(200);
});

it('cancels a queued clock/restart pair without reading the clock or constructing a request', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  const { api, apiWithFreshServerTime } = await import('./api');
  const fetch = vi.fn(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const initial = Promise.all(Array.from({ length: 99 }, () => api('info')));
  for (let i = 0; i < 800; i++) await new Promise(resolve => setImmediate(resolve));
  await initial;
  const controller = new AbortController(), prepare = vi.fn();
  const sending = apiWithFreshServerTime('manual-restarts', prepare, controller.signal).catch(error => error);
  for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve));
  controller.abort();
  expect((await sending).name).toBe('AbortError');
  await vi.advanceTimersByTimeAsync(61_000);
  expect(fetch).toHaveBeenCalledTimes(99);
  expect(prepare).not.toHaveBeenCalled();
});
