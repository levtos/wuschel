import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { contractSources, contractState, contractValue, fieldsForContract } from '@/features/contracts/presentation';
import { historyPoints, MiniHistory, observationTime } from '@/features/contracts/ContractSummary';
import type { Envelope, HaEntity } from '@/lib/types';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

const at = '2026-10-16T10:00:00Z';
const config = sampleConfig({
  sources: [{ source_id: 'source.heat', source_type: 'numeric', display_name: 'Gespeicherter Sensorname', value_schema: { type: 'number', unit: '°C' } }],
  bindings: [{ binding_id: 'binding.heat', source_id: 'source.heat', adapter: { kind: 'ha_state', ha_entity_id: 'sensor.zimmer' } }],
  contracts: [{ contract_id: 'zimmer', display_name: 'Temperatur im Zimmer', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'a', ref: 'source.heat' }] }],
});
const field = { status: 'valid' as const, value: 21.5, quality: 'healthy' as const, reasons: [], evidence: [] };
const envelope: Envelope = { contract_id: 'zimmer', enabled: true, type: { type_id: 'test.echo', type_version: 1 }, fields: { value: { ...field, reasons: [], evidence: [] } }, contract_health: 'healthy', available: true, computed_at: at, published_at: at, publication_seq: 1, registry_revision: 1, epoch_id: 'e1' };
const entities = [{ entity_id: 'sensor.zimmer', display_name: 'Zimmerthermometer' } as HaEntity];
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

function app(hash: string, liveEnvelope = envelope) {
  window.location.hash = hash;
  const store = idleLive();
  vi.spyOn(store, 'getSnapshot').mockReturnValue({ ...store.getSnapshot(), contracts: { zimmer: liveEnvelope }, registryRevision: 1, stale: false, connection: 'open' });
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
}
function fake() {
  const api = installFakeApi({ revisions: [{ revision: 1, config, checksum: 'sum', created_at: at }] });
  api.routes.push({ match: /^ha\/entities/, respond: () => reply(200, { items: entities, next_cursor: null, read_only: true }) });
  return api;
}

it('formats values in German without treating unknown as false', () => {
  expect(contractValue(envelope, { value: { type: 'number', unit: '°C', required: true, physical_state: false } })).toBe('21,5 °C');
  expect(contractValue({ ...envelope, fields: { value: { ...envelope.fields.value!, value: false } } }, {})).toBe('Nein');
  expect(contractValue({ ...envelope, fields: { value: { ...envelope.fields.value!, value: null, status: 'unknown' } } }, {})).toBe('unbekannt');
  expect(contractState(envelope, true, true).label).toBe('Entwurf');
  expect(contractState(envelope, false).tone).toBe('neutral');
});

it('resolves HA names through upstream inputs and protects against cycles', () => {
  const chained = { ...config, contracts: [...config.contracts as [], { contract_id: 'downstream', inputs: [{ ref: 'zimmer.value' }, { ref: 'downstream' }] }] };
  expect(contractSources('downstream', chained, entities)).toEqual([{ id: 'source.heat', name: 'Zimmerthermometer', entityId: 'sensor.zimmer', sourceIndex: 0 }]);
  expect(contractSources('zimmer', config, [])[0]?.name).toBe('Gespeicherter Sensorname');
});

it('takes units from the supplied active configuration without mutating template fields', () => {
  const types: Parameters<typeof fieldsForContract>[2] = [{ key: 'test.echo@1', type_id: 'test.echo', type_version: 1, fields: { value: { type: 'number', required: true, physical_state: false } }, origin: 'package', inputs: [], declarative: false, machine: false }];
  expect(fieldsForContract((config.contracts as any[])[0], config, types).value?.unit).toBe('°C');
  expect(types[0]!.fields.value?.unit).toBeUndefined();
});

it('shows category and room alongside the value, a single state chip and the HA name', async () => {
  fake(); app('#/contracts/list/all');
  const table = await screen.findByRole('table', { name: 'Verträge' });
  await within(table).findByText('21,5 °C');
  expect(within(table).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['Name', 'Kategorie', 'Raum', 'Wert', 'Zustand', 'Quelle']);
  expect(await within(table).findByText('Zimmerthermometer')).toBeVisible();
  expect(within(table).getAllByText('Gesund')).toHaveLength(1);
  expect(within(table).getByRole('link', { name: 'Temperatur im Zimmer' })).toHaveAttribute('title', 'zimmer');
  expect(within(screen.getByRole('combobox', { name: 'Zustand' })).getAllByRole('option').map(option => option.textContent)).toEqual(['Jeder Zustand', 'Gesund', 'Eingeschränkt', 'Unbekannt', 'Ausgeschaltet', 'Entwurf']);
});

