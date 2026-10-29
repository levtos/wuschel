import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { t } from '@/i18n';
import de from '@/i18n/de.json';
import { installFakeApi, reply } from '../test/fakeApi';
import { idleLive } from '../test/harness';
import type { ServiceState } from '@/lib/types';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function app(path: string, service?: ServiceState) {
  const live = idleLive();
  if (service) { const snapshot = { ...live.getSnapshot(), service }; vi.spyOn(live, 'getSnapshot').mockReturnValue(snapshot); }
  window.location.hash = `#${path}`;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={live} /></QueryClientProvider>);
}

it.each([
  ['disabled', true, true], ['disabled', false, false], ['supervisor', true, false], ['external', true, false],
] as const)('MQTT %s with external settings %s shows only the relevant hint', async (mode, present, shown) => {
  installFakeApi();
  app('/settings/connections', { ready: false, publication_confirmed: false, persistence: 'connected', ha: 'disconnected', bridge: 'disconnected', mqtt: 'disabled', mqtt_mode: mode, mqtt_external_settings_present: present, reasons: [] });
  await screen.findByRole('heading', { name: t('alpha7.nav.connections'), level: 1 }, { timeout: 5000 });
  expect(!!screen.queryByText(t('alpha10.mqtt.disabledHint'))).toBe(shown);
});

it('keeps an unsent catalog assignment through a 409 and requires an explicit comparison and retry', async () => {
  const fake = installFakeApi(); const user = userEvent.setup();
  const entry = { identity: 'synthetic', key: 'Synthetic Title', scope: 'default', media_type: 'music', signal_type: 'title', classification: null, master_key: null, display_name: '', note: '', effective_classification: 'other', complete: false, semantic_digest: 'synthetic' };
  fake.routes.push({ match: /^catalogs\/preview$/, method: 'POST', respond: () => reply(200, { affected_keys: 1, affected_contracts: 0 }) });
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'example.titles', version: 1, classification_schema: { values: ['other', 'example'] } }]) });
  fake.routes.push({ match: /^catalogs\/(?:entries|care)\?/, respond: () => reply(200, { items: [entry], content_revision: 1, next_cursor: null }) });
  fake.routes.push({ match: /^catalogs\/entry\?/, respond: () => reply(200, { content_revision: 2, active_revision: 0, effective_classification: 'other', entry }) });
  fake.routes.push({ match: /^catalogs\/entries$/, method: 'POST', respond: () => reply(200, { content_revision: 3 }) });
  app('/catalog-care');
  await user.click(await screen.findByRole('button', { name: t('alpha10.catalog.edit') }));
  const dialog = screen.getByRole('dialog');
  await user.selectOptions(within(dialog).getByLabelText(t('alpha10.catalog.class')), 'example');
  await user.type(within(dialog).getByLabelText(t('alpha10.catalog.note')), 'Synthetic decision');
  fake.failNext.push({ path: /^catalogs\/entries$/, status: 409, body: { error: 'catalog_content_conflict' } });
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') }));
  await within(dialog).findByText(t('alpha10.catalog.impact', { keys: 1, contracts: 0 }));
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.save') }));
  await user.click(await within(dialog).findByRole('button', { name: t('alpha10.catalog.reload') }));
  await within(dialog).findByText(t('alpha10.catalog.comparison', { classification: 'other', master: t('alpha10.catalog.independent') }));
  expect(within(dialog).getByLabelText(t('alpha10.catalog.note'))).toHaveValue('Synthetic decision');
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') }));
  await within(dialog).findByText(t('alpha10.catalog.impact', { keys: 1, contracts: 0 }));
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.save') }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const writes = fake.calls.filter(call => call.path === 'catalogs/entries' && call.method === 'POST');
  expect(writes).toHaveLength(2);
  expect(writes[1]!.body).toMatchObject({ expected_content_revision: 2, changes: [{ classification: 'example', note: 'Synthetic decision' }] });
  expect(fake.activations).toEqual([]);
}, 15_000);

