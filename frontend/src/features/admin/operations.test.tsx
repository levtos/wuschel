import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { installFakeApi, reply } from '../../../test/fakeApi';
import OperationsPage from './OperationsPage';
import SystemStatePage from '../diagnostics/SystemStatePage';
import type { OperationSettings, SystemState } from './operations';

const settings: OperationSettings = { notice: null, settings: [
  { key: 'ingest_queue_size', name: 'Eingangs-Warteschlange', explanation: 'Hier warten Eingänge.', value: 256, active_value: 256, default: 256, minimum: 1, maximum: 16384, unit: 'Einträge', integer: true, restart_required: true, pending_restart: false },
] };
const state: SystemState = {
  ready: true, publication_confirmed: true, publication_seq: 42, version: '1.0.0b25', persistence: 'available', ha: 'not_needed', bridge: 'not_needed', mqtt: 'disabled', traber_version: null,
  operations_notice: 'operations_invalid', startup_attempts: 2, startup: { total: 500 },
  queues: { ingest: { depth: 3, capacity: 256, overflows: 2 }, refresh: { depth: 0, capacity: 256, overflows: 0 } },
  event_loop: { lag_ms: null, max_lag_ms: null }, commits: { count: 2, window: 256, median_ms: 4, p95_ms: 7 },
  retries: { ha: { count: 3, consecutive: 0, last_error: 'TimeoutError', last_error_at: '2026-11-13T12:00:00Z' } }, reasons: [],
};
function mount(page: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{page}</QueryClientProvider>);
}
afterEach(() => vi.useRealTimers());

it('validates empty, fractional and out-of-range values before saving and resets to default', async () => {
  const server = installFakeApi(); const user = userEvent.setup();
  server.routes.push({ match: /^operations\/settings$/, respond: () => reply(200, settings) });
  mount(<OperationsPage />);
  const input = await screen.findByRole('spinbutton', { name: 'Eingangs-Warteschlange' });
  expect(screen.getByText(/Standard: 256 Einträge/)).toBeInTheDocument();
  expect(screen.getByText('Wirkt nach Neustart')).toBeInTheDocument();
  await user.clear(input);
  expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled();
  await user.type(input, '0.5');
  expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled();
  await user.clear(input); await user.type(input, '20000');
  expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Auf Standard zurücksetzen' }));
  expect(input).toHaveValue(256);
  expect(screen.getByRole('button', { name: 'Speichern' })).toBeEnabled();
  expect(server.calls.filter(call => call.method === 'PUT')).toHaveLength(0);
});

it('saves independent of drafts, shows pending restart and keeps server validation readable', async () => {
  const server = installFakeApi(); const user = userEvent.setup(); let fail = false;
  server.routes.push(
    { match: /^operations\/settings$/, method: 'PUT', respond: () => fail ? reply(400, { error: 'invalid_operations_settings', issues: [{ code: 'range', path: '/ingest_queue_size', message: 'Gib einen erlaubten Wert ein.' }] }) : reply(200, { ...settings, settings: settings.settings.map(field => ({ ...field, value: 8, pending_restart: true })) }) },
    { match: /^operations\/settings$/, method: 'GET', respond: () => reply(200, settings) },
  );
  mount(<OperationsPage />);
  const input = await screen.findByRole('spinbutton');
  await user.clear(input); await user.type(input, '8');
  await user.click(screen.getByRole('button', { name: 'Speichern' }));
  await screen.findByText('Gespeichert. Diese Änderung wartet auf den Neustart.');
  expect(server.calls.find(call => call.method === 'PUT')?.body).toEqual({ ingest_queue_size: 8 });
  expect(screen.getByText('Aktuell wirksam: 256 Einträge')).toBeInTheDocument();
  fail = true;
  await user.click(screen.getByRole('button', { name: 'Speichern' }));
  await screen.findByText('Gib einen erlaubten Wert ein.');
  expect(input).toHaveValue(8);
});

it('shows a read failure without enabling save', async () => {
  const server = installFakeApi();
  server.routes.push({ match: /^operations\/settings$/, respond: () => reply(503, { error: 'persistence_unavailable' }) });
  mount(<OperationsPage />);
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled();
});

it('shows actual diagnostics, unknown measurements and a stale warning after refresh failure', async () => {
  const server = installFakeApi(); const user = userEvent.setup(); let fail = false;
  server.routes.push({ match: /^system\/state$/, respond: () => fail ? reply(503, { error: 'persistence_unavailable' }) : reply(200, state) });
  mount(<SystemStatePage />);
  await screen.findByText('Bereit');
  expect(screen.getByText('42')).toBeInTheDocument();
  expect(screen.getByText('3 / 256')).toBeInTheDocument();
  expect(screen.getByText(/Die Datei mit den Betriebswerten ist nicht lesbar/)).toBeInTheDocument();
  expect(screen.getAllByText('Noch nicht gemessen')).toHaveLength(2);
  expect(screen.getByText('TimeoutError')).toBeInTheDocument();
  fail = true;
  await user.click(screen.getByRole('button', { name: 'Jetzt aktualisieren' }));
  await screen.findByText(/Du siehst den letzten gelesenen Stand/);
  expect(screen.getByText('42')).toBeInTheDocument();
});

it('polls again after five seconds and stops when the page unmounts', async () => {
  vi.useFakeTimers();
  const server = installFakeApi();
  server.routes.push({ match: /^system\/state$/, respond: () => reply(200, state) });
  const view = mount(<SystemStatePage />);
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(screen.getByText('Bereit')).toBeInTheDocument();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(server.calls.filter(call => call.path === 'system/state').length).toBeGreaterThanOrEqual(2);
  view.unmount();
  const count = server.calls.filter(call => call.path === 'system/state').length;
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(server.calls.filter(call => call.path === 'system/state')).toHaveLength(count);
});
