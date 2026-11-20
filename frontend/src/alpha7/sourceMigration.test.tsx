import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router';
import { Workbench } from '@/Workbench';
import { planDevice } from '@/features/sources/devicePlan';
import { historicalSource, migrationSourceId, objects, planSourceMigration, type MigrationOptions } from '@/features/sources/sourceMigration';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { SourceMigrationDialog } from '@/features/sources/SourceMigrationDialog';
import { clone, type JsonObject } from '@/lib/json';
import type { HaEntity, RevisionRow } from '@/lib/types';
import { installFakeApi, makeDraft, reply, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

const device: HaEntity = { entity_id: 'sensor.zimmer', display_name: 'Zimmer', unit: '°C', device_class: 'temperature', state_class: 'measurement', current_value: '21.5', attributes: [] };
const original = () => planDevice(sampleConfig(), device, { contract: 'new', freshness: { mode: 'periodic_ttl', interval_s: 900, future_tolerance_s: 1 } }).config;
const revision = (config: JsonObject): RevisionRow => ({ revision: 1, created_at: '2026-10-16T10:00:00Z', checksum: 'sum', config });
const options = (config: JsonObject): MigrationOptions => ({ sourceId: 'source.neu', name: 'Neue Temperatur', schema: { type: 'text', required: true, physical_state: false }, adapter: { kind: 'ha_state', ha_entity_id: device.entity_id }, contracts: objects(config.contracts).map(contract => String(contract.contract_id)), adaptPassthrough: true });
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it('moves a whole physical path atomically, restores an attempted locked edit, versions simple templates and preserves the old source', () => {
  const config = original(); const before = clone(config); const id = String(objects(config.sources)[0]!.source_id);
  objects(config.sources)[0]!.source_type = 'text'; objects(config.sources)[0]!.value_schema = { type: 'text', required: true, physical_state: false };
  const result = planSourceMigration(config, [revision(before)], id, options(config));
  expect(objects(result.config.sources)[0]).toEqual(objects(before.sources)[0]);
  expect(objects(result.config.sources)[1]).toMatchObject({ source_id: 'source.neu', source_type: 'text' });
  expect(result.config.bindings).toEqual([expect.objectContaining({ source_id: 'source.neu', transform: { kind: 'type_cast', target: 'text' } })]);
  expect(objects(result.config.contracts)[0]).toMatchObject({ inputs: [{ name: 'geraet', ref: 'source.neu' }], type_version: 2 });
  expect(objects(result.config.definitions)[0]).toEqual(objects(before.definitions)[0]);
  expect(objects(result.config.definitions)[1]).toMatchObject({ type_version: 2, inputs: [{ name: 'geraet', schema: options(config).schema }] });
  expect(result.newVersions).toBe(1); expect(result.movedBinding).toBe(true);
  expect(historicalSource([revision(before)], id)).toEqual(objects(before.sources)[0]);
  expect(objects(config.sources)[0]!.source_type).toBe('text'); // input was not mutated
});

it('supports a selected-contract move to another device and rejects duplicate physical evidence, liveness and dependency uses', () => {
  const config = original(); const contract = clone(objects(config.contracts)[0]!); contract.contract_id = 'weiterer_vertrag'; config.contracts = [...objects(config.contracts), contract];
  const id = String(objects(config.sources)[0]!.source_id); const opts = options(config); opts.contracts = [String(objects(config.contracts)[0]!.contract_id)];
  expect(() => planSourceMigration(config, [], id, opts)).toThrow(/Das bisherige Gerät wird noch gebraucht/);
  opts.adapter = { kind: 'ha_state', ha_entity_id: 'sensor.anderes_zimmer' };
  const moved = planSourceMigration(config, [], id, opts);
  expect(objects(moved.config.bindings)).toHaveLength(2);
  expect(objects(moved.config.contracts)[1]!.inputs).toEqual(contract.inputs);
  opts.adapter = { kind: 'ha_state', ha_entity_id: device.entity_id }; opts.contracts = options(config).contracts;
  config.dependencies = [{ dependency_id: 'dep.monitor', source_id: id, kind: 'other', monitor: 'source' }];
  expect(() => planSourceMigration(config, [], id, opts)).toThrow(/Das bisherige Gerät wird noch gebraucht/);
  config.dependencies = []; config.sources = [...objects(config.sources), { source_id: 'source.monitor', freshness: { liveness_source: id } }];
  expect(() => planSourceMigration(config, [], id, opts)).toThrow(/Das bisherige Gerät wird noch gebraucht/);
});

it('reserves all historical identities and never rewrites a calculation as a passthrough', () => {
  const config = original(); const id = String(objects(config.sources)[0]!.source_id);
  const old = clone(config); objects(old.sources)[0]!.source_id = 'source.neu';
  expect(() => planSourceMigration(config, [revision(old)], id, options(config))).toThrow(/schon verwendet/);
  expect(migrationSourceId(config, [revision(old)], 'source')).not.toBe('source.neu');
  objects(config.definitions)[0]!.outputs = { value: { op: 'formula', formula: 'sum', args: [] } };
  expect(planSourceMigration(config, [], id, options(config)).newVersions).toBe(0);
  expect(() => planSourceMigration({ ...config, sources: [null] }, [], id, options(config))).toThrow(/unvollständige Listen/);
});

it('opens from live, checks the exact migration and applies only to the draft; changing the form invalidates the verdict', async () => {
  const user = userEvent.setup(); const config = original(); const api = installFakeApi({ revisions: [revision(config)] });
  api.routes.push({ match: /^ha\/entities/, respond: () => reply(200, { items: [device], next_cursor: null }) });
  window.location.hash = '#/sources/devices?item=0';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  await user.click(await screen.findByRole('button', { name: 'Quelle mit anderem Typ anlegen' }, { timeout: 5000 }));
  const dialog = await screen.findByRole('dialog', { name: 'Quelle umziehen' });
  await user.selectOptions(within(dialog).getByLabelText('Neue Werteart'), 'text');
  expect(within(dialog).getByRole('checkbox', { name: 'Zimmer' })).toBeChecked();
  await user.click(within(dialog).getByRole('button', { name: 'Prüfen' }));
  await within(dialog).findByText(/Direkt umgestellte Verträge: 1/);
  expect(within(dialog).getByRole('button', { name: 'In Entwurf speichern' })).toBeEnabled();
  await user.type(within(dialog).getByLabelText('Name der neuen Quelle'), ' geändert');
  expect(within(dialog).getByRole('button', { name: 'In Entwurf speichern' })).toBeDisabled();
  await user.click(within(dialog).getByRole('button', { name: 'Prüfen' }));
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'In Entwurf speichern' })).toBeEnabled());
  await user.click(within(dialog).getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => expect(objects([...api.drafts.values()][0]!.config.sources)).toHaveLength(2));
  expect(api.revisions).toHaveLength(1); expect(api.revisions[0]!.config).toEqual(config);
}, 15_000);