it('has no retired product names in the current German UI dictionary', () => {
  for (const value of Object.values(de)) expect(value).not.toMatch(/\b(Mimir|Terra|Bifröst)\b/i);
});

it('rejects a late preview after the assignment form changes', async () => {
  const fake = installFakeApi(); const user = userEvent.setup();
  let release!: (value: Response) => void;
  const pending = new Promise<Response>(resolve => { release = resolve; });
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'example.titles', version: 1, classification_schema: { values: ['other', 'example'] } }]) });
  fake.routes.push({ match: /^catalogs\/(?:entries|care)\?/, respond: () => reply(200, { items: [{ identity: 'synthetic', key: 'Synthetic Title', scope: 'default', media_type: 'music', signal_type: 'title', classification: null, master_key: null, display_name: '', note: '', effective_classification: 'other', complete: false, semantic_digest: 'synthetic' }], content_revision: 1, next_cursor: null }) });
  fake.routes.push({ match: /^catalogs\/preview$/, method: 'POST', respond: () => pending });
  app('/catalog-care');
  await user.click(await screen.findByRole('button', { name: t('alpha10.catalog.edit') }));
  const dialog = screen.getByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') }));
  await waitFor(() => expect(fake.calls.some(call => call.path === 'catalogs/preview')).toBe(true));
  await user.type(within(dialog).getByLabelText(t('alpha10.catalog.note')), 'Changed while waiting');
  await act(async () => { release(reply(200, { affected_keys: 1, affected_contracts: 1 })); await pending; });
  await waitFor(() => expect(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') })).toBeEnabled());
  expect(within(dialog).getByRole('button', { name: t('alpha10.catalog.save') })).toBeDisabled();
  expect(within(dialog).queryByText(t('alpha10.catalog.impact', { keys: 1, contracts: 1 }))).not.toBeInTheDocument();
  expect(fake.calls.some(call => call.path === 'catalogs/entries' && call.method === 'POST')).toBe(false);
}, 15_000);

it('pages older catalog history and keeps the current head for a restore preview', async () => {
  const fake = installFakeApi(); const user = userEvent.setup();
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'example.titles', version: 1, classification_schema: { values: ['other', 'example'] } }]) });
  fake.routes.push({ match: /^catalogs\/(?:entries|care)\?/, respond: () => reply(200, { items: [], content_revision: 120, next_cursor: null }) });
  fake.routes.push({ match: /^catalogs\/history\?/, respond: path => {
    const before = Number(new URLSearchParams(path.split('?')[1]).get('after') || 121);
    return reply(200, Array.from({ length: Math.min(100, before - 1) }, (_, index) => ({ revision: before - index - 1, head_revision: 120, active_revision: 0, at: '2026-10-20T12:00:00Z', reason: 'Synthetic history', affected: [] })));
  } });
  fake.routes.push({ match: /^catalogs\/revision\?/, respond: () => reply(200, { items: [], total: 0, next_cursor: null }) });
  fake.routes.push({ match: /^catalogs\/preview$/, method: 'POST', respond: () => reply(200, { affected_keys: 0, affected_contracts: 0 }) });
  app('/catalog-care');
  await user.selectOptions(await screen.findByRole('combobox', { name: t('alpha10.catalog.view') }), 'history');
  await user.click(await screen.findByRole('button', { name: t('alpha10.catalog.older') }));
  const row = (await screen.findByText(t('alpha10.catalog.revision', { n: 20 }), { exact: false })).closest('li')!;
  await user.click(within(row).getByRole('button', { name: t('alpha10.catalog.historyOpen') }));
  const dialog = await screen.findByRole('dialog', { name: t('alpha10.catalog.revision', { n: 20 }) });
  await waitFor(() => expect(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') })).toBeEnabled());
  await user.click(within(dialog).getByRole('button', { name: t('alpha10.catalog.preview') }));
  await waitFor(() => expect(fake.calls.find(call => call.path === 'catalogs/preview')?.body).toMatchObject({ restore_revision: 20, expected_content_revision: 120 }));
  expect(fake.calls.some(call => call.path === 'catalogs/entries' && call.method === 'POST')).toBe(false);
}, 15_000);
