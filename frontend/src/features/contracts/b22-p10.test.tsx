import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, expect, it } from 'vitest';
import type { Json, JsonObject } from '@/lib/json';
import type { Envelope, FieldValue } from '@/lib/types';
import type { FieldSchema } from '@/features/authoring/model';
import { ContractSummary } from './ContractSummary';
import { CurrentValues } from './CurrentValues';
import { outputFields } from './outputPresentation';
import { groupedInputs, contractSources, contractFieldValue } from './presentation';
import { notApplicableValue } from './inputPresentation';
import { installFakeApi, reply, sampleConfig } from '../../../test/fakeApi';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { hasValueLabel, valueText } from '@/lib/valueText';
import { Workbench } from '@/Workbench';
import { idleLive } from '../../../test/harness';

const stamp = '2032-01-05T10:00:00Z';
const episode = '22222222-2222-4222-8222-222222222222';
const field = (value: Json): FieldValue => ({ status: 'valid', quality: 'healthy', value, reasons: [], evidence: [], since_at: stamp, attribution: { kind: 'unknown', basis: 'synthetic' } });
const schema = (type: FieldSchema['type'], extra: JsonObject = {}): FieldSchema => ({ type, required: true, physical_state: false, ...extra });
function envelope(values: Record<string, FieldValue>, type = 'synthetic.view'): Envelope {
  return { contract_id: 'synthetic.view', type: { id: type, version: 1 }, enabled: true, fields: values, available: true, contract_health: 'healthy', computed_at: stamp, published_at: stamp, epoch_id: 'synthetic', publication_seq: 1, registry_revision: 1 };
}
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });

it('shows fifteen declared fields as grouped normal-sized rows, with date formatting and one attribution', () => {
  const values = { value: field('pre_sleep'), since_at: field(stamp), sleep_context_active: field(true), wake_next_wake_start: field(stamp), wake_window_start: field(stamp), wake_window_end: field(stamp), wake_plan_date: field('2032-01-05'), wake_status: field('scheduled'), wake_decision: field('rule_wake'), wake_minimum_sleep_applied: field(false), episode_active: field(true), episode_started_at: field(stamp), entry_origin: field('initial'), last_action_origin: field('confirmed'), episode_id: field(episode) };
  const fields = outputFields('sleep.flow', Object.fromEntries(Object.keys(values).map(name => [name, schema(name.includes('_at') || name.includes('window') || name.includes('wake_start') ? 'timestamp' : typeof values[name as keyof typeof values].value === 'boolean' ? 'bool' : 'text')])));
  render(<CurrentValues envelope={envelope(values, 'sleep.flow')} fields={fields} config={null} attribution />);
  const table = screen.getByRole('table', { name: 'Aktuelle Vertragswerte' });
  expect(within(table).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['Feld', 'Wert', 'Seit/Stand']);
  expect(within(table).getAllByRole('rowgroup').slice(1).map(group => group.querySelector('th')?.textContent)).toEqual(['Jetzt', 'Nächstes Wecken', 'Durchlauf']);
  expect(within(table).getByText('05.01.2032')).toBeVisible();
  expect(table.querySelector('.text-4xl')).toBeNull();
  expect(table.textContent).not.toContain('2032-01-05');
  expect(table.textContent).not.toContain(episode);
  expect(screen.getAllByTestId('contract-attribution')).toHaveLength(1);
  expect(screen.getAllByText('Auslöser nicht sicher bekannt')).toHaveLength(1);
});

it('keeps a single value as a tile and follows schema order when no groups exist', () => {
  const view = render(<CurrentValues envelope={envelope({ value: field(true) })} fields={{ value: schema('bool') }} config={null} attribution={false} />);
  expect(screen.getByTestId('value-value')).toHaveClass('text-4xl');
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  const values = { a: field(1), b: field(2), c: field(3), d: field(4) };
  view.rerender(<CurrentValues envelope={envelope(values)} fields={{ d: schema('number'), b: schema('number'), a: schema('number'), c: schema('number') }} config={null} attribution={false} />);
  const table = screen.getByRole('table');
  expect(Array.from(table.querySelectorAll('[data-testid]'), cell => cell.getAttribute('data-testid'))).toEqual(['value-d', 'value-b', 'value-a', 'value-c']);
});

