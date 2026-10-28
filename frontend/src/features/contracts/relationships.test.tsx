import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { expect, it } from 'vitest';
import { fieldCompatible, type FieldSchema } from '@/features/authoring/model';
import { ContractSummary } from './ContractSummary';
import { affectedContractInputs, contractSourcePaths, contractSources } from './presentation';
import type { Envelope, Registry } from '@/lib/types';
import { installFakeApi, sampleConfig } from '../../../test/fakeApi';

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
  const where = screen.getByRole('heading', { name: 'Woher?' }).closest('section') ?? screen.getByRole('heading', { name: 'Woher?' }).parentElement!.parentElement!;
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
