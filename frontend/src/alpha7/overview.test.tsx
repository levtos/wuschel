import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { contractCounts, eventText, overviewEvents, problemAdvice } from '@/features/overview/overviewModel';
import { relativeTime } from '@/lib/format';
import type { Envelope, HistoryItem } from '@/lib/types';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

const at = '2026-10-16T12:00:00Z';
const config = sampleConfig({ contracts: [
  { contract_id: 'room', display_name: 'Temperatur im Zimmer', enabled: true, type_id: 'test.boolean', type_version: 1, inputs: [] },
  { contract_id: 'off', display_name: 'Ausgeschalteter Vertrag', enabled: false, type_id: 'test.boolean', type_version: 1, inputs: [] },
] });
const envelope = (id: string, health: 'healthy' | 'degraded'): Envelope => ({ contract_id: id, contract_health: health } as Envelope);
const row = (id: string, code?: string, contract = 'room', onset = at): HistoryItem => ({ id, contract_id: contract, published_at: `2026-10-16T12:0${id}:00Z`, fields: { value: { reasons: code ? [{ code, since: onset }] : [] } } });
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it('counts only enabled server health and keeps missing snapshot counts unknown', () => {
  expect(contractCounts(config, { room: envelope('room', 'degraded'), off: envelope('off', 'healthy') })).toEqual({ active: 1, healthy: 0, degraded: 1, off: 1 });
  expect(contractCounts(config, null)).toEqual({ active: 1, healthy: null, degraded: null, off: 1 });
});

it('shows activation and rollback facts but leaves draft bookkeeping out', () => {
  const events = overviewEvents([
    { id: '1', operation: 'activate', at, revision: 4 },
    { id: '2', operation: 'draft_create', at, revision: 5 },
    { id: '3', operation: 'draft_delete', at, revision: 5 },
    { id: '4', operation: 'rollback', at, revision: 6 },
  ], [], []);
  expect(events).toHaveLength(2);
  expect(eventText(events[0]!, config)).toBe("Konfiguration 4 übernommen");
  expect(eventText(events[1]!, config)).toBe("Konfiguration 6 durch Zurücksetzen übernommen");
});

it('deduplicates repeated stale publications, remembers recovery and uses server onset', () => {
  const events = overviewEvents([], [row('1', 'input_stale'), row('2', 'input_stale'), row('3'), row('4', 'input_stale', 'room', '2026-10-16T12:04:00Z'), row('5', 'evaluation_error', 'room', '2026-10-16T12:05:00Z')], []);
  expect(events.map(event => event.kind)).toEqual(['contract_error', 'contract_stale', 'contract_stale']);
  expect(events.filter(event => event.kind === 'contract_stale').map(event => event.at)).toEqual(['2026-10-16T12:04:00Z', at]);
  expect(eventText(events[1]!, config)).toBe('Temperatur im Zimmer: Wert veraltet');
});

it('marks session connection observations and rejects invalid event timestamps', () => {
  const events = overviewEvents([{ id: 'bad', operation: 'activate', at: 'broken', revision: 8 }], [], [{ id: 'connection-1', at, kind: 'ha_disconnected' }]);
  expect(events).toEqual([{ id: 'connection-1', at, kind: 'ha_disconnected', session: true }]);
});

it('offers everyday explanations with a direct solution and German relative time', () => {
  const advice = problemAdvice([{ code: 'input_stale', input: 'a', since: at }], config, 'room');
  expect(advice.text).toBe('Ein benötigter Wert ist zu alt.');
  expect(advice.to).toBe('/sources/devices');
  expect(relativeTime(at, Date.parse(at) + 9 * 60_000)).toBe('vor 9 Min.');
  expect(relativeTime('broken')).toBe('Zeit unbekannt');
});

function app(live = idleLive()) {
  window.location.hash = '#/';
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={live} /></QueryClientProvider>);
}

it('shows the server count of unbounded values during HA loss', async () => {
  installFakeApi();
  const live = idleLive();
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), stale: false, service: {
    ready: true, publication_confirmed: true, persistence: 'available', ha: 'disconnected', bridge: 'unavailable', mqtt: 'disabled', reasons: [],
    ha_disconnected_since: at, ha_unbounded_values: 3,
  } });
  app(live);
  expect(await screen.findByText(/Home Assistant getrennt seit .* – 3 Werte ohne Frist gelten unverändert weiter/)).toBeVisible();
});

it('keeps valid unassigned values healthy and lists configuration separately', async () => {
  installFakeApi({ revisions: [{ revision: 4, config, checksum: 'sum', created_at: at }] });
  const live = idleLive();
  const current = { ...envelope('room', 'healthy'), fields: { value: { status: 'valid', value: 'unassigned', quality: 'healthy', reasons: [], evidence: [], configuration: { complete: false, hints: [{ code: 'activity_mapping_missing', message: 'Noch nicht zugeordnet' }] } } } } as Envelope;
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), stale: false, registryRevision: 4, contracts: { room: current } });
  app(live);
  expect(await screen.findByRole('link', { name: '1 gesund' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Signal gestört' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Konfiguration unvollständig' })).toBeVisible();
  expect(screen.getByText('Noch nicht zugeordnet')).toBeVisible();
});

