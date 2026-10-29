import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { t } from '@/i18n';
import { CatalogCare, CatalogDetail } from './CatalogCare';
import CatalogPage from './CatalogPage';
import type { Catalog, Entry } from './CatalogPage';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';

afterEach(() => vi.unstubAllGlobals());
const catalog: Catalog = { catalog_id: 'synthetic.music', version: 1, classification_schema: { values: ['normal', 'boost'], legend: { normal: { display_name: 'Normal', description: 'Synthetic' }, boost: { display_name: 'Bevorzugt', description: 'Synthetic' } } } };
const rows: Entry[] = ['one', 'two'].map(key => ({ identity: key, key: `Synthetic ${key}`, scope: 'synthetic', media_type: 'music', signal_type: 'title', classification: 'normal', master_key: null, display_name: '', note: '', effective_classification: 'normal', complete: true, semantic_digest: 'synthetic' }));
const impact = { affected_keys: 2, affected_contracts: 0, context_effects: [], changes: rows };
function mount() { wrap(<CatalogCare catalog={catalog} rows={rows} selected={Object.fromEntries(rows.map(row => [row.identity, row]))} setSelected={() => {}} revision={5} activeRevision={4} parameters="catalog_id=synthetic.music&definition_version=1" />); }

const compoundRows: Entry[] = [
  { ...rows[0]!, key: '["Synthetic Ensemble","Synthetic Beacon"]', identity: 'synthetic.original' },
  { ...rows[0]!, key: '["Synthetic Ensemble","Synthetic Beacon (Remix)"]', identity: 'synthetic.remix' },
  { ...rows[0]!, key: '["Synthetic Solo"]', identity: 'synthetic.solo' },
];

it('labels local variant suggestions and previews readably while submitting original identities', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs\/care\/queue\?/, respond: () => reply(200, { groups: [{ members: compoundRows.slice(0, 2), master: compoundRows[0]!.identity }], content_revision: 5, active_revision: 4 }) });
  fake.routes.push({ match: /^catalogs\/care\/preview$/, respond: () => reply(200, { ...impact, changes: compoundRows }) });
  wrap(<CatalogCare catalog={catalog} rows={compoundRows} selected={{}} setSelected={() => {}} revision={5} activeRevision={4} parameters="catalog_id=synthetic.music&definition_version=1" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Varianten prüfen' }));
  const dialog = await screen.findByRole('dialog');
  const master = within(dialog).getByRole('option', { name: 'Synthetic Ensemble – Synthetic Beacon' });
  expect(master).toHaveValue('synthetic.original');
  expect(within(dialog).getByRole('option', { name: 'Synthetic Ensemble – Synthetic Beacon (Remix)' })).toHaveValue('synthetic.remix');
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') }));
  await within(dialog).findByText('Synthetic Solo · Normal');
  expect(dialog.textContent).not.toContain('["');
  expect(fake.calls.find(call => call.path === 'catalogs/care/preview')?.body).toMatchObject({ identities: ['synthetic.original', 'synthetic.remix'], master: 'synthetic.original' });
  expect(fake.calls.some(call => call.path === 'catalogs/care/write')).toBe(false);
});

it('formats local catalog rows, accessible labels and edit master options without changing key values', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [catalog]) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: compoundRows, content_revision: 5, next_cursor: null }) });
  fake.routes.push({ match: /^catalogs\/entries\?/, respond: () => reply(200, { items: compoundRows, content_revision: 5, next_cursor: null }) });
  wrap(<CatalogPage />);
  const select = await screen.findByRole('checkbox', { name: t('b17.selectTitle', { title: 'Synthetic Ensemble – Synthetic Beacon' }) });
  expect(screen.getByText('Synthetic Solo')).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(within(select.closest('li')!).getByRole('button', { name: t('alpha10.catalog.edit') }));
  await user.selectOptions(screen.getByLabelText(t('alpha10.catalog.assignment')), 'variant');
  const option = await screen.findByRole('option', { name: 'Synthetic Ensemble – Synthetic Beacon (Remix)' });
  expect(option).toHaveValue(compoundRows[1]!.key);
  expect(document.body.textContent).not.toContain('["');
});

it('formats detail titles, master and variants without TC metadata', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs\/care\/detail\?/, respond: () => reply(200, { entry: compoundRows[1], master: compoundRows[0], variants: [compoundRows[2]], contexts: [], next_cursor: null }) });
  wrap(<CatalogDetail catalog={catalog} entry={compoundRows[1]!} onClose={() => {}} onOpen={() => {}} />);
  await screen.findByRole('heading', { name: 'Synthetic Ensemble – Synthetic Beacon (Remix)' });
  expect(screen.getByRole('button', { name: `${t('alpha10.catalog.master')}: Synthetic Ensemble – Synthetic Beacon` })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Synthetic Solo' })).toBeInTheDocument();
  expect(screen.getByRole('dialog').textContent).not.toContain('["');
});

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