it('puts the catalog explanation beside the German class and does not translate arbitrary text', () => {
  const enumSchema = schema('enum', { value_catalog: ['private'], legend: { private: { display_name: 'Persönlich', description: 'Synthetische Erklärung der Klasse.' } } });
  render(<CurrentValues envelope={envelope({ value: field('private') })} fields={{ value: enumSchema }} config={null} attribution={false} />);
  expect(screen.getByTestId('value-value')).toHaveTextContent('Persönlich');
  expect(screen.getByText('Synthetische Erklärung der Klasse.')).toBeVisible();
  expect(contractFieldValue('synthetic', 'value', field('autumn'), schema('text'))).toBe('autumn');
  expect(contractFieldValue('synthetic', 'value', field('2032-01-05'), schema('text'))).toBe('05.01.2032');
  expect(valueText('undeclared_synthetic', schema('enum', { value_catalog: ['undeclared_synthetic'] }))).toBe('Deutscher Anzeigename fehlt');
});

it('requires German labels for every enum code in every shipped template file', () => {
  const root = resolve(process.cwd(), '../examples');
  const files = readdirSync(root).filter(name => name.includes('templates') && name.endsWith('.json'));
  const missing: string[] = [];
  let count = 0;
  function visit(value: unknown, path: string) {
    if (Array.isArray(value)) { value.forEach((item, index) => visit(item, `${path}/${index}`)); return; }
    if (!value || typeof value !== 'object') return;
    const object = value as Record<string, unknown>;
    if (object.type === 'enum' && Array.isArray(object.value_catalog)) for (const code of object.value_catalog) { count++; if (!hasValueLabel(String(code), object as FieldSchema)) missing.push(`${path}:${String(code)}`); }
    for (const [key, item] of Object.entries(object)) visit(item, `${path}/${key}`);
  }
  for (const name of files) visit(JSON.parse(readFileSync(resolve(root, name), 'utf8')), name);
  expect(count).toBeGreaterThan(74);
  expect(missing).toEqual([]);
});

it('groups ten fields from the same upstream contract into one row with expandable fields', () => {
  const inputs = Array.from({ length: 10 }, (_, index) => ({ name: `input_${index}`, ref: `synthetic.flow.field_${index}` }));
  const config = sampleConfig({ contracts: [{ contract_id: 'synthetic.flow', display_name: 'Synthetischer Ablauf', inputs: [] }, { contract_id: 'synthetic.view', inputs }] });
  const groups = groupedInputs(contractSources('synthetic.view', config, []));
  expect(groups).toHaveLength(1);
  expect(groups[0]!.members).toHaveLength(10);
  expect(groups[0]!.href).toBe('/contracts/view/synthetic.flow');
});

it('renders one upstream link rather than ten duplicates in the real contract list', async () => {
  const names = Array.from({ length: 10 }, (_, index) => `field_${index}`);
  const fields = Object.fromEntries(names.map(name => [name, schema('bool')]));
  const config = sampleConfig({ definitions: [
    { type_id: 'synthetic.flow', type_version: 1, language_version: 1, inputs: [], schema: { fields }, outputs: Object.fromEntries(names.map(name => [name, { op: 'literal', value: true, schema: schema('bool') }])) },
    { type_id: 'synthetic.view', type_version: 1, language_version: 1, inputs: names.map(name => ({ name, schema: schema('bool') })), schema: { fields: { value: schema('bool') } }, outputs: { value: { op: 'and', args: names.map(name => ({ op: 'input', name })) } } },
  ], contracts: [
    { contract_id: 'synthetic.flow', display_name: 'Synthetischer Ablauf', type_id: 'synthetic.flow', type_version: 1, enabled: true, parameters: {}, inputs: [] },
    { contract_id: 'synthetic.view', display_name: 'Synthetische Sicht', type_id: 'synthetic.view', type_version: 1, enabled: true, parameters: {}, inputs: names.map(name => ({ name, ref: `synthetic.flow.${name}` })) },
  ] });
  installFakeApi({ revisions: [{ revision: 1, config, checksum: 'synthetic', created_at: stamp }] });
  location.hash = '#/contracts/list/all';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench live={idleLive()} onSetup={() => {}} /></QueryClientProvider>);
  const table = await screen.findByRole('table', { name: 'Verträge' });
  const row = await within(table).findByRole('row', { name: /Synthetische Sicht/ });
  expect(within(row).getAllByRole('link', { name: /Synthetischer Ablauf/ })).toHaveLength(1);
  await userEvent.click(within(row).getByText('10 Werte'));
  expect(within(row).getAllByRole('listitem')).toHaveLength(10);
});

