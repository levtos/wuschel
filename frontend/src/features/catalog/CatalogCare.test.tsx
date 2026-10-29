import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { t } from '@/i18n';
import { CatalogCare } from './CatalogCare';
import type { Catalog, Entry } from './CatalogPage';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';

afterEach(() => vi.unstubAllGlobals());
const catalog: Catalog = { catalog_id: 'synthetic.music', version: 1, classification_schema: { values: ['normal', 'boost'], legend: { normal: { display_name: 'Normal', description: 'Synthetic' }, boost: { display_name: 'Bevorzugt', description: 'Synthetic' } } } };
const rows: Entry[] = ['one', 'two'].map(key => ({ identity: key, key: `Synthetic ${key}`, scope: 'synthetic', media_type: 'music', signal_type: 'title', classification: 'normal', master_key: null, display_name: '', note: '', effective_classification: 'normal', complete: true, semantic_digest: 'synthetic' }));
const impact = { affected_keys: 2, affected_contracts: 0, context_effects: [], changes: rows };
function mount() { wrap(<CatalogCare catalog={catalog} rows={rows} selected={Object.fromEntries(rows.map(row => [row.identity, row]))} setSelected={() => {}} revision={5} activeRevision={4} parameters="catalog_id=synthetic.music&definition_version=1" />); }

it('requires a matching preview for bulk class changes and sends one bounded write', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs\/care\/preview$/, respond: () => reply(200, impact) });
  fake.routes.push({ match: /^catalogs\/care\/write$/, respond: () => reply(200, { content_revision: 6 }) });
  mount(); const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Klasse setzen' }));
  const save = screen.getByRole('button', { name: t('alpha10.catalog.save') });
  expect(save).toBeDisabled();
  await user.click(screen.getByRole('button', { name: t('alpha10.catalog.preview') }));
  await waitFor(() => expect(save).toBeEnabled());
  await user.selectOptions(screen.getByLabelText(t('alpha10.catalog.class')), 'boost');
  expect(save).toBeDisabled();
  await user.click(screen.getByRole('button', { name: t('alpha10.catalog.preview') }));
  await waitFor(() => expect(save).toBeEnabled()); await user.click(save);
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'catalogs/care/write')).toHaveLength(1));
  expect(fake.calls.find(call => call.path === 'catalogs/care/write')?.body).toMatchObject({ identities: ['one', 'two'], classification: 'boost', expected_content_revision: 5, action: 'classify' });
});

it('keeps the current variant group after a conflict and never advances or retries automatically', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs\/care\/queue\?/, respond: () => reply(200, { groups: [{ members: rows, master: 'one' }, { members: rows, master: 'two' }], content_revision: 5, active_revision: 4 }) });
  fake.routes.push({ match: /^catalogs\/care\/preview$/, respond: () => reply(200, impact) });
  fake.routes.push({ match: /^catalogs\/care\/write$/, respond: () => reply(409, { error: 'catalog_content_conflict' }) });
  mount(); const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Varianten prüfen' }));
  await screen.findByText('Noch 2 Gruppen zu prüfen');
  await user.click(screen.getByRole('button', { name: t('alpha10.catalog.preview') }));
  const save = screen.getByRole('button', { name: t('alpha10.catalog.save') });
  await waitFor(() => expect(save).toBeEnabled()); await user.click(save);
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'catalogs/care/write')).toHaveLength(1));
  expect(screen.getByText('Noch 2 Gruppen zu prüfen')).toBeInTheDocument();
  expect(screen.getByLabelText(t('alpha10.catalog.master'))).toHaveValue('one');
  await user.click(screen.getByRole('button', { name: 'Überspringen' }));
  expect(screen.getByText('Noch 1 Gruppen zu prüfen')).toBeInTheDocument();
});
