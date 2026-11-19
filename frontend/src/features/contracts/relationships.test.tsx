import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { expect, it, vi } from 'vitest';
import { fieldCompatible, type FieldSchema } from '@/features/authoring/model';
import { ContractSummary } from './ContractSummary';
import { affectedContractInputs, contractSourcePaths, contractSources } from './presentation';
import type { Envelope, Registry } from '@/lib/types';
import { installFakeApi, reply, sampleConfig } from '../../../test/fakeApi';
import { caps, idleLive, wrap } from '../../../test/harness';
import { Workbench } from '@/Workbench';
import { InputOrigins } from './InputOrigins';

const config: Registry = sampleConfig({
  sources: [{ source_id: 'source.exception', origin: 'manual', display_name: 'Weckausnahme', value_schema: { type: 'text' } }],
  bindings: [{ binding_id: 'binding.exception', source_id: 'source.exception', adapter: { kind: 'manual' } }],
  contracts: [
    { contract_id: 'date', display_name: 'Date aktiv', type_id: 'presence.command_context', inputs: [{ name: 'sleep', ref: 'sleep.state.value' }] },
    { contract_id: 'sleep.state', display_name: 'Schlafzustand', type_id: 'sleep.state_view', inputs: [{ name: 'sleep', ref: 'sleep.flow.value' }] },
    { contract_id: 'sleep.flow', display_name: 'Ablauf', type_id: 'sleep.flow', inputs: [{ name: 'manual_exception', ref: 'source.exception' }] },
  ],
});
const envelope: Envelope = {
  contract_id: 'date', type: { id: 'presence.command_context', version: 1 }, enabled: true,
  fields: { value: { status: 'valid', value: false, quality: 'degraded', evidence: [], reasons: [
    { code: 'restore_context_missing', input: 'sleep.flow', since: '2026-01-01T12:00:00Z' },
  ] } }, available: false, contract_health: 'degraded', computed_at: '', published_at: '',
  epoch_id: 'e', publication_seq: 1, registry_revision: 1,
};

it('links the immediate sleep state, owns controls at the flow, and hides technical paths initially', async () => {
  installFakeApi();
  const user = userEvent.setup();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>
    <ContractSummary envelope={envelope} stale={false} fields={{}} config={config} machine={false} />
  </MemoryRouter></QueryClientProvider>);
  // #97 P10 replaces the two panels with one origin/cause panel.
  const where = screen.getByRole('heading', { name: 'Woher kommt der Wert?' }).closest('section') ?? screen.getByRole('heading', { name: 'Woher kommt der Wert?' }).parentElement!.parentElement!;
  expect(within(where).getByRole('link', { name: 'Schlafzustand' })).toHaveAttribute('href', '/contracts/view/sleep.state');
  expect(screen.getByText('Schlafzustand: Beginnt der Schlaf, endet Besuch/Date; während des Schlafs wird Start ignoriert; sonst bestimmen die Knöpfe.')).toBeVisible();
  expect(screen.getByTestId('value-value')).toHaveTextContent('Nein');
  expect(screen.getByText('Verlässlichkeit: Eingeschränkt')).toBeVisible();
  expect(screen.getByText(/^Betroffener Eingang:/)).toBeVisible();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByText(/Die Eingabe „Weckausnahme“ pflegst du/)).toBeVisible();
  expect(screen.queryByText(/Date aktiv → Schlafzustand/)).not.toBeInTheDocument();
  await user.click(screen.getByText('Technische Details'));
  expect(await screen.findByText(/Date aktiv → Schlafzustand →/)).toBeVisible();
});

it('shows immediate window contracts and retains multiple technical paths to shared evidence', () => {
  const windows = { ...config, contracts: [
    ...config.contracts as [],
    { contract_id: 'windows', inputs: [{ name: 'left', ref: 'sleep.state.value' }, { name: 'right', ref: 'sleep.flow.value' }] },
  ] };
  expect(contractSources('windows', windows, []).map(input => input.id)).toEqual(['sleep.state', 'sleep.flow']);
  expect(contractSourcePaths('windows', windows, [])).toHaveLength(2);
  expect(affectedContractInputs(envelope, config, []).map(input => input.id)).toEqual(['sleep.state']);
});

it('matches declarative type, enum subset and optional unit compatibility', () => {
  const schema = (type: FieldSchema['type'], extra = {}): FieldSchema => ({ type, required: true, physical_state: false, ...extra });
  expect(fieldCompatible(schema('enum', { value_catalog: ['sleep'] }), schema('text'))).toBe(true);
  expect(fieldCompatible(schema('text'), schema('enum', { value_catalog: ['sleep'] }))).toBe(false);
  expect(fieldCompatible(schema('enum', { value_catalog: ['sleep'] }), schema('enum', { value_catalog: ['sleep', 'awake'] }))).toBe(true);
  expect(fieldCompatible(schema('enum', { value_catalog: ['other'] }), schema('enum', { value_catalog: ['sleep'] }))).toBe(false);
  expect(fieldCompatible(schema('number', { unit: '°C' }), schema('number'))).toBe(true);
  expect(fieldCompatible(schema('number', { unit: 'W' }), schema('number', { unit: '°C' }))).toBe(false);
  expect(fieldCompatible(schema('bool'), schema('object'))).toBe(false);
});

