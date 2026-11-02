import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { t } from '@/i18n';
import { CatalogCare } from './CatalogCare';
import CatalogPage, { type Catalog, type Entry } from './CatalogPage';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';

afterEach(() => vi.unstubAllGlobals());
const catalog: Catalog = { catalog_id: 'synthetic.catalog', version: 1, classification_schema: { values: ['normal', 'boost'] } };
const members: Entry[] = ['one', 'two'].map(id => ({ identity: id, key: `Synthetic ${id}`, display_name: '', scope: 'synthetic', media_type: 'synthetic', signal_type: 'synthetic', classification: 'normal', master_key: null, note: '', complete: true, effective_classification: 'normal', semantic_digest: id, review_status: 'reviewed' }));
const master: Entry = { ...members[0]!, identity: 'master', key: 'Synthetic master', variant_count: 2 };
const variants: Entry[] = members.map(row => ({ ...row, classification: null, master_key: master.key, master_identity: master.identity, effective_classification: 'boost' }));

function mountCare() {
  wrap(<CatalogCare catalog={catalog} rows={members} selected={Object.fromEntries(members.map(row => [row.identity, row]))} setSelected={() => {}} revision={5} activeRevision={4} parameters="catalog_id=synthetic.catalog" />);
}

it('previews a freely named master and saves the matching request only on confirmation', async () => {
  const fake = installFakeApi();
  const pending: { body: unknown; resolve: (response: Response) => void }[] = [];
  fake.routes.push({ match: /^catalogs\/care\/preview$/, respond: (_path, body) => body.new_master_name ? new Promise(resolve => pending.push({ body, resolve })) : reply(200, { affected_keys: 2, affected_contracts: 0, context_effects: [], changes: members }) });
  fake.routes.push({ match: /^catalogs\/care\/write$/, respond: () => reply(200, { content_revision: 6 }) });
  mountCare();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: t('b17.action.group') }));
  await user.selectOptions(screen.getByLabelText('Master'), '__new_master__');
  expect(screen.getByRole('button', { name: t('alpha10.catalog.preview') })).toBeDisabled();
  await user.type(screen.getByLabelText(t('b22.catalog.masterName')), 'Synthetic new master');
  await waitFor(() => expect(pending.at(-1)?.body).toMatchObject({ new_master_name: 'Synthetic new master' }));
  const preview = pending.at(-1)!;
  expect(preview.body).toMatchObject({ new_master_name: 'Synthetic new master', identities: ['one', 'two'], action: 'group', expected_content_revision: 5 });
  expect(preview.body).not.toHaveProperty('master');
  expect(screen.getByRole('button', { name: t('alpha10.catalog.save') })).toBeDisabled();
  expect(fake.calls.some(call => call.path === 'catalogs/care/write')).toBe(false);
  await act(async () => preview.resolve(reply(200, { affected_keys: 3, affected_contracts: 0, context_effects: [], changes: [master, ...variants] })));
  const save = screen.getByRole('button', { name: t('alpha10.catalog.save') });
  await waitFor(() => expect(save).toBeEnabled());
  await act(async () => { for (const old of pending.filter(item => item !== preview)) old.resolve(reply(400, { error: 'catalog_master_name_conflict' })); });
  expect(save).toBeEnabled();
  await user.click(save);
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'catalogs/care/write')).toHaveLength(1));
  expect(fake.calls.find(call => call.path === 'catalogs/care/write')!.body).toEqual(preview.body);
});

it('keeps a free master name and selection after a collision, without writing', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs\/care\/preview$/, respond: () => reply(400, { error: 'catalog_master_name_conflict' }) });
  mountCare();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: t('b17.action.group') }));
  await user.selectOptions(screen.getByLabelText('Master'), '__new_master__');
  await user.type(screen.getByLabelText(t('b22.catalog.masterName')), 'Synthetic duplicate');
  await screen.findByText(t('codes.catalog_master_name_conflict'));
  expect(screen.getByLabelText(t('b22.catalog.masterName'))).toHaveValue('Synthetic duplicate');
  expect(screen.getByRole('button', { name: t('alpha10.catalog.save') })).toBeDisabled();
  expect(fake.calls.some(call => call.path === 'catalogs/care/write')).toBe(false);
});

function installPage(parent: Entry = master) {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [catalog]) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [parent], content_revision: 5, next_cursor: null }) });
  fake.routes.push({ match: /^catalogs\/care\/detail\?/, respond: () => reply(200, { entry: parent, variants, next_cursor: null, contexts: [] }) });
  wrap(<CatalogPage />);
  return fake;
}

it('renders labelled variants as nested children, with selection and edit actions', async () => {
  const fake = installPage();
  const list = await screen.findByRole('list', { name: t('b22.catalog.entryList') });
  expect(list.querySelectorAll(':scope > li')).toHaveLength(1);
  expect(within(list).getByText('Master')).toBeInTheDocument();
  expect(fake.calls.some(call => call.path.startsWith('catalogs/care/detail?'))).toBe(false);
  await userEvent.click(within(list).getByText('Varianten · 2'));
  await within(list).findByText('Synthetic two');
  const nested = within(list).getByRole('list', { name: 'Varianten' });
  expect(nested.closest('li')).toHaveAttribute('data-catalog-role', 'root');
  expect(within(nested).getAllByText('Variante')).toHaveLength(2);
  expect(within(nested).getAllByText(t('b22.catalog.inheritedClass'))).toHaveLength(2);
  expect(within(nested).getAllByRole('checkbox')).toHaveLength(2);
  expect(within(nested).getAllByRole('button', { name: t('alpha10.catalog.edit') })).toHaveLength(2);
  expect(list.querySelectorAll(':scope > li')).toHaveLength(1);
  expect(new URLSearchParams(fake.calls.find(call => call.path.startsWith('catalogs/care?'))!.path.split('?')[1]).get('grouped')).toBe('true');
});

it('automatically opens the master context for a variant-only search match', async () => {
  const fake = installPage({ ...master, matches_filter: false, matching_variant_count: 1 });
  await screen.findByText('Synthetic two');
  expect(screen.getByText(t('b22.catalog.variantMatch', { n: 1 }))).toBeInTheDocument();
  expect(fake.calls.filter(call => call.path.startsWith('catalogs/care/detail?'))).toHaveLength(1);
});