it('shows the HA identifier only once when it is also the available name', async () => {
  const api = fake();
  api.routes.unshift({ match: /^ha\/entities/, respond: () => reply(200, { items: [{ ...entities[0], display_name: 'sensor.zimmer' }], next_cursor: null, read_only: true }) });
  app('#/contracts/view/zimmer');
  await screen.findByRole('link', { name: 'sensor.zimmer' });
  expect(screen.getAllByText('sensor.zimmer')).toHaveLength(1);
});

it('formats a timestamp contract value in the list instead of exposing its wire format', async () => {
  fake(); app('#/contracts/list/all', { ...envelope, fields: { stamp: { ...field, value: '2026-01-01T12:00:00Z' } } });
  const table = await screen.findByRole('table', { name: 'Verträge' });
  await waitFor(() => expect(within(table).getByText('1. Jan. 2026, 13:00')).toBeVisible());
  expect(within(table).queryByText('2026-01-01T12:00:00Z')).not.toBeInTheDocument();
});

it('shows now, a sentence for why, named origin and history with gaps', async () => {
  const api = fake();
  // The chart now has a real 24-hour window; keep all three samples inside it.
  const chartAt = Date.now() - 3_600_000;
  api.routes.push({ match: /^history\/query\/contracts/, respond: () => reply(200, { items: [
    { id: '1', contract_id: 'zimmer', registry_revision: 1, published_at: new Date(chartAt).toISOString(), fields: { value: field } },
    { id: '2', contract_id: 'zimmer', registry_revision: 1, published_at: new Date(chartAt + 60_000).toISOString(), fields: { value: { ...field, value: null, status: 'unknown' } } },
    { id: '3', contract_id: 'zimmer', registry_revision: 1, published_at: new Date(chartAt + 120_000).toISOString(), fields: { value: { ...field, value: 22 } } },
  ], next_cursor: null }) });
  app('#/contracts/view/zimmer');
  expect(await screen.findByRole('heading', { name: 'Wert jetzt' })).toBeVisible();
  expect(screen.getByTestId('value-value')).toHaveTextContent('21,5 °C');
  expect(screen.getByRole('heading', { name: 'Warum?' })).toBeVisible();
  expect(screen.getByText("Alle benötigten Angaben sind vorhanden und verwendbar.")).toBeVisible();
  expect(await screen.findByRole('link', { name: 'Zimmerthermometer' })).toBeVisible();
  const chart = await screen.findByRole('img', { name: 'Verlauf: Wert' });
  expect(chart.querySelectorAll('polyline')).toHaveLength(2);
  expect(screen.queryByText('publication_seq')).not.toBeInTheDocument();
});

it('starts a draft-only contract with an explicit draft state and no invented live value', async () => {
  const user = userEvent.setup(); const api = installFakeApi(); app('#/contracts/list/all');
  await user.click(await screen.findByRole('button', { name: 'Neuer Vertrag' }));
  await user.click(await screen.findByRole('button', { name: 'Ohne Gerät anlegen' }));
  await user.type(screen.getByLabelText("Vertragskennung"), 'mein.neuer');
  await user.click(screen.getByRole('button', { name: 'Anlegen' }));
  expect(await screen.findByText('Dieser Vertrag gehört zum Entwurf. Prüfe ihn und wähle Übernehmen, damit er Werte liefern kann.')).toBeVisible();
  expect(screen.queryByTestId('value-value')).not.toBeInTheDocument();
  expect(api.revisions).toHaveLength(0);
});

it('keeps history scoped to the selected contract and retains unknown samples as gaps', () => {
  const points = historyPoints([
    { id: '1', contract_id: 'other', published_at: at, fields: { value: field } },
    { id: '2', contract_id: 'zimmer', published_at: at, fields: { value: { ...field, status: 'unknown', value: null } } },
    { id: '3', contract_id: 'zimmer', published_at: 'bad', fields: { value: field } },
  ], 'zimmer', 'value');
  expect(points).toHaveLength(1); expect(points[0]?.field?.status).toBe('unknown');
});