it('names the public consumers of an internal contract', () => {
  const config = sampleConfig({ contracts: [
    { contract_id: 'synthetic.flow', type_id: 'sleep.flow', type_version: 1, display_name: 'Synthetischer Ablauf (intern)', inputs: [] },
    { contract_id: 'synthetic.view', type_id: 'synthetic.view', type_version: 1, display_name: 'Synthetische öffentliche Sicht', inputs: [{ name: 'a', ref: 'synthetic.flow.value' }] },
  ] });
  installFakeApi();
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ContractSummary envelope={{ ...envelope({ value: field(true) }, 'sleep.flow'), contract_id: 'synthetic.flow' }} stale={false} fields={{ value: schema('bool') }} config={config} machine={false} /></MemoryRouter></QueryClientProvider>);
  expect(screen.getByText('Dieser interne Vertrag liefert Angaben für diese öffentlichen Verträge:')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Synthetische öffentliche Sicht' })).toHaveAttribute('href', '/contracts/view/synthetic.view');
});

it('explains an already confirmed not-applicable value only from a proven input guard', () => {
  const output = { op: 'first_match', cases: [{ when: { op: 'not', args: [{ op: 'input', name: 'applicable' }] }, then: { op: 'not_applicable', arg: { op: 'literal', value: true } } }] };
  const config = sampleConfig({ contracts: [{ contract_id: 'synthetic.view', type_id: 'synthetic.view', type_version: 1, inputs: [{ name: 'applicable', ref: 'source.synthetic' }] }], sources: [{ source_id: 'source.synthetic', display_name: 'Fernseher an' }], definitions: [{ type_id: 'synthetic.view', type_version: 1, inputs: [{ name: 'applicable', schema: schema('bool') }], outputs: { value: output } }] });
  const current = envelope({ value: { ...field(null), status: 'not_applicable' } });
  const inputs = { applicable: field(false) };
  expect(notApplicableValue(current, 'value', config, inputs)).toBe('Gilt gerade nicht: Fernseher an – Nein');
  expect(notApplicableValue(current, 'value', config, { applicable: { ...field(null), status: 'unknown' } })).toBe('Gilt gerade nicht; der auslösende Eingang ist noch nicht belegt');
  expect(inputs.applicable.value).toBe(false);
  expect(current.fields.value!.status).toBe('not_applicable');
});

it('uses one origin panel, revision/epoch checked input values and a technical-only episode identifier', async () => {
  const inputs = Array.from({ length: 10 }, (_, index) => ({ name: `input_${index}`, ref: `synthetic.flow.field_${index}` }));
  const config = sampleConfig({ contracts: [{ contract_id: 'synthetic.flow', display_name: 'Synthetischer Ablauf', inputs: [] }, { contract_id: 'synthetic.view', type_id: 'synthetic.view', type_version: 1, inputs }] });
  const fake = installFakeApi();
  fake.routes.push({ match: /^diagnostics\/contracts\/synthetic.view\?include_inputs=1$/, respond: () => reply(200, { registry_revision: 1, input_epoch_id: 'synthetic', inputs: Object.fromEntries(inputs.map(input => [input.name, field(true)])) }) });
  const value = envelope({ value: field(true), episode_id: field(episode) });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><ContractSummary envelope={value} stale={false} fields={{ value: schema('bool'), episode_id: schema('text') }} config={config} machine={false} /></MemoryRouter></QueryClientProvider>);
  expect(screen.getAllByRole('heading', { name: 'Woher kommt der Wert?' })).toHaveLength(1);
  expect(screen.queryByRole('heading', { name: 'Warum?' })).not.toBeInTheDocument();
  expect(screen.queryByText(episode)).not.toBeInTheDocument();
  expect(screen.getAllByRole('link', { name: 'Synthetischer Ablauf' })).toHaveLength(1);
  await userEvent.click(screen.getByText('10 Werte'));
  await waitFor(() => expect(screen.queryByText('Aktueller Eingangswert noch nicht belegt')).not.toBeInTheDocument());
  await userEvent.click(screen.getByText('Technische Details'));
  expect(await screen.findByTestId('value-episode_id')).toHaveTextContent(episode);
});