it('uses status colors for known counts and equally sized quick actions', async () => {
  installFakeApi({ revisions: [{ revision: 4, config, checksum: 'sum', created_at: at }] });
  const live = idleLive();
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), stale: false, registryRevision: 4, contracts: { room: { ...envelope('room', 'healthy'), fields: {} } } });
  app(live);
  const healthy = await screen.findByRole('link', { name: '1 gesund' });
  expect(healthy.querySelector('span')).toHaveClass('text-ok');
  expect(screen.getByRole('link', { name: '0 eingeschränkt' }).querySelector('span')).toHaveClass('text-warn');
  expect(screen.getByRole('link', { name: '1 ausgeschaltet' }).querySelector('span')).toHaveClass('text-muted');
  expect(screen.getByRole('link', { name: '1 eingeschaltet' }).querySelector('span')).toHaveClass('text-fg');
  expect(screen.queryByRole('button', { name: 'Bearbeiten' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Änderungen übernehmen' })).not.toBeInTheDocument();
  for (const action of [screen.getByRole('button', { name: 'Neuer Vertrag' }), screen.getByRole('button', { name: 'Import & Export' })]) expect(action).toHaveClass('min-h-11');
});

it.each(['healthy', 'degraded'] as const)('uses current %s publications instead of stale polled problem details', async health => {
  const api = installFakeApi({ revisions: [{ revision: 4, config, checksum: 'sum', created_at: at }] });
  api.routes.push({ match: /^diagnostics\/problems$/, respond: () => reply(200, { problems: [{ contract_id: 'room', reasons: [{ code: 'evaluation_error', since: at }] }] }) });
  const current = { ...envelope('room', health), fields: { value: { status: 'unknown', value: null, quality: 'degraded', evidence: [], reasons: health === 'degraded' ? [{ code: 'input_stale', input: 'a', since: at }] : [] } } } as Envelope;
  const live = idleLive();
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), stale: false, registryRevision: 4, contracts: { room: current } });
  app(live);
  if (health === 'degraded') expect(await screen.findByText('Ein benötigter Wert ist zu alt.')).toBeVisible();
  else {
    await screen.findByRole('link', { name: /1 gesund/ });
    expect(await screen.findByText('Zurzeit sind keine Probleme gemeldet.')).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Temperatur im Zimmer' })).not.toBeInTheDocument();
  }
});

it.each(['healthy', 'degraded'] as const)('uses the same %s category for counts, filter and chip even with an unknown stale field', async health => {
  const user = userEvent.setup();
  installFakeApi({ revisions: [{ revision: 4, config, checksum: 'sum', created_at: at }] });
  const live = idleLive();
  const current = { ...envelope('room', health), registry_revision: 4, fields: { value: { status: 'unknown', value: null, quality: 'degraded', evidence: [], reasons: [{ code: 'input_stale', input: 'a', since: at }] } } } as Envelope;
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), stale: false, registryRevision: 4, contracts: { room: current } });
  app(live);
  await user.click(await screen.findByRole('link', { name: health === 'healthy' ? '1 gesund' : '1 eingeschränkt' }));
  const table = await screen.findByRole('table', { name: 'Verträge' });
  expect(within(table).getByRole('link', { name: 'Temperatur im Zimmer' })).toBeVisible();
  expect(within(table).getByText(health === 'healthy' ? 'Gesund' : 'Eingeschränkt')).toBeVisible();
  expect(within(table).queryByText('Veraltet')).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Zustand' })).toHaveValue(health);
  await user.selectOptions(screen.getByRole('combobox', { name: 'Zustand' }), 'unknown');
  expect(screen.queryByRole('link', { name: 'Temperatur im Zimmer' })).not.toBeInTheDocument();
});

it('renders exactly three overview blocks and links the count to a filtered list', async () => {
  const user = userEvent.setup();
  const api = installFakeApi({ revisions: [{ revision: 4, config, checksum: 'sum', created_at: at }] });
  api.activations.push({ revision: 4, at, operation: 'activate' });
  app();
  const main = await screen.findByRole('main');
  await within(main).findByText("Konfiguration 4 übernommen");
  expect(within(main).getAllByRole('heading', { level: 2 }).map(h => h.textContent)).toEqual(['Schnellaktionen', 'Verträge', 'Letzte Ereignisse']);
  expect(within(main).queryByText('Installationskennung')).not.toBeInTheDocument();
  expect(within(main).queryByText('Bereiche')).not.toBeInTheDocument();
  expect(within(main).queryByRole('button', { name: 'Änderungen übernehmen' })).not.toBeInTheDocument();
  expect(within(main).getByRole('link', { name: 'Alle im Protokoll' })).toHaveAttribute('href', '#/settings/log');
  await user.click(within(main).getByRole('link', { name: /1 ausgeschaltet/ }));
  expect(await screen.findByRole('link', { name: 'Ausgeschalteter Vertrag' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Temperatur im Zimmer' })).not.toBeInTheDocument();
  expect(window.location.hash).toBe('#/contracts/list/all?filter=off');
});

it('explains a problem without raw reason codes and opens new contract creation in place', async () => {
  const user = userEvent.setup(); const api = installFakeApi({ revisions: [{ revision: 4, config, checksum: 'sum', created_at: at }] });
  api.routes.push({ match: /^diagnostics\/problems$/, respond: () => reply(200, { problems: [{ contract_id: 'room', reasons: [{ code: 'input_stale', input: 'a', since: at }] }] }) });
  app();
  expect(await screen.findByText('Ein benötigter Wert ist zu alt.')).toBeVisible();
  expect(screen.queryByText('input_stale')).not.toBeInTheDocument();
  await user.click(screen.getByText("Details"));
  expect(screen.getByRole('link', { name: 'Quelle prüfen' })).toHaveAttribute('href', '#/sources/devices');
  await user.click(screen.getByRole('button', { name: 'Neuer Vertrag' }));
  expect(await screen.findByRole('dialog', { name: 'Neuer Vertrag' })).toBeVisible();
  expect(api.drafts.size).toBe(1);
  expect(window.location.hash).toBe('#/contracts/list/all');
});
