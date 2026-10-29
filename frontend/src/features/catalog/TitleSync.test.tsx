import { act, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { TitleSync } from './TitleSync';
import { idleLive, wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';
import { emptyRegistry } from '@/features/authoring/model';
import { t } from '@/i18n';
import { Workbench } from '@/Workbench';

afterEach(() => vi.unstubAllGlobals());
const mapping = { mappings: { '["synthetic",""]': { catalog_id: 'synthetic.music', definition_version: 1, scope: 'synthetic', profile: 'music' } }, standalone: [] };
const preview = { plan_id: 'synthetic-plan', plan_digest: 'synthetic-digest', at: '2032-01-01T00:00:00Z',
  counts: { new: 1 }, can_apply: true, mapping, mapping_version: 1, changed_targets: 1,
  page: 1, page_count: 1, page_source_rows: 1, error_groups: [] as { context: string; reason: string; count: number; details: { row: number; key?: string; reasons: string[] }[] }[],
  contexts: [{ key: '["synthetic",""]', context: 'synthetic', source_app: '' }], errors: [],
  affected_contracts: [], context_effects: [] };
const item = { catalog_id: 'synthetic.music', old_class: '–', new_class: 'Normal', change: 'new', after: {
  key: 'synthetic', display_name: 'Synthetic Track', reviewed_at: preview.at,
  tc_metadata: { artist: 'Synthetic Artist', title: 'Synthetic Track', telemetry: { seen_count: 40, first_seen: '2026-10-26T15:10:00Z', last_seen: '2026-10-26T16:10:00Z' } },
} };
function pages(fake: ReturnType<typeof installFakeApi>, page = { ...preview, items: [item] }) {
  fake.routes.push({ match: /^title-sync\/page/, respond: () => reply(200, page) });
  fake.routes.push({ match: /^title-sync\/cancel$/, respond: () => reply(200, { cancelled: true }) });
}

it('reconciles again without reentering a saved mapping and applies only the approved plan', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, preview) });
  pages(fake);
  fake.routes.push({ match: /^title-sync\/apply$/, respond: () => reply(200, { changed: 1, complete: true, page: 1 }) });
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup();
  const button = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(button).toBeEnabled()); await user.click(button);
  const apply = await screen.findByRole('button', { name: 'Diese Seite übernehmen' });
  await waitFor(() => expect(apply).toBeEnabled());
  const firstBody = fake.calls.find(call => call.path === 'title-sync/preview')?.body;
  expect(firstBody).toEqual({ expected_active_revision: 4, run_id: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/) });
  await user.click(apply);
  await screen.findByText(t('b18.finished'));
  expect(fake.calls.find(call => call.path === 'title-sync/apply')?.body).toMatchObject({ plan_id: preview.plan_id, plan_digest: preview.plan_digest });
  await user.click(screen.getByRole('button', { name: 'Neu abgleichen' }));
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'title-sync/preview')).toHaveLength(2));
  expect(fake.calls.filter(call => call.path === 'title-sync/preview')[1]?.body).not.toHaveProperty('mapping');
  expect(fake.calls.filter(call => call.path === 'title-sync/preview')[1]?.body).not.toEqual(firstBody);
});

it('blocks apply after deliberate mapping edits until a new preview binds the mapping version', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, preview) });
  pages(fake);
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup(); const button = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(button).toBeEnabled()); await user.click(button);
  const apply = await screen.findByRole('button', { name: 'Diese Seite übernehmen' });
  await waitFor(() => expect(apply).toBeEnabled());
  await user.click(screen.getByText('Kontextabbildung pflegen'));
  const scope = screen.getByLabelText('Geltungsbereich');
  await user.clear(scope); await user.type(scope, 'synthetic-new');
  expect(apply).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Geänderte Abbildung prüfen' }));
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'title-sync/preview')).toHaveLength(2));
  expect(fake.calls.filter(call => call.path === 'title-sync/preview')[1]?.body).toMatchObject({ plan_id: preview.plan_id, expected_mapping_version: 1, mapping: { mappings: { '["synthetic",""]': { scope: 'synthetic-new' } } } });
});

it('shows a wide ordered page table with German dates and counts, filters only its view and works without randomUUID', async () => {
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, preview) });
  const second = { ...item, catalog_id: 'synthetic.second', change: 'changed', after: { ...item.after, display_name: 'Synthetic Second', tc_metadata: { ...item.after.tc_metadata, title: 'Synthetic Second' } } };
  pages(fake, { ...preview, items: [item, second] });
  wrap(<><div id="title-sync-panel" /><TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }, { catalog_id: 'synthetic.second', version: 1 }]} /></>);
  const user = userEvent.setup(); const start = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(start).toBeEnabled()); await user.click(start);
  const table = await screen.findByRole('table');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.getElementById('title-sync-panel')).toContainElement(table);
  expect(within(table).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['Gerät', 'Interpret', 'Titel', 'Klasse (alt → neu)', 'Master', 'Status', 'Sichtungen', 'Erstmals gesehen', 'Zuletzt gesehen', 'Art der Änderung']);
  expect(within(table).getAllByText('40 Sichtungen')).toHaveLength(2);
  expect(within(table).getAllByText('26.10.2026, 16:10')).toHaveLength(2);
  expect(within(table).getAllByText('Erledigt')).toHaveLength(2);
  expect(screen.getByText('Seite 1 von 1')).toBeVisible();
  await user.selectOptions(screen.getByRole('combobox', { name: 'Art der Änderung' }), 'new');
  expect(within(table).queryByText('Synthetic Second')).not.toBeInTheDocument();
  await user.selectOptions(screen.getByRole('combobox', { name: 'Art der Änderung' }), '');
  await user.selectOptions(screen.getByRole('combobox', { name: 'Gerät' }), 'synthetic.second');
  expect(within(table).queryByText('Synthetic Track')).not.toBeInTheDocument();
  expect(within(table).getByText('Synthetic Second')).toBeVisible();
  expect(fake.calls.some(call => call.path === 'title-sync/apply')).toBe(false);
});

