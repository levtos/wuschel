import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';
import { CatalogScopeRepair } from './CatalogScopeRepair';
import CatalogPage from './CatalogPage';

afterEach(() => vi.unstubAllGlobals());

const plan = { complete: false, plan_digest: 'synthetic', clear_pc: true, catalogs: [{ catalog_id: 'media.homepods', definition_version: 1, moved: 3, collisions: 2, removed: 0 }] };

it('summarizes resolutions, expands readable per-entry reasons and applies the exact preview digest', async () => {
  const fake = installFakeApi();
  const resolved = { ...plan, plan_digest: 'resolved-plan', catalogs: [{ ...plan.catalogs[0]!, conflicts: 3, findings: [
    { identity: 'one', key: '["Synthetic Artist","Synthetic Variant"]', display_name: '', reason: 'live_master', master_before: 'Synthetic Master A', master_after: 'Synthetic Master B' },
    { identity: 'two', key: 'Synthetic Class Conflict', display_name: '', reason: 'live_class_standalone', master_before: 'Synthetic Master A', master_after: null },
    { identity: 'three', key: 'Synthetic Orphan', display_name: '', reason: 'missing_master_standalone', master_before: 'Synthetic Missing', master_after: null },
  ] }] };
  fake.routes.push({ match: /^catalogs\/scope\/preview$/, respond: () => reply(200, resolved) });
  fake.routes.push({ match: /^catalogs\/scope\/apply$/, respond: () => reply(200, { ...resolved, complete: true }) });
  wrap(<CatalogScopeRepair activeRevision={4} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Katalogbereiche korrigieren' }));
  const summary = await screen.findByText('Auflösungen (3)');
  expect(screen.getByText('Synthetic Artist – Synthetic Variant')).not.toBeVisible();
  expect(fake.calls.some(call => call.path.endsWith('/apply'))).toBe(false);
  await user.click(summary);
  expect(screen.getByText('Synthetic Artist – Synthetic Variant')).toBeVisible();
  expect(screen.getByText('Der vorhandene Live-Masterbezug bleibt erhalten.')).toBeVisible();
  expect(screen.getByText(/Die abweichende Live-Klasse bleibt als selbständige Zuordnung erhalten/)).toBeVisible();
  expect(screen.getByText(/Der Master fehlt/)).toBeVisible();
  expect(screen.getByText('Master nach der Korrektur: Synthetic Master B')).toBeVisible();
  expect(screen.getByRole('dialog').textContent).not.toContain('["');
  await user.click(screen.getByRole('button', { name: 'Bestätigen und ausführen' }));
  await screen.findByText(/Die einmalige Korrektur ist abgeschlossen/);
  expect(fake.calls.find(call => call.path.endsWith('/apply'))?.body).toMatchObject({ plan_digest: 'resolved-plan', confirmed: true });
});

it('previews totals with PC enabled, invalidates an option change and needs one explicit apply', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs\/scope\/preview$/, respond: (_, body) => reply(200, { ...plan, clear_pc: body.clear_pc }) });
  fake.routes.push({ match: /^catalogs\/scope\/apply$/, respond: () => reply(200, { ...plan, complete: true }) });
  wrap(<CatalogScopeRepair activeRevision={4} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Katalogbereiche korrigieren' }));
  const dialog = within(await screen.findByRole('dialog'));
  const confirm = dialog.getByRole('button', { name: 'Bestätigen und ausführen' });
  await dialog.findByText('HomePods: 3 verschieben, 2 zusammenführen, 0 entfernen');
  expect(dialog.getByRole('checkbox', { name: 'PC-Katalog vollständig leeren' })).toBeChecked();
  expect(fake.calls.some(call => call.path.endsWith('/apply'))).toBe(false);
  await user.click(dialog.getByRole('checkbox'));
  await waitFor(() => expect(confirm).toBeEnabled());
  await user.click(confirm);
  await dialog.findByText(/Die einmalige Korrektur ist abgeschlossen/);
  expect(confirm).toBeDisabled();
  expect(fake.calls.filter(call => call.path.endsWith('/apply'))).toHaveLength(1);
  expect(fake.calls.find(call => call.path.endsWith('/apply'))?.body).toMatchObject({ expected_active_revision: 4, clear_pc: false, confirmed: true, plan_digest: 'synthetic' });
});

it('starts with all titles and labels open assigned, reviewed, hidden and missing classes without raw names', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'media.tv_device', version: 1, classification_schema: { values: ['normal'], legend: { normal: { display_name: 'Normal', description: 'Synthetic' } } } }]) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, {
    items: ['open', 'reviewed', 'hidden', 'open'].map((review_status, index) => ({ identity: String(index), key: `Synthetic ${index}`, display_name: '', scope: 'default', media_type: 'source', signal_type: 'title', classification: index === 3 ? null : 'normal', master_key: null, note: '', effective_classification: 'normal', complete: index !== 3, review_status })),
    content_revision: 1, next_cursor: null, contexts: ['["tv",""]', '["synthetic.player",""]'], context_targets: { '["synthetic.player",""]': { catalog_id: 'media.homepods', scope: 'homepods' } }, filter_values: { scope: ['default'], media_type: ['source'], signal_type: ['title'] },
  }) });
  wrap(<CatalogPage />);
  await screen.findByText('Synthetic 0');
  expect(screen.getByLabelText('Ansicht')).toHaveValue('all');
  expect(screen.getAllByText('Offen').length).toBeGreaterThan(1);
  expect(screen.getAllByText('Erledigt').length).toBeGreaterThan(1);
  expect(screen.getAllByText('Ausgeblendet').length).toBeGreaterThan(1);
  expect(screen.getByText('Ohne Klasse')).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'TV-Gerät' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Fernseher' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'HomePods' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Bildquelle' })).toBeInTheDocument();
  const copy = document.body.cloneNode(true) as HTMLElement;
  copy.querySelectorAll('details').forEach(node => node.remove());
  expect(copy.textContent).not.toMatch(/media\.tv_device|synthetic\.player|\["|\bdefault\b|\bsource\b|\btitle\b/);
});
