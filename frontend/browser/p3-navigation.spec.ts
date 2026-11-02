import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

test('Back restores filters and the main scroll position in Firefox', async ({ page }) => {
  const capabilities = JSON.parse(readFileSync('test/fixtures/capabilities.json', 'utf8'));
  const boolean = { type: 'bool', required: true, physical_state: false };
  const contracts = Array.from({ length: 100 }, (_, index) => ({ contract_id: `synthetic.${String(index).padStart(3, '0')}`, display_name: `Synthetic entry ${String(index).padStart(3, '0')}`, type_id: 'synthetic.echo', type_version: 1, enabled: true, inputs: [], parameters: {} }));
  const config = { config_schema_version: 1, dependencies: [], sources: [], bindings: [], catalogs: [], contracts, definitions: [{ type_id: 'synthetic.echo', type_version: 1, language_version: 1, inputs: [], schema: { fields: { value: boolean }, available_projection: ['value'] }, outputs: { value: { op: 'literal', value: true, schema: boolean } } }] };
  const active = { revision: 1, checksum: 'synthetic', config, created_at: '2032-04-05T12:00:00Z' };
  await page.route('**/api/v1/**', async route => {
    const name = new URL(route.request().url()).pathname.split('/api/v1/')[1];
    let data: unknown = {};
    if (name === 'setup/status') data = { phase: 'completed', onboarding_complete: true, database_configured: true, installation_id: 'synthetic-installation' };
    else if (name === 'info') data = { version: 'synthetic', installation_id: 'synthetic-installation', epoch_id: 'synthetic-epoch', server_time: '2032-04-05T12:00:00Z' };
    else if (name === 'capabilities') data = capabilities;
    else if (name === 'registry/active') data = active;
    else if (name === 'registry/active/export') data = config;
    else if (name === 'registry/revisions') data = [active];
    else if (name === 'registry/drafts' || name === 'catalogs') data = [];
    else if (name === 'editorial') data = { items: contracts.map(contract => ({ object: { kind: 'contract', id: contract.contract_id }, editorial_version: 1, category: 'media', room: { mode: 'none', area_id: null, name: '', icon: 'mdi:home', missing: false } })), draft_items: [] };
    else if (name?.startsWith('history/query/')) data = { items: [], next_cursor: null };
    else if (name === 'diagnostics/problems') data = { problems: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.routeWebSocket('**/api/v1/ws', socket => {
    socket.onMessage(message => {
      const value = JSON.parse(String(message));
      if (value.type === 'hello') socket.send(JSON.stringify({ type: 'welcome', installation_id: 'synthetic-installation' }));
      if (value.type === 'subscribe') socket.send(JSON.stringify({ type: 'snapshot', epoch_id: 'synthetic-epoch', publication_seq: 1, registry_revision: 1, contracts: {} }));
    });
  });
  await page.goto('/#/contracts/list/all');
  const main = page.locator('#main');
  await expect(page.getByRole('link', { name: 'Synthetic entry 030', exact: true })).toBeAttached();
  await page.getByLabel('Kategorie', { exact: true }).selectOption('media');
  await page.getByLabel('Verträge suchen', { exact: true }).fill('Synthetic entry');
  await expect(page).toHaveURL(/category=media/);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const target = page.getByRole('link', { name: 'Synthetic entry 030', exact: true });
  await target.scrollIntoViewIfNeeded();
  const position = await main.evaluate(element => element.scrollTop);
  expect(position).toBeGreaterThan(100);
  await target.click();
  await expect(page).toHaveURL(/contracts\/view\/synthetic\.030/);
  await page.goBack();
  await expect(page.getByLabel('Verträge suchen', { exact: true })).toHaveValue('Synthetic entry');
  await expect(page.getByLabel('Kategorie', { exact: true })).toHaveValue('media');
  await expect.poll(async () => Math.abs(await main.evaluate(element => element.scrollTop) - position)).toBeLessThan(2);
});