it('shows minutes since midnight as a clock time in the input origins', () => {
  wrap(<InputOrigins envelope={envelope} config={config} rule="" sources={[{
    id: 'test.clock', kind: 'contract', sourceIndex: -1, name: 'Testuhr', inputName: 'minute',
    ref: 'test.clock.minute_of_day', href: '/contracts/view/test.clock', role: '',
  }]} inputs={{ minute: { status: 'valid', quality: 'healthy', value: 1284, evidence: [], reasons: [] } }} />);
  expect(screen.getByText('Uhrzeit 21:24')).toBeVisible();
});

it('follows a calendar cause, binding and template in read mode and returns to the same contract tab', async () => {
  sessionStorage.clear(); localStorage.clear();
  const user = userEvent.setup();
  const type = caps.types[0]!;
  const typed = { type_id: type.type_id, type_version: type.type_version, enabled: true };
  const sourceId = 'source.synthetic_calendar';
  const bindingId = 'binding.synthetic_calendar';
  const rootId = 'test.workday';
  const childId = 'test.day_type';
  const at = '2032-04-05T12:00:00Z';
  const sourceConfig = sampleConfig({
    sources: [{
      source_id: sourceId, source_type: 'state', source_origin: 'local', display_name: 'Synthetischer Kalender',
      calendar_coverage_months: 12, calendar_max_gap_months: 5,
      value_schema: { type: 'object', required: true, physical_state: false },
      freshness: { mode: 'periodic_ttl', interval_s: 3600, future_tolerance_s: 0 },
    }],
    bindings: [{
      binding_id: bindingId, source_id: sourceId, display_name: 'Synthetische Verknüpfung',
      adapter: { kind: 'ha_calendar', ha_entity_id: 'calendar.synthetic', window: { timezone: 'Europe/Berlin', past_days: 0, future_days: 31 }, refresh_s: 900, max_age_s: 3600 },
    }],
    contracts: [
      { ...typed, contract_id: rootId, display_name: 'Synthetischer Arbeitstag', inputs: [{ name: 'day', ref: `${childId}.value` }] },
      { ...typed, contract_id: childId, display_name: 'Synthetische Tagesart', inputs: [{ name: 'holidays', ref: sourceId }] },
    ],
  });
  const fake = installFakeApi({ revisions: [{ revision: 1, config: sourceConfig, checksum: 'synthetic', created_at: at }] });
  const reason = { code: 'calendar_coverage_missing', input: sourceId, since: at, detail: 'Der synthetische Kalender belegt den abgefragten Tag nicht.' };
  fake.routes.push({ match: /^sources\/calendar-status$/, respond: () => reply(200, [{ binding_id: bindingId, source_id: sourceId, fetched_at: at, expires_at: at, coverage: { covered: false, max_gap_months: 5, message: reason.detail } }]) });
  const root = { ...envelope, contract_id: rootId, type: { id: type.type_id, version: type.type_version }, fields: { value: { status: 'unknown', quality: 'degraded', value: null, reasons: [reason], evidence: [] } } } satisfies Envelope;
  const live = idleLive();
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), stale: false, registryRevision: 1, contracts: { [rootId]: root, [childId]: { ...root, contract_id: childId } } });
  const original = `#/contracts/view/${rootId}?tab=state&category=synthetic`;
  location.hash = original;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={live} /></QueryClientProvider>);
  const chain = await screen.findByRole('list', { name: 'Ursachenkette' });
  expect(chain).toHaveTextContent('Synthetische Tagesart');
  expect(screen.getByText(reason.detail)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Feiertagsquelle öffnen' })).toHaveAttribute('href', `#/sources/devices?source_id=${sourceId}`);
  await user.click(within(chain).getByRole('link', { name: 'Kalenderprüfung' }));
  const calendar = await screen.findByRole('region', { name: 'Kalenderqualität' });
  expect(within(calendar).getByText('5 Monate')).toBeVisible();
  expect(within(calendar).getByText('31 Tage')).toBeVisible();
  await user.click(within(calendar).getByRole('link', { name: 'Datenherkunft ansehen' }));
  expect(await screen.findByRole('heading', { name: 'Synthetische Verknüpfung' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Vorherige Ansicht' }));
  await screen.findByRole('region', { name: 'Kalenderqualität' });
  await user.click(screen.getByRole('button', { name: 'Vorherige Ansicht' }));
  await screen.findByRole('list', { name: 'Ursachenkette' });
  expect(location.hash).toBe(original);

  await user.click(screen.getByRole('tab', { name: 'Konfiguration' }));
  const panel = screen.getByRole('tabpanel');
  const template = within(panel).getByRole('link', { name: `${type.type_id} · Version ${type.type_version}` });
  expect(within(panel).queryByLabelText('Verknüpfter Wert')).not.toBeInTheDocument();
  await user.click(template);
  expect(await screen.findByRole('heading', { name: type.type_id, level: 1 })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Vorherige Ansicht' }));
  expect(await screen.findByRole('heading', { name: 'Vorlage und Version' })).toBeVisible();
  expect(fake.calls.some(call => call.method === 'POST' && call.path === 'registry/drafts')).toBe(false);
});
