import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { MemoryRouter } from 'react-router';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { t } from '@/i18n';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { declarativeParameterSchema } from '@/features/authoring/catalog';
import { WakePlanningDialog } from '@/features/contracts/WakePlanningDialog';
import { SourceMigrationDialog } from '@/features/sources/SourceMigrationDialog';
import { planEntityChange } from '@/features/sources/sourceMigration';
import { SourceProblems } from '@/features/contracts/SourceProblems';
import { NO_ROOM } from '@/features/editorial/model';
import { asArray, asObject } from '@/features/authoring/model';
import type { Draft, Envelope } from '@/lib/types';
import type { Json } from '@/lib/json';
import { caps, idleLive, wrap } from '../test/harness';
import { installFakeApi, makeDraft, reply, sampleConfig } from '../test/fakeApi';

const contract = { contract_id: 'synthetic.one', display_name: 'Synthetic contract', type_id: caps.types[0]!.type_id, type_version: caps.types[0]!.type_version, enabled: true, parameters: { unrelated: 42 }, inputs: [] };
const source = { source_id: 'source.synthetic', display_name: 'Synthetic source', source_type: 'text', source_origin: 'local', physical_source_key: 'physical.synthetic', dependency_id: 'dep.synthetic', freshness: { mode: 'event_stateful', future_tolerance_s: 0 } };
const binding = { binding_id: 'binding.synthetic', source_id: source.source_id, adapter: { kind: 'ha_state', ha_entity_id: 'sensor.synthetic_old' }, transform: { kind: 'type_cast', target: 'text' } };
const config = () => sampleConfig({ contracts: [structuredClone(contract)], sources: [structuredClone(source)], bindings: [structuredClone(binding)] });
const revision = (value = config()) => ({ revision: 1, checksum: 'synthetic', config: value, created_at: '2032-04-05T12:00:00Z' });
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function app(hash: string) {
  location.hash = hash;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
}

it('edits only the selected contract through its action and leaves active configuration unchanged', async () => {
  const fake = installFakeApi({ revisions: [revision()] });
  app('#/contracts/view/synthetic.one');
  const user = userEvent.setup();
  await user.click(await screen.findByRole('tab', { name: 'Konfiguration' }));
  const field = within(screen.getByRole('tabpanel')).getByLabelText('Anzeigename');
  expect(field).toBeDisabled();
  await user.click(screen.getByRole('button', { name: t('b22.object.edit') }));
  await waitFor(() => expect(field).toBeEnabled());
  await user.clear(field); await user.type(field, 'Synthetic edited contract');
  await user.click(screen.getByRole('button', { name: t('b22.object.finish') }));
  await waitFor(() => expect(asArray([...fake.drafts.values()][0]?.config.contracts)[0]).toMatchObject({ contract_id: contract.contract_id, display_name: 'Synthetic edited contract' }));
  expect(fake.revisions).toHaveLength(1);
  expect(fake.revisions[0]!.config).toEqual(config());
  expect(screen.queryByRole('button', { name: 'Ansehen' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: t('b22.object.applyChanges') })).toBeVisible();
});

it.each(['rename', 'disable'] as const)('starts a draft for a direct %s action without activating', async action => {
  const fake = installFakeApi({ revisions: [revision()] });
  app('#/contracts/view/synthetic.one');
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: t(`b22.object.${action}`) }));
  if (action === 'rename') {
    const dialog = await screen.findByRole('dialog', { name: t('b22.object.rename') });
    await user.clear(within(dialog).getByLabelText(t('b22.object.name')));
    await user.type(within(dialog).getByLabelText(t('b22.object.name')), 'Synthetic renamed contract');
    await user.click(within(dialog).getByRole('button', { name: t('b22.object.save') }));
  }
  await waitFor(() => expect(asArray([...fake.drafts.values()][0]?.config.contracts)[0]).toMatchObject(action === 'rename' ? { contract_id: contract.contract_id, display_name: 'Synthetic renamed contract' } : { contract_id: contract.contract_id, enabled: false }));
  expect(fake.revisions).toHaveLength(1);
  expect(fake.revisions[0]!.config).toEqual(config());
});

function OpenDraft({ draft, children }: { draft: Draft; children: React.ReactNode }) {
  const doc = useWorkingDocument();
  useEffect(() => doc.open(draft), [doc.open, draft]);
  return doc.draft ? children : null;
}
function mountDraft(draft: Draft, child: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><WorkingDocumentProvider><OpenDraft draft={draft}>{child}</OpenDraft></WorkingDocumentProvider></MemoryRouter></QueryClientProvider>);
}
function DocumentProbe() {
  const doc = useWorkingDocument();
  return <output aria-label="Synthetic working configuration">{JSON.stringify(doc.config)}</output>;
}

it('changes only wake_settings through the dedicated action', async () => {
  const current = config();
  asObject(asArray(current.contracts)[0]).parameters = { unrelated: 42, wake_settings: { profiles: { weekday: { id: 'weekday', label: 'Synthetic weekday', wake_time: '08:00' }, weekend: { id: 'weekend', label: 'Synthetic weekend', wake_time: '09:00' } }, rules: [] } };
  const draft = makeDraft('synthetic-draft', current, { base_revision: 1 });
  const fake = installFakeApi({ revisions: [revision(current)], drafts: [draft] });
  const schema = declarativeParameterSchema({ parameter_schema: { wake_settings: { schema: { type: 'object', required: true }, structured_schema: 'wake_planning_v1', label: 'Weckplanung' } } }, caps.structured_parameter_schemas);
  mountDraft(draft as Draft, <WakePlanningDialog contractId={contract.contract_id} parameterSchema={schema} onClose={() => {}} />);
  const fields = await screen.findAllByLabelText(/Weckzeit/);
  fireEvent.change(fields[0]!, { target: { value: '07:30' } });
  await userEvent.click(screen.getByRole('button', { name: t('b22.object.save') }));
  await waitFor(() => expect(asObject(asObject(asObject(asObject(asArray(fake.drafts.get(draft.draft_id)!.config.contracts)[0]).parameters).wake_settings).profiles).weekday).toMatchObject({ wake_time: '07:30' }));
  const before = asObject(asArray(current.contracts)[0]); const after = asObject(asArray(fake.drafts.get(draft.draft_id)!.config.contracts)[0]);
  expect({ ...after, parameters: before.parameters }).toEqual(before);
  expect(asObject(after.parameters).unrelated).toBe(42);
  expect(fake.revisions[0]!.config).toEqual(current);
});

