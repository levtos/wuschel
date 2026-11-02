import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { browserSession, reloadAfterRestart } from './browser-session';
import { useProblems } from './queries';

function visible(value: boolean) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: value ? 'visible' : 'hidden' });
  document.dispatchEvent(new Event('visibilitychange'));
}
afterEach(() => { visible(true); browserSession.newDocument(); vi.useRealTimers(); sessionStorage.clear(); });

it.each([401, 403, 200])('sends at most one request after an invalid Ingress response (%i)', async status => {
  let release!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>(resolve => { release = resolve; }));
  vi.stubGlobal('fetch', fetcher);
  const results = Promise.allSettled([api('info'), api('registry/active'), api('registry/drafts', { method: 'POST', body: {} })]);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  release(new Response(status === 200 ? '<html>Ingress session is not valid</html>' : '{}', { status, headers: { 'Content-Type': status === 200 ? 'text/html' : 'application/json' } }));
  const settled = await results;
  expect(settled.every(result => result.status === 'rejected' && result.reason instanceof ApiError && result.reason.code === 'ingress_session_expired')).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(browserSession.getSnapshot().blocked).toBe(true);
  await expect(api('diagnostics')).rejects.toMatchObject({ code: 'ingress_session_expired' });
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('does not poll a hidden page and resumes only when it becomes visible', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ problems: [] }), { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Probe() { useProblems(1000); return null; }
  const view = render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(fetcher).toHaveBeenCalledTimes(1);
  act(() => visible(false));
  const before = fetcher.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(fetcher).toHaveBeenCalledTimes(before);
  act(() => visible(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
  expect(fetcher.mock.calls.length).toBeGreaterThan(before);
  view.unmount(); client.clear();
});

it('keeps a queued request off the network while hidden', async () => {
  visible(false);
  const fetcher = vi.fn(async () => new Response('{}', { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetcher);
  const request = api('info');
  await Promise.resolve(); await Promise.resolve();
  expect(fetcher).not.toHaveBeenCalled();
  visible(true);
  await request;
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('does not let a response from a previous document expire the new document', async () => {
  let release!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; })).mockResolvedValue(new Response('{}', { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetcher);
  const old = api('info').catch(error => error);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  browserSession.newDocument();
  release(new Response('{}', { status: 401 }));
  expect(await old).toMatchObject({ name: 'AbortError' });
  await expect(api('info')).resolves.toEqual({});
  expect(browserSession.getSnapshot().blocked).toBe(false);
});

it('reloads once for a new backend epoch and then requires an explicit reload', () => {
  const reload = vi.fn();
  reloadAfterRestart('synthetic-new-epoch', reload);
  reloadAfterRestart('synthetic-new-epoch', reload);
  expect(reload).toHaveBeenCalledTimes(1);
  expect(browserSession.getSnapshot().blocked).toBe(true);
});