it('discards a delayed validation result after the working draft changes', async () => {
  const user = userEvent.setup(); const config = original(); const api = installFakeApi({ revisions: [revision(config)] });
  let release: (() => void) | undefined;
  api.routes.push({ match: /^registry\/validate$/, respond: async () => { await new Promise<void>(resolve => { release = resolve; }); return reply(200, { valid: true, issues: [] }); } });
  const draft = makeDraft('migration', config, { base_revision: 1 });
  function Subject() {
    const doc = useWorkingDocument();
    return <><button onClick={() => doc.open(draft)}>Open</button><button onClick={() => doc.setAt('/other', true)}>Change</button>{doc.config && <SourceMigrationDialog sourceId={String(objects(config.sources)[0]!.source_id)} onClose={() => {}} />}</>;
  }
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><WorkingDocumentProvider validateDebounceMs={100_000}><Subject /></WorkingDocumentProvider></MemoryRouter></QueryClientProvider>);
  await user.click(screen.getByText('Open')); const dialog = await screen.findByRole('dialog');
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Prüfen' })).toBeEnabled());
  await user.click(within(dialog).getByRole('button', { name: 'Prüfen' }));
  await waitFor(() => expect(release).toBeDefined());
  // Simulate another editor updating the shared document while the modal's request is pending.
  act(() => screen.getByText('Change').click()); release!();
  await within(dialog).findByText(/Der Entwurf oder die Auswahl hat sich geändert/);
  expect(within(dialog).getByRole('button', { name: 'In Entwurf speichern' })).toBeDisabled();
});

it('follows the immutable-source error directly into a migration and shows server validation errors before any edit', async () => {
  const user = userEvent.setup(); const config = original(); const api = installFakeApi({ revisions: [revision(config)] });
  api.issuesFor = () => [{ code: 'input_type_mismatch', path: '/contracts/0/inputs/0', object_id: 'zimmer', message: 'internal' }];
  window.location.hash = `#/sources/devices?move=${encodeURIComponent(String(objects(config.sources)[0]!.source_id))}`;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  const dialog = await screen.findByRole('dialog', { name: 'Quelle umziehen' });
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Prüfen' })).toBeEnabled());
  await user.click(within(dialog).getByRole('button', { name: 'Prüfen' }));
  await within(dialog).findByText(/Der Umzug passt noch nicht/);
  expect(within(dialog).getByRole('button', { name: 'In Entwurf speichern' })).toBeDisabled();
  expect(api.drafts.size).toBe(0);
});
