import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { t } from '@/i18n';
import CatalogPage from '@/features/catalog/CatalogPage';
import { installFakeApi, reply } from '../../test/fakeApi';
import { idleLive, wrap } from '../../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
const row = { identity: 'same-id', key: 'Synthetic Work', scope: 'default', media_type: 'music', signal_type: 'title', classification: 'boost', effective_classification: 'boost', master_key: null, display_name: '', note: '', complete: true, semantic_digest: 'synthetic', variant_count: 1, seen_count: 0, family_seen_count: 4, family_last_seen: '2026-01-01T12:00:00Z' };
const child = { ...row, identity: 'child', key: 'Synthetic Work (Remix)', master_identity: row.identity, master_key: row.key, classification: null, variant_count: 0, seen_count: 1 };
function mount() {
  const api = installFakeApi();
  api.routes.push({ match: /^catalogs$/, respond: () => reply(200, [
    { catalog_id: 'first', display_name: 'Erster Katalog', version: 1, classification_schema: { values: ['boost'], legend: { boost: { display_name: 'Boost', description: 'Synthetisch' } } } },
    { catalog_id: 'second', display_name: 'Zweiter Katalog', version: 2, classification_schema: { values: ['normal', 'boost'] } },
  ]) });
  api.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [{ ...row, catalog_id: 'first', definition_version: 1, content_revision: 3 }, { ...row, key: 'Synthetic Second', catalog_id: 'second', definition_version: 2, content_revision: 7 }], content_revision: 0, next_cursor: 'next-server-page' }) });
  api.routes.push({ match: /^catalogs\/care\/detail\?/, respond: () => reply(200, { variants: [child], next_cursor: null }) });
  api.routes.push({ match: /^catalogs\/entries\?/, respond: () => reply(200, { items: [], next_cursor: null }) });
  api.routes.push({ match: /^catalogs\/care\/preview$/, respond: () => reply(200, { affected_keys: 2, affected_contracts: 1, changes: [], context_effects: [] }) });
  api.routes.push({ match: /^catalogs\/care\/write$/, respond: () => reply(200, { content_revision: 4 }) });
  wrap(<CatalogPage />, { live: idleLive() }); return api;
}
it('defaults to every catalog with server paging, class grouping and independent row owners', async () => {
  const user = userEvent.setup(); const api = mount();
  let table = await screen.findByRole('table', { name: 'Katalogeinträge' });
  expect(screen.getByLabelText(t('alpha10.catalog.catalog'))).toHaveValue('');
  expect(within(table).getByRole('columnheader', { name: 'Katalog' })).toBeVisible();
  expect(table.querySelectorAll('tr[data-catalog-role="root"]')).toHaveLength(2);
  expect(within(table).getAllByText('4 Wiedergaben')).toHaveLength(2);
  expect(within(table).getAllByText('01.01.2026, 13:00')).toHaveLength(2);
  const initial = new URLSearchParams(api.calls.find(call => call.path.startsWith('catalogs/care?'))!.path.split('?')[1]);
  expect(initial.get('catalog_id')).toBe('all');
  await user.selectOptions(screen.getByLabelText(t('alpha10.catalog.class')), JSON.stringify(['first@1', 'boost']));
  await waitFor(() => {
    const params = new URLSearchParams(api.calls.filter(call => call.path.startsWith('catalogs/care?')).at(-1)!.path.split('?')[1]);
    expect(params.get('classification')).toBe('boost'); expect(params.get('classification_catalog')).toBe('first@1');
  });
  table = await screen.findByRole('table', { name: 'Katalogeinträge' });
  await user.click(within(table).getByRole('button', { name: 'Klasse' }));
  await waitFor(() => expect(api.calls.filter(call => call.path.startsWith('catalogs/care?')).at(-1)?.path).toContain('sort=classification'));
  table = await screen.findByRole('table', { name: 'Katalogeinträge' });
  const second = within(table).getByText('Synthetic Second').closest('tr')!;
  await user.click(within(second).getByRole('button', { name: 'Bearbeiten' }));
  await user.click(within(screen.getByRole('dialog', { name: 'Zuordnung bearbeiten' })).getByRole('button', { name: t('alpha10.catalog.preview') }));
  await waitFor(() => expect(api.calls.some(call => call.path === 'catalogs/preview')).toBe(true));
  expect(api.calls.find(call => call.path === 'catalogs/preview')?.body).toMatchObject({ catalog_id: 'second', definition_version: 2, expected_content_revision: 7 });
});
it('keeps variant columns aligned and promotes only through preview and explicit confirmation', async () => {
  const user = userEvent.setup(); const api = mount();
  const table = await screen.findByRole('table', { name: 'Katalogeinträge' });
  await user.click(within(table).getAllByRole('button', { name: 'Varianten · 1' })[0]!);
  const variant = (await screen.findByText('Synthetic Work (Remix)')).closest('tr')!;
  expect(variant.children).toHaveLength(table.querySelectorAll('thead th').length);
  expect(within(variant).getByText('1 Wiedergabe')).toBeVisible();
  await user.click(within(variant).getByRole('button', { name: 'Bearbeiten' }));
  const dialog = screen.getByRole('dialog', { name: 'Zuordnung bearbeiten' });
  await user.selectOptions(within(dialog).getByLabelText(t('alpha10.catalog.assignment')), 'promote');
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') }));
  const save = within(dialog).getByRole('button', { name: t('alpha10.catalog.save') });
  await waitFor(() => expect(save).toBeEnabled()); await user.click(save);
  const confirmation = screen.getByRole('alertdialog');
  expect(confirmation).toHaveTextContent('Alle bisherigen Varianten ziehen gemeinsam um');
  expect(api.calls.some(call => call.path === 'catalogs/care/write')).toBe(false);
  await user.click(within(confirmation).getByRole('button', { name: t('alpha10.catalog.save') }));
  await waitFor(() => expect(api.calls.some(call => call.path === 'catalogs/care/write')).toBe(true));
  expect(api.calls.find(call => call.path === 'catalogs/care/write')?.body).toMatchObject({ catalog_id: 'first', definition_version: 1, action: 'promote', identities: ['child'], classification: 'boost', expected_content_revision: 3 });
});
