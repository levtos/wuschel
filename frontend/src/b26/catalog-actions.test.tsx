import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { t } from '@/i18n';
import CatalogPage from '@/features/catalog/CatalogPage';
import { installFakeApi, reply } from '../../test/fakeApi';
import { idleLive, wrap } from '../../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
const entry = { identity: 'synthetic', key: 'Synthetic Title', scope: 'default', media_type: 'music', signal_type: 'title', classification: 'normal', effective_classification: 'normal', master_key: null, display_name: '', note: '', complete: true, semantic_digest: 'synthetic', variant_count: 0 };
function mount() {
  const api = installFakeApi();
  api.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'synthetic', version: 1, classification_schema: { values: ['normal'] } }]) });
  api.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [entry], content_revision: 1, next_cursor: null }) });
  api.routes.push({ match: /^catalogs\/entries\?/, respond: () => reply(200, { items: [], content_revision: 1, next_cursor: null }) });
  api.routes.push({ match: /^catalogs\/preview$/, respond: () => reply(200, { affected_keys: 1, affected_contracts: 2 }) });
  api.routes.push({ match: /^catalogs\/entries$/, respond: () => reply(200, { content_revision: 2 }) });
  wrap(<CatalogPage />, { live: idleLive() }); return api;
}
it.each([false, true])('confirms catalog display rename or deletion (%s) after the exact preview', async deleting => {
  const user = userEvent.setup(); const api = mount();
  const edit = (await screen.findAllByRole('button', { name: 'Bearbeiten' }))[0]!;
  await user.click(edit);
  const dialog = screen.getByRole('dialog', { name: 'Zuordnung bearbeiten' });
  if (deleting) await user.click(within(dialog).getByRole('button', { name: 'Eintrag löschen' }));
  else await user.type(within(dialog).getByLabelText('Anzeigename'), 'Neuer Titel');
  await user.click(within(dialog).getByRole('button', { name: 'Auswirkungen prüfen' }));
  const save = within(dialog).getByRole('button', { name: deleting ? 'Eintrag löschen' : t('alpha10.catalog.save') });
  await waitFor(() => expect(save).toBeEnabled()); await user.click(save);
  const confirmation = screen.getByRole('alertdialog');
  expect(confirmation).toHaveTextContent('Synthetic Title');
  expect(confirmation).toHaveTextContent(/2/);
  if (!deleting) expect(confirmation).toHaveTextContent('Neuer Titel');
  expect(api.calls.some(call => call.method === 'POST' && call.path === 'catalogs/entries')).toBe(false);
  await user.click(within(confirmation).getByRole('button', { name: deleting ? 'Eintrag löschen' : t('alpha10.catalog.save') }));
  await waitFor(() => expect(api.calls.some(call => call.method === 'POST' && call.path === 'catalogs/entries')).toBe(true));
  expect(api.calls.find(call => call.method === 'POST' && call.path === 'catalogs/entries')?.body).toMatchObject({ changes: [{ key: 'Synthetic Title', tombstone: deleting, display_name: deleting ? '' : 'Neuer Titel' }] });
});