it('previews an entity repair through EntityPicker without changing logical IDs or active data', async () => {
  const current = config(); const draft = makeDraft('synthetic-draft', current, { base_revision: 1 });
  const fake = installFakeApi({ revisions: [revision(current)], drafts: [draft] });
  fake.routes.push({ match: /^ha\/entities\?/, respond: () => reply(200, { items: [{ entity_id: 'sensor.synthetic_new', display_name: 'Synthetic new device', attributes: [] }], next_cursor: null }) });
  mountDraft(draft as Draft, <><SourceMigrationDialog sourceId={source.source_id} bindingId={binding.binding_id} entityOnly onClose={() => {}} /><DocumentProbe /></>);
  await userEvent.click(await screen.findByRole('button', { name: t('b22.entity.choose') }));
  await userEvent.type(await screen.findByLabelText(t('editors.RegistryObjects.entitaten_durchsuchen_4437b8')), 'Synthetic new');
  await userEvent.click(await screen.findByRole('button', { name: /Synthetic new device/ }));
  await userEvent.click(screen.getByRole('button', { name: t('alpha7.move.check') }));
  await waitFor(() => expect(screen.getByRole('button', { name: t('b22.entity.apply') })).toBeEnabled());
  const request = fake.calls.find(call => call.path === 'registry/validate' && asArray(asObject(call.body as Json).bindings).some(row => asObject(asObject(row).adapter).ha_entity_id === 'sensor.synthetic_new'));
  expect(request).toBeDefined();
  const planned = planEntityChange(current, source.source_id, binding.binding_id, 'sensor.synthetic_new');
  expect(planned.contracts).toEqual(current.contracts); expect(planned.sources).toEqual(current.sources);
  expect(asArray(planned.bindings)[0]).toEqual({ ...binding, adapter: { ...binding.adapter, ha_entity_id: 'sensor.synthetic_new' } });
  await userEvent.click(screen.getByRole('button', { name: t('b22.entity.apply') }));
  await waitFor(() => expect(JSON.parse(screen.getByLabelText('Synthetic working configuration').textContent!)).toEqual(planned));
  expect(fake.drafts.get(draft.draft_id)!.config).toEqual(current);
  expect(fake.revisions[0]!.config).toEqual(current);
  expect(current).toEqual(config());
  expect(fake.revisions).toHaveLength(1);
});

it('keeps a new manual source read-only until its local edit action and never shows active runtime controls', async () => {
  const active = config();
  const manual = { ...source, source_id: 'source.synthetic_manual', display_name: 'Synthetic manual source', origin: 'manual' };
  const draft = makeDraft('synthetic-new-source', { ...active, sources: [...asArray(active.sources), manual] }, { base_revision: 1 });
  const fake = installFakeApi({ revisions: [revision(active)], drafts: [draft] });
  sessionStorage.setItem('terra.workingDraft', draft.draft_id);
  app('#/sources/devices?source_id=source.synthetic_manual');
  const field = await screen.findByLabelText('Name');
  expect(field).toBeDisabled();
  expect(fake.calls.some(call => /manual|calendar/.test(call.path))).toBe(false);
  await userEvent.click(screen.getByRole('button', { name: t('b22.object.edit') }));
  await waitFor(() => expect(field).toBeEnabled());
  expect(fake.revisions[0]!.config).toEqual(active);
});

it('places templates under contracts and each catalog under its own main navigation point', async () => {
  const fake = installFakeApi({ revisions: [revision()] });
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'synthetic.catalog', version: 1, display_name: 'Synthetic catalog', classification_schema: { values: ['normal'] } }]) });
  fake.routes.push({ match: /^editorial(?:\?|$)/, respond: () => reply(200, { items: [{ object: { kind: 'contract', id: contract.contract_id }, category: 'media', room: NO_ROOM, editorial_version: 1 }], draft_items: [] }) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [], content_revision: 0, next_cursor: null }) });
  app('#/contracts/list/all');
  expect(await screen.findByRole('link', { name: 'Medien (1)' })).toHaveAttribute('href', '#/contracts/list/all?category=media');
  expect(screen.queryByRole('button', { name: 'Vorlagen' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Eigene Vorlagen' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Katalogpflege' }));
  const target = await screen.findByRole('link', { name: 'Synthetic catalog' });
  await userEvent.click(target);
  await waitFor(() => expect(fake.calls.some(call => call.path.startsWith('catalogs/care?') && new URLSearchParams(call.path.split('?')[1]).get('catalog_id') === 'synthetic.catalog')).toBe(true));
});

it('labels an evidenced source problem as a source and links to that source', () => {
  const envelope = { contract_health: 'degraded', fields: { value: { reasons: [{ code: 'input_stale', source_id: source.source_id }] } } } as unknown as Envelope;
  wrap(<SourceProblems config={config()} rows={[{ stale: false, envelope }]} />);
  expect(screen.getByText('Quelle')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Synthetic source' })).toHaveAttribute('href', '/sources/devices?source_id=source.synthetic');
});
