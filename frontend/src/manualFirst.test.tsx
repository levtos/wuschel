import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import example from '../test/fixtures/example-registry.json';
import { flow } from '../test/flow';
import { installFakeApi, reply, type FakeApi } from '../test/fakeApi';
import { idleLive } from '../test/harness';
import { Workbench } from '@/Workbench';
import type { JsonObject } from '@/lib/json';

/**
 * The complete manual-first flow through the real application (routes, pages,
 * editors, working draft, save, activation, export, import): the Alpha-4 example
 * registry is rebuilt from an empty draft *only* by operating controls, saved,
 * activated, exported and re-imported without loss. The HTTP layer is an in-memory
 * fake with the real concurrency rules; the real engine is covered by the isolated
 * end-to-end test (e2e.test.tsx).
 */
const user = userEvent.setup();
const f = flow(user);
const registry = example as unknown as JsonObject;
let api: FakeApi;
let exported: string[];

function app() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Workbench onSetup={() => {}} live={idleLive()} />
    </QueryClientProvider>,
  );
}
const savedConfig = (id?: string): JsonObject => [...api.drafts.values()].find((d) => !id || d.draft_id === id)!.config;

beforeEach(() => {
  window.location.hash = '#/registry';
  api = installFakeApi();
  exported = [];
  // The browser download of generated text is captured instead of opening a file dialog.
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: (blob: Blob) => { void blob.text().then((t) => exported.push(t)); return 'blob:test'; }, revokeObjectURL: () => {} }));
  HTMLAnchorElement.prototype.click = () => {};
});
afterEach(() => {
  window.location.hash = '';
});

describe('Alpha-4 example registry: complete manual-first flow', () => {
  it('rebuilds the registry from an empty draft with controls only, then saves and activates it', async () => {
    app();
    await f.createDraft('GUI-Aufbau', "Leere Konfiguration");
    await screen.findAllByText('GUI-Aufbau');
    await f.buildExampleRegistry();

    await user.click(await f.saveButton());
    await waitFor(() => expect([...api.drafts.values()][0]!.draft_version).toBeGreaterThan(1));
    expect(savedConfig()).toEqual(registry); // exact, including the object value, the machine and the nested rules

    const dialog = await f.activateOpenDraft([...api.drafts.keys()][0]!);
    await within(dialog).findByText("Konfiguration 1 ist übernommen.");
    expect(api.revisions[0]!.config).toEqual(registry);
  }, 240_000);

  it('imports a valid registry, edits it graphically (including structured catalog values), exports and re-imports it without loss', async () => {
    app();
    const enriched: JsonObject = { ...registry, x_future: { z: [2, null], a: { id: 'stable' } }, definitions: (registry.definitions as JsonObject[]).map((d, i) => i === 0 ? { ...d, x_note: { z: 1, a: 2 } } : d) };
    await f.createDraft('Import', "Einlesen (JSON)", JSON.stringify(enriched));
    const imported = [...api.drafts.values()][0]!;
    expect(imported.config).toEqual(enriched);

    // Edit through the UI: a catalog with object / list / null values.
    await f.go('#/sources?tab=catalogs');
    await user.click(await screen.findByRole('button', { name: 'Werteliste hinzufügen' }));
    await f.type(await screen.findByLabelText("Kennung der Werteliste"), 'catalog.struktur');
    await user.selectOptions(screen.getByLabelText('Wert für an: Typ'), 'object');
    await user.click(screen.getByRole('button', { name: 'Wert für an: Eigenschaft hinzufügen' }));
    await f.type(screen.getByLabelText('Wert für an: Schlüssel 1'), 'liste');
    await user.tab();
    await user.selectOptions(screen.getByLabelText('Wert für an.liste: Typ'), 'array');
    await user.click(screen.getByRole('button', { name: 'Wert für an.liste: Element hinzufügen' }));
    await f.type(screen.getByLabelText('Wert für an.liste[1]'), 'eins');
    await user.click(screen.getByRole('button', { name: 'Wert für an.liste: Element hinzufügen' }));
    await user.selectOptions(screen.getByLabelText('Wert für an.liste[2]: Typ'), 'null');
    await user.click(screen.getByRole('button', { name: 'Eintrag' }));
    await f.type(screen.getAllByLabelText('Rohwert').at(-1)!, 'aus');
    await user.tab();
    await user.selectOptions(screen.getByLabelText('Wert für aus: Typ'), 'null');

    await user.click(await f.saveButton());
    const expected = { ...enriched, catalogs: [{ catalog_id: 'catalog.struktur', kind: 'map', version: 1, entries: { an: { liste: ['eins', null] }, aus: null } }] };
    await waitFor(() => expect(api.drafts.get(imported.draft_id)!.draft_version).toBeGreaterThan(1));
    expect(savedConfig(imported.draft_id)).toEqual(expected);

    // Export (real button) → import into a new draft → identical, and the editor shows the real types.
    api.routes.push({ match: /^portable\/export$/, method: 'POST', respond: (_path, body) => {
      const draft = api.drafts.get(body.draft_id);
      if (!draft || draft.draft_version !== body.draft_version) return reply(409, { error: 'draft_version_conflict' });
      return reply(200, { format: 'wuschel.configuration', format_version: 1, scope: 'configuration', config: draft.config, catalog_contents: [], editorial: [] });
    } });
    await f.go('#/registry');
    await user.click((await screen.findAllByRole('button', { name: /Speichern als Datei/ }))[0]!);
    await waitFor(() => expect(exported).toHaveLength(1));
    const portable = JSON.parse(exported[0]!);
    expect(portable.format).toBe('wuschel.configuration');
    expect(portable.config).toEqual(expected);
    expect(Object.keys(portable.config.x_future)).toEqual(['z', 'a']);
    await f.createDraft('Reimport', "Einlesen (JSON)", exported[0]);
    const second = [...api.drafts.values()].find((d) => d.display_name === 'Reimport')!;
    expect(second.config).toEqual(expected);
    await f.go('#/sources?tab=catalogs');
    await user.click(await screen.findByText('catalog.struktur'));
    expect(screen.getByLabelText('Wert für an: Typ')).toHaveValue('object');
    expect(screen.getByLabelText('Wert für an.liste: Typ')).toHaveValue('array');
    expect(screen.getByLabelText('Wert für an.liste[2]: Typ')).toHaveValue('null');
    expect(screen.getByLabelText('Wert für aus: Typ')).toHaveValue('null');
    expect(document.body.textContent).not.toContain('[object Object]');
  }, 240_000);
});