it('offers declared flow commands on the detail page and requires confirmation before sending', async () => {
  const user = userEvent.setup(); const api = fake();
  api.revisions[0]!.config = sampleConfig({ contracts: [{ contract_id: 'zimmer', type_id: 'test.state_machine', type_version: 2, enabled: true, inputs: [] }] });
  api.routes.push({ match: /^commands$/, method: 'POST', respond: (_path, body) => reply(200, { command_id: body.command_id, status: 'accepted' }) });
  app('#/contracts/view/zimmer', { ...envelope, type: { type_id: 'test.state_machine', type_version: 2 } });
  expect(await screen.findByRole('button', { name: 'Zustand B anfordern' })).toBeVisible();
  expect(screen.queryByLabelText('Befehl')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /Befehl senden/ }));
  expect(api.calls.some(call => call.method === 'POST' && call.path === 'commands')).toBe(false);
  await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Senden' }));
  expect(await screen.findByText('angenommen')).toBeVisible();
  const command = api.calls.find(call => call.method === 'POST' && call.path === 'commands')!.body as Record<string, unknown>;
  expect(command.command).toBe('request_b'); expect(command.expected_registry_revision).toBe(1);
  expect(command).not.toHaveProperty('state');
});

it('does not label a value from a newer live version with the previous version unit', async () => {
  fake(); app('#/contracts/view/zimmer', { ...envelope, registry_revision: 2 });
  await screen.findByRole('heading', { name: 'Temperatur im Zimmer' });
  expect(screen.queryByTestId('value-value')).not.toBeInTheDocument();
  expect(screen.queryByText('21,5 °C')).not.toBeInTheDocument();
});

it('draws boolean history as full-width steps with Ja/Nein axis labels', () => {
  render(<MiniHistory name="value" schema={{ type: 'bool', required: true, physical_state: false }} points={[
    { at, field: { ...field, value: false } },
    { at: '2026-10-16T10:01:00Z', field: { ...field, value: true } },
  ]} />);
  const chart = screen.getByRole('img', { name: 'Verlauf: Wert' });
  expect(chart).toHaveClass('w-full', 'h-auto');
  expect(within(chart).getByText('Ja')).toBeVisible();
  expect(within(chart).getByText('Nein')).toBeVisible();
  expect(chart.querySelector('polyline')).toHaveAttribute('data-interpolation', 'step');
  expect(chart.querySelector('polyline')?.getAttribute('points')).toBe('110,132 980,132 980,32');
});

it('orders equal-time publications by server sequence instead of reversing valid samples and gaps', () => {
  const points = historyPoints([
    { id: 'later', contract_id: 'zimmer', published_at: at, publication_seq: 2, fields: { value: { ...field, value: true } } },
    { id: 'earlier', contract_id: 'zimmer', published_at: at, publication_seq: 1, fields: { value: { ...field, value: null, status: 'unknown' } } },
  ], 'zimmer', 'value');
  expect(points.map(point => point.field?.status)).toEqual(['unknown', 'valid']);
});

it('continues a currently confirmed boolean through now without inventing stored samples', () => {
  const yes = { ...field, value: true };
  render(<MiniHistory name="value" current={yes} now={Date.parse('2026-10-16T10:02:00Z')} points={[{ at, field: yes }]} />);
  expect(screen.getByRole('img').querySelector('polyline')).toHaveAttribute('points', '110,32 980,32 980,32');
  expect(screen.getAllByRole('row')).toHaveLength(2); // header + one real publication
});

it('does not draw a confirmed tail across a recorded gap or an unknown publication', () => {
  const yes = { ...field, value: true };
  const points = historyPoints([{ id: 'p', contract_id: 'zimmer', published_at: at, fields: { value: yes } }], 'zimmer', 'value', undefined, [{ id: 'gap', at: '2026-10-16T10:01:00Z' }]);
  const view = render(<MiniHistory name="value" current={yes} now={Date.parse('2026-10-16T10:02:00Z')} points={points} />);
  expect(screen.getByRole('img').querySelectorAll('circle')).toHaveLength(1);
  view.rerender(<MiniHistory name="value" current={yes} now={Date.parse('2026-10-16T10:02:00Z')} points={[{ at, field: yes }, { at: '2026-10-16T10:01:00Z', field: { ...yes, value: null, status: 'unknown' } }]} />);
  expect(screen.getByRole('img').querySelectorAll('circle')).toHaveLength(1);
});

it('names the affected input and the age of its measured value without treating receipt as measurement', async () => {
  const api = fake();
  const measured = new Date(Date.now() - 300_000).toISOString();
  api.routes.push({ match: /^evidence\/source.heat/, respond: () => reply(200, [{ observation_kind: 'report', ha_last_reported: measured, received_at: new Date().toISOString() }]) });
  app('#/contracts/view/zimmer', { ...envelope, fields: { value: { ...field, status: 'unknown', value: null, reasons: [{ code: 'input_stale', input: 'source.heat', since: at }] } } });
  expect(await screen.findByText(/Eingang „Gespeicherter Sensorname“: Der zuletzt gemessene Wert ist 5 min alt/)).toBeVisible();
  expect(observationTime({ observation_kind: 'report', ha_last_changed: at, received_at: at })).toBeUndefined();
});