it('puts grouped context errors below the mapping and only expands individual details deliberately', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  const plan = { ...preview, can_apply: false, error_groups: [{ context: '["synthetic",""]', reason: 'context_unmapped', count: 3209, details: [{ row: 1, key: 'Synthetic Detail', reasons: ['context_unmapped'] }] }] };
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, plan) });
  pages(fake, { ...plan, items: [] });
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup(); const start = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(start).toBeEnabled()); await user.click(start);
  const findings = await screen.findByRole('region', { name: 'Nicht zuordenbare Titel' });
  const mapping = screen.getByText('Kontextabbildung pflegen');
  expect(mapping.compareDocumentPosition(findings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByText('Synthetic Detail')).not.toBeInTheDocument();
  await user.click(within(findings).getByText(/3209 Einträge/));
  expect(await screen.findByText('Synthetic Detail')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Diese Seite übernehmen' })).toBeDisabled();
});

it('shows errors and loading beside the actions and cancels a busy run without silently applying another page', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, preview) }); pages(fake);
  let finish!: (value: Response) => void;
  fake.routes.push({ match: /^title-sync\/apply$/, respond: () => new Promise(resolve => { finish = resolve; }) });
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup(); const start = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(start).toBeEnabled()); await user.click(start);
  const apply = await screen.findByRole('button', { name: 'Diese Seite übernehmen' });
  await waitFor(() => expect(apply).toBeEnabled()); await user.click(apply);
  const loading = await within(apply.closest('.sticky') as HTMLElement).findByRole('status');
  expect(apply.closest('.sticky')).toContainElement(loading);
  const abort = screen.getByRole('button', { name: 'Abbrechen' }); expect(abort).toBeEnabled();
  await user.click(abort);
  await waitFor(() => expect(fake.calls.some(call => call.path === 'title-sync/cancel')).toBe(true));
  await act(async () => finish(reply(200, { changed: 1, complete: false, page: 1 })));
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(fake.calls.filter(call => call.path === 'title-sync/apply')).toHaveLength(1);
  expect(fake.activations).toHaveLength(0);
});

it('keeps a failed page and its request ID until manual retry, then shows the next page without applying it', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  let attempts = 0;
  const current = { ...preview, page_count: 2 };
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, current) });
  fake.routes.push({ match: /^title-sync\/page/, respond: () => reply(200, { ...current, page: attempts > 1 ? 2 : 1, plan_digest: attempts > 1 ? 'second-page' : preview.plan_digest, items: [item] }) });
  fake.routes.push({ match: /^title-sync\/apply$/, respond: () => ++attempts === 1 ? reply(409, { error: 'catalog_content_conflict' }) : reply(200, { changed: 1, complete: false, page: 1 }) });
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup(); const start = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(start).toBeEnabled()); await user.click(start);
  const apply = await screen.findByRole('button', { name: 'Diese Seite übernehmen' });
  await waitFor(() => expect(apply).toBeEnabled()); await user.click(apply);
  const error = await screen.findByText(t('b3.error.catalog_content_conflict'));
  expect(apply.closest('.sticky')).toContainElement(error);
  expect(screen.getByText('Seite 1 von 2')).toBeVisible();
  expect(attempts).toBe(1);
  await user.click(apply);
  await screen.findByText('Seite 2 von 2');
  expect(attempts).toBe(2);
  const writes = fake.calls.filter(call => call.path === 'title-sync/apply');
  expect(writes[0]?.body).toEqual(writes[1]?.body);
  expect(fake.activations).toHaveLength(0);
});

it('opens the complete catalog page and bulk editor on an insecure context using getRandomValues', async () => {
  const random = vi.fn(crypto.getRandomValues.bind(crypto));
  vi.stubGlobal('crypto', { getRandomValues: random });
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'synthetic.music', version: 1, classification_schema: { values: ['normal', 'other'] } }]) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [{ identity: 'synthetic', key: 'Synthetic Track', display_name: 'Synthetic Track', scope: 'default', media_type: 'music', signal_type: 'title', classification: 'normal', master_key: null, note: '', complete: true, effective_classification: 'normal', semantic_digest: 'synthetic' }], content_revision: 1, next_cursor: null }) });
  window.location.hash = '#/catalog-care';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  const user = userEvent.setup();
  await screen.findByRole('heading', { name: t('alpha10.catalog.title'), level: 1 });
  await user.click(await screen.findByRole('checkbox', { name: 'Titel auswählen: Synthetic Track' }));
  await user.click(screen.getByRole('button', { name: 'Klasse setzen' }));
  expect(await screen.findByRole('dialog', { name: 'Titel gemeinsam pflegen' })).toBeVisible();
  expect(random).toHaveBeenCalled();
  expect(fake.calls.some(call => call.path === 'catalogs/care/write')).toBe(false);
});
