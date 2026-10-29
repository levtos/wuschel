import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { PageLoading } from '@/components/PageLoading';
import { t } from '@/i18n';
import type { JsonObject } from '@/lib/json';
import example from '../test/fixtures/example-registry.json';
import { installFakeApi, makeDraft } from '../test/fakeApi';
import { idleLive } from '../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(cleanup);
const config = example as unknown as JsonObject;
const routes = [
  ['/', 'alpha9.pages.overview'], ['/contracts/list/all', 'alpha9.pages.contracts'],
  [`/contracts/view/${example.contracts[0]!.contract_id}`, 'alpha9.pages.contract'],
  ['/contracts/view/missing', 'alpha9.pages.contract'],
  ['/contracts/import/json', 'alpha10.transfer.description'], ['/contracts/relations/graph', 'alpha9.pages.graph'],
  ['/templates/own', 'alpha9.pages.templatesOwn'], ['/templates/built-in', 'alpha9.pages.templatesBuiltin'],
  [`/templates/details/${example.definitions[0]!.type_id}/1`, 'alpha9.pages.template'],
  ['/templates/details/missing/1', 'alpha9.pages.template'],
  ...['devices', 'bindings', 'catalogs', 'dependencies'].map(section => [`/sources/${section}`, `alpha9.pages.${section === 'devices' ? 'sources' : section}`]),
  ['/changes/drafts', 'alpha9.pages.drafts'], ['/changes/drafts/d1', 'alpha9.pages.draft'], ['/changes/drafts/missing', 'alpha9.pages.draft'],
  ['/changes/versions', 'alpha9.pages.versions'], ['/changes/transfer', 'alpha10.transfer.description'], ['/import-export', 'alpha10.transfer.description'], ['/catalog-care', 'b17.catalogDescription'],
  ['/settings/system', 'alpha9.system.description'],
  ...['installation', 'connections', 'keys', 'log', 'diagnostics'].map(section => [`/settings/${section}`, `alpha9.pages.${section}`]),
  ['/settings/notifications', 'alpha9.notifications.description'], ['/settings/retention', 'alpha9.retention.description'],
];
function app(path: string) {
  const api = installFakeApi({ revisions: [{ revision: 1, checksum: 'a'.repeat(64), created_at: '2026-10-18T10:00:00Z', config }], drafts: [makeDraft('d1', config)] });
  window.location.hash = `#${path}`;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  return api;
}
it.each(routes)('%s has a title, an everyday explanation and one primary page action', async (path, key) => {
  const api = app(path!);
  await screen.findByText(t(key!), {}, { timeout: 8000 });
  await waitFor(() => expect(document.querySelector('[data-page-loading]')).toBeNull());
  const description = screen.getByText(t(key!));
  expect(description).toBeVisible();
  expect(description.textContent).toMatch(/^Hier kannst du /);
  const header = description.closest('[data-page-header]')!;
  expect(within(header as HTMLElement).getByRole('heading', { level: 1 }).textContent?.trim()).not.toBe('');
  const primary = within(header as HTMLElement).getAllByRole('button').filter(button => button.dataset.variant === 'primary');
  expect(primary).toHaveLength(1);
  expect(api.calls.filter(call => call.method !== 'GET')).toEqual([]);
}, 15_000);

it('retains the page rule while loading, without offering a premature action', () => {
  render(<PageLoading />);
  expect(screen.getByRole('heading', { level: 1, name: 'Wuschel' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Seite neu laden' })).toBeDisabled();
  expect(screen.getByText(t('alpha9.pages.app'))).toBeVisible();
});

it('the connection action performs a read only check and never configures Home Assistant', async () => {
  const api = app('/settings/connections');
  const button = await screen.findByRole('button', { name: t('alpha9.actions.checkConnections') });
  await userEvent.click(button);
  await waitFor(() => expect(api.calls.some(call => call.path === 'ha/entities?limit=1')).toBe(true));
  expect(api.calls.filter(call => call.method !== 'GET')).toEqual([]);
});
