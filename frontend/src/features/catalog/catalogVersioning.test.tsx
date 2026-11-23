import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { t } from '@/i18n';
import { WorkingDocumentProvider } from '@/features/authoring/WorkingDocument';
import { installFakeApi, reply, sampleConfig } from '../../../test/fakeApi';
import { idleLive, wrap } from '../../../test/harness';
import CatalogPage from './CatalogPage';
import { CatalogVersions } from './CatalogVersions';
import { catalogUsage, switchCatalogConsumers } from './catalogVersioning';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

const managed = { catalog_id: 'test.catalog', kind: 'managed', version: 1, classification_schema: { values: ['normal'] }, unassigned_value: 'normal' };
const output = { op: 'managed_catalog_lookup', catalog_id: managed.catalog_id, definition_version: 1 };
const definition = { type_id: 'test.play', type_version: 1, display_name: 'Synthetische Wiedergabe', outputs: { value: output } };
const config = sampleConfig({
  catalogs: [managed, { ...managed, version: 2 }],
  definitions: [definition],
  contracts: [
    { contract_id: 'test.first', type_id: definition.type_id, type_version: 1, display_name: 'Synthetischer Verbraucher' },
    { contract_id: 'test.second', type_id: definition.type_id, type_version: 1 },
    { contract_id: 'test.downstream', inputs: [{ ref: 'test.first.value' }], display_name: 'Nachgelagerte Regel' },
  ],
});

it('previews and moves only chosen consumers to a new, versioned lookup', () => {
  expect(catalogUsage(config, managed.catalog_id, 1)).toEqual({ definitions: [definition], direct: ['test.first', 'test.second'], indirect: ['test.downstream'] });
  const result = switchCatalogConsumers(config, managed.catalog_id, 1, 2, ['test.first'], []);
  expect(result.definitions).toEqual([definition, { ...definition, type_version: 2, outputs: { value: { ...output, definition_version: 2 } } }]);
  expect(result.contracts).toEqual([{ ...(config.contracts as object[])[0], type_version: 2 }, ...(config.contracts as object[]).slice(1)]);
  expect(output.definition_version).toBe(1);
});

it('creates an independent catalog definition before offering a separate consumer switch', async () => {
  const api = installFakeApi({ revisions: [{ revision: 1, config, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  wrap(<WorkingDocumentProvider><CatalogVersions id={managed.catalog_id} version={1} /></WorkingDocumentProvider>, { live: idleLive() });
  const user = userEvent.setup();
  expect(await screen.findByRole('link', { name: 'Synthetische Wiedergabe · Version 1' })).toHaveAttribute('href', '/templates/details/test.play/1?tab=rules');
  expect(screen.getByRole('link', { name: 'Nachgelagerte Regel' })).toHaveAttribute('href', '/contracts/view/test.downstream');
  await user.click(screen.getByRole('button', { name: 'Neue Katalogversion' }));
  const creating = within(screen.getByRole('dialog', { name: 'Neue Katalogversion' }));
  expect(creating.getByText('test.catalog · Version 3')).toBeVisible();
  await user.click(creating.getByRole('button', { name: 'In Entwurf speichern' }));
  const moving = within(await screen.findByRole('dialog', { name: 'Verbraucher umstellen' }));
  expect(moving.getByRole('checkbox', { name: 'Synthetischer Verbraucher' })).not.toBeChecked();
  await waitFor(() => expect([...api.drafts.values()][0]?.config.catalogs).toEqual([managed, { ...managed, version: 2 }, { ...managed, version: 3 }]));
  expect([...api.drafts.values()][0]?.config.contracts).toEqual(config.contracts);
});

it('keeps PS5 entries independent, limits editing and discards the old selection when filtering', async () => {
  const api = installFakeApi();
  const catalog = { catalog_id: 'media.ps5', version: 1, classification_schema: { values: ['normal'] } };
  const entry = (name: string) => ({
    catalog_id: catalog.catalog_id, definition_version: 1, identity: name,
    key: name, display_name: name, note: '', master_key: null, classification: null, effective_classification: 'normal',
    scope: 'default', media_type: 'game', signal_type: 'title', review_status: 'open', complete: false, semantic_digest: name,
  });
  const records = [entry('Synthetisch Eins'), entry('Synthetisch Zwei')];
  api.routes.push({ match: /^catalogs$/, respond: () => reply(200, [catalog]) });
  api.routes.push({ match: /^catalogs\/care\?/, respond: path => reply(200, {
    items: records.filter(row => row.key.includes(new URLSearchParams(path.split('?')[1]).get('q') ?? '')),
    content_revision: 3, next_cursor: null,
  }) });
  api.routes.push({ match: /^catalogs\/care\/preview$/, respond: () => reply(200, { affected_keys: 0, affected_contracts: 0, changes: [], context_effects: [] }) });
  wrap(<CatalogPage />, { live: idleLive() });
  const user = userEvent.setup();
  const table = within(await screen.findByRole('table', { name: t('b22.catalog.entryList') }));
  for (const row of records) await user.click(table.getByRole('checkbox', { name: t('b17.selectTitle', { title: row.key }) }));
  expect(table.getAllByRole('button', { name: 'Bearbeiten' }).every(button => button.hasAttribute('disabled'))).toBe(true);
  expect(screen.getByText(t('a2.care.gameEditHelp'))).toBeVisible();
  await user.type(screen.getByLabelText(t('alpha10.catalog.search')), 'Zwei');
  const remaining = await screen.findByRole('checkbox', { name: t('b17.selectTitle', { title: 'Synthetisch Zwei' }) });
  expect(remaining).not.toBeChecked();
  await user.click(remaining);
  await user.click(screen.getByRole('button', { name: t('b17.action.classify') }));
  const dialog = within(screen.getByRole('dialog', { name: t('b17.bulk') }));
  expect(dialog.getByText('Synthetisch Zwei')).toBeVisible();
  expect(dialog.queryByText('Synthetisch Eins')).not.toBeInTheDocument();
});
