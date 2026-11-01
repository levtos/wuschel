import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { t } from '@/i18n';
import { CatalogCleanup } from './CatalogCleanup';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';

afterEach(() => vi.unstubAllGlobals());
it('offers filtered bulk selection for unclear rows but never selects protected rows and requires confirmation', async () => {
  const fake = installFakeApi();
  const rows = [
    { identity: 'unclear', key: 'Synthetic unclear title', kind: 'unclear', protected: false, first_seen: '2026-01-01T12:00:00Z', seen_count: 1, source: 'synthetic' },
    { identity: 'protected', key: 'Synthetic protected title', kind: 'protected', protected: true, first_seen: '2026-01-01T12:00:00Z', seen_count: 1, source: 'synthetic' },
  ];
  fake.routes.push({ match: /^catalogs\/cleanup\/preview$/, method: 'POST', respond: (_path, body) => reply(200, {
    plan_id: 'synthetic-plan', plan_digest: body.select_filtered ? 'selected-digest' : 'empty-digest',
    items: rows, counts: { unclear: 1, protected: 1 }, total: 2, filtered: 2,
    selected: body.select_filtered ? ['unclear'] : body.selected ?? [], reset_counters: true,
    next_offset: null, counters_before: 2, catalog_count: 7, sources: ['synthetic'],
  }) });
  fake.routes.push({ match: /^catalogs\/cleanup\/apply$/, method: 'POST', respond: () => reply(200, { removed: 1, reset_counters: true }) });
  wrap(<CatalogCleanup catalog={{ catalog_id: 'synthetic', version: 1, classification_schema: { values: ['normal'] } }} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: t('b20.cleanup.title') }));
  const dialog = await screen.findByRole('dialog');
  const protectedBox = within(dialog).getByRole('checkbox', { name: /Synthetic protected title/ });
  expect(protectedBox).toBeDisabled(); expect(protectedBox).not.toBeChecked();
  expect(within(dialog).getByRole('checkbox', { name: /Synthetic unclear title/ })).not.toBeChecked();
  const apply = within(dialog).getByRole('button', { name: t('b20.cleanup.apply') });
  expect(apply).toBeDisabled();
  await user.selectOptions(within(dialog).getByLabelText(t('b20.cleanup.kind')), 'unclear');
  await user.click(within(dialog).getByRole('checkbox', { name: t('b20.cleanup.one') }));
  await user.click(within(dialog).getByRole('button', { name: t('b20.cleanup.selectFiltered') }));
  await waitFor(() => expect(within(dialog).getByRole('checkbox', { name: /Synthetic unclear title/ })).toBeChecked());
  expect(protectedBox).not.toBeChecked(); expect(apply).toBeDisabled();
  const previewCalls = fake.calls.filter(call => call.path === 'catalogs/cleanup/preview');
  expect(previewCalls.at(-1)?.body).toMatchObject({ select_filtered: true, filters: { kind: 'unclear', one: true, unmaintained: true } });
  expect(fake.calls.some(call => call.path === 'catalogs/cleanup/apply')).toBe(false);
  await user.click(within(dialog).getByRole('checkbox', { name: t('b20.cleanup.confirm', { n: 1 }) }));
  await waitFor(() => expect(apply).toBeEnabled()); await user.click(apply);
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'catalogs/cleanup/apply')).toHaveLength(1));
  expect(fake.calls.find(call => call.path === 'catalogs/cleanup/apply')?.body).toMatchObject({ plan_digest: 'selected-digest', confirmed: true });
});
