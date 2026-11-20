import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

async function installRegistry(page: Page) {
  const capabilities = JSON.parse(readFileSync('test/fixtures/capabilities.json', 'utf8'));
  const valid = JSON.parse(readFileSync('test/fixtures/validate-ok.json', 'utf8'));
  const boolean = { type: 'bool', required: true, physical_state: false };
  const config = { config_schema_version: 1, fingerprint_version: 2, dependencies: [], sources: [], bindings: [], catalogs: [], contracts: ['first', 'second'].map(id => ({ contract_id: `synthetic.${id}`, display_name: `Vertrag ${id}`, type_id: 'synthetic.echo', type_version: 1, enabled: true, inputs: [], parameters: {} })), definitions: [{ type_id: 'synthetic.echo', type_version: 1, language_version: 1, inputs: [], schema: { fields: { value: boolean }, available_projection: ['value'] }, outputs: { value: { op: 'literal', value: true, schema: boolean } } }] };
  let active = { revision: 1, checksum: 'synthetic', config, created_at: '2032-04-05T12:00:00Z' };
  type Draft = { draft_id: string; draft_version: number; config: typeof config; lifecycle: string; base_revision: number; display_name: string };
  const drafts = new Map<string, Draft>();
  const publish: (() => void)[] = [];
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const name = new URL(request.url()).pathname.split('/api/v1/')[1]!;
    const body = request.postDataJSON();
    let data: unknown = {};
    const match = name.match(/^registry\/drafts\/([^/]+)(?:\/(.*))?$/);
    if (name === 'setup/status') data = { phase: 'completed', onboarding_complete: true, database_configured: true, installation_id: 'synthetic' };
    else if (name === 'info') data = { version: 'synthetic', installation_id: 'synthetic', epoch_id: 'synthetic', server_time: '2032-04-05T12:00:00Z' };
    else if (name === 'capabilities') data = capabilities;
    else if (name === 'registry/active') data = active;
    else if (name === 'registry/active/export') data = active.config;
    else if (name === 'registry/revisions') data = [active];
    else if (name === 'registry/drafts') {
      if (request.method() === 'POST') {
        const draft: Draft = { draft_id: `synthetic-draft-${drafts.size + 1}`, draft_version: 1, config: structuredClone(body.config ?? active.config), lifecycle: 'open', base_revision: active.revision, display_name: body.display_name ?? 'Entwurf' };
        drafts.set(draft.draft_id, draft); data = draft;
      } else data = [...drafts.values()];
    } else if (match) {
      const draft = drafts.get(match[1]!)!;
      if (!match[2] && request.method() === 'PUT') { draft.config = body.config; draft.display_name = body.display_name; draft.draft_version++; }
      if (match[2] === 'validate') data = valid;
      else if (match[2] === 'activation-preview') data = { expected_active_revision: active.revision, comparison_digest: 'synthetic', no_effect: false, required_confirmations: ['impact'], retention: { before: null, after: null, changed: false, shortening: [] } };
      else if (match[2] === 'activate') { active = { ...active, config: structuredClone(draft.config), revision: active.revision + 1 }; draft.lifecycle = 'completed'; data = active; publish.forEach(send => send()); }
      else data = draft;
    } else if (name === 'registry/validate') data = valid;
    else if (name === 'editorial') data = { items: [], draft_items: [] };
    else if (name.startsWith('history/query/')) data = { items: [], next_cursor: null };
    else if (name === 'diagnostics/problems') data = { problems: [] };
    else if (name === 'ha/entities') data = { items: [], next_cursor: null, read_only: true };
    else if (name === 'catalogs') data = [];
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const snapshot = () => socket.send(JSON.stringify({ type: 'snapshot', epoch_id: 'synthetic', publication_seq: active.revision, registry_revision: active.revision, contracts: {} }));
    publish.push(snapshot);
    socket.onMessage(message => {
      const value = JSON.parse(String(message));
      if (value.type === 'hello') socket.send(JSON.stringify({ type: 'welcome', installation_id: 'synthetic' }));
      if (value.type === 'subscribe') snapshot();
    });
  });
}

async function renameAndApply(page: Page, name: string, revision: number) {
  await page.getByRole('button', { name: 'Umbenennen', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Vertrag bearbeiten', exact: true });
  const rename = page.getByRole('dialog', { name: 'Umbenennen', exact: true });
  await rename.getByRole('textbox').fill(name);
  await rename.getByRole('button', { name: 'Übernehmen' }).click();
  await page.getByRole('alertdialog', { name: 'Umbenennen' }).getByRole('button', { name: 'Übernehmen' }).click();
  await expect(rename).not.toBeVisible();
  await editor.getByRole('button', { name: 'In Entwurf speichern', exact: true }).click();
  await expect(editor).not.toBeVisible();
  const apply = page.getByRole('button', { name: 'Änderungen übernehmen', exact: true });
  await expect(apply).toBeEnabled();
  await apply.click();
  const confirmation = page.getByRole('dialog', { name: 'Konfiguration übernehmen' });
  await confirmation.getByRole('checkbox', { name: /Auswirkungen geprüft/ }).check();
  await confirmation.getByRole('button', { name: 'Jetzt übernehmen' }).click();
  await expect(confirmation.getByText(`Konfiguration ${revision} ist übernommen.`, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('terra.workingDraft'))).toBeNull();
  await confirmation.locator('header').getByRole('button', { name: 'Schließen' }).click();
  await expect(confirmation).not.toBeVisible();
}

for (const next of ['Bearbeiten', 'Neuer Vertrag']) test(`after X, ${next} starts another successful activation`, async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await installRegistry(page);
  await page.goto('/#/contracts/view/synthetic.first');
  await page.getByRole('button', { name: 'Bearbeiten', exact: true }).click();
  await renameAndApply(page, 'Erster neuer Name', 2);
  if (next === 'Bearbeiten') {
    await page.evaluate(() => { window.location.hash = '#/contracts/view/synthetic.second'; });
    await page.getByRole('button', { name: 'Bearbeiten', exact: true }).click();
  } else {
    await page.evaluate(() => { window.location.hash = '#/contracts/list/all'; });
    await page.getByRole('button', { name: 'Neuer Vertrag', exact: true }).click();
    await page.getByRole('button', { name: 'Ohne Gerät anlegen' }).click();
    const create = page.getByRole('dialog', { name: 'Neuer Vertrag' });
    await create.getByRole('textbox').fill('synthetic.new');
    await create.getByRole('button', { name: 'In Entwurf speichern', exact: true }).click();
  }
  await expect(page.getByRole('button', { name: 'Umbenennen', exact: true })).toBeVisible();
  await renameAndApply(page, 'Zweiter neuer Name', 3);
  expect(errors).toEqual([]);
  await page.evaluate(() => { window.location.hash = '#/changes/drafts/synthetic-draft-2'; });
  await page.reload();
  await expect(page.getByRole('heading', { name: /Änderungen vom/, level: 1 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Änderungen übernehmen', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('terra.workingDraft'))).toBeNull();
});
