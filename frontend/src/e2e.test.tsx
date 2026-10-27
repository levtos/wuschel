import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, configure, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import WebSocket from 'ws'; // jsdom's global Event and Node's WebSocket are not compatible
import { IDBFactory } from 'fake-indexeddb';
import example from '../test/fixtures/example-registry.json';
import { flow } from '../test/flow';
import { clickReadyButton } from '../test/gui';
import { exerciseCoverage } from '../test/coverage';
import type { Capabilities } from '@/lib/capabilities';
import { Workbench } from '@/Workbench';
import { LiveStore, type SocketLike } from '@/lib/live';
import type { JsonObject } from '@/lib/json';
import ux12 from '../test/fixtures/ux12-komplett.json';
import { MachineEditor } from '@/editors/MachineEditor';
import { FieldSchemaEditor } from '@/editors/FieldSchemaEditor';
import type { FieldSchema } from '@/features/authoring/model';
import { Stateful, wrap } from '../test/harness';
import { objects } from '@/features/sources/sourceMigration';
import type { Draft, RevisionRow } from '@/lib/types';
import { reserveRequest } from '@/lib/request-budget';
import { episodeId as b14Episode, sleepRegistry as b14Registry } from '../test/b14-fixtures';

/**
 * End-to-end against the REAL API and engine in an isolated, in-memory instance
 * (`python dev/workbench_demo.py --feed --interval 1`, started fresh and empty). It
 * is skipped unless WUSCHEL_E2E_URL points at such an instance. Never point it at an
 * installation: it creates drafts, activates registries and rolls back.
 *
 *   WUSCHEL_E2E_URL=http://127.0.0.1:8123 npm run test:e2e
 */
const base = process.env.WUSCHEL_E2E_URL;
// Real HTTP requests share the production client's 61-second request window.
// Await the acknowledged UI state rather than jsdom's default one-second window.
configure({ asyncUtilTimeout: 65_000 });
vi.unmock('@/lib/request-budget');
const user = userEvent.setup();
const f = flow(user);
const registry = example as unknown as JsonObject;
const realFetch = globalThis.fetch;
const raw = async <T,>(path: string, init?: RequestInit): Promise<{ status: number; body: T }> => {
  if (path === 'registry/drafts' && init?.method === 'POST') { const body = JSON.parse(String(init.body)); await reserveRequest(); const head = await (await realFetch(`${base}/api/v1/registry/active`)).json(); init = { ...init, body: JSON.stringify({ ...body, expected_active_revision: head?.revision ?? 0 }) }; }
  // Fixture/readback calls share the same real request budget as the UI. They
  // must not exhaust the server's headroom behind the production client's back.
  await reserveRequest();
  const response = await realFetch(`${base}/api/v1/${path}`, init);
  const text = await response.text();
  try {
    return { status: response.status, body: JSON.parse(text) as T };
  } catch {
    throw new Error(`${init?.method ?? 'GET'} ${path} -> ${response.status} ${text.slice(0, 80)}`);
  }
};
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json', 'X-Authoring-Protocol': '2' }, body: JSON.stringify(body) });

async function activationInput(draft: { draft_id: string; draft_version: number }, expected: number) {
  const preview = (await raw<{ comparison_digest: string; required_confirmations: string[] }>(`registry/drafts/${draft.draft_id}/activation-preview`, json('POST', {}))).body;
  return { request_id: crypto.randomUUID(), draft_version: draft.draft_version, expected_active_revision: expected, comparison_digest: preview.comparison_digest, confirmations: preview.required_confirmations };
}

/** The server stores/exports its canonical form (defaults filled in), so equality is judged by the server's own diff. */
interface Verdict {
  valid: boolean;
  diff: { objects: Record<string, { added: string[]; removed: string[]; changed: string[] }>; affected_contracts: string[]; fingerprint_changes: string[] };
}
async function expectSameAsActive(document: unknown) {
  const { status, body } = await raw<Verdict>('registry/validate', json('POST', document));
  expect(status).toBe(200);
  expect(body.valid).toBe(true);
  for (const [group, change] of Object.entries(body.diff.objects)) expect({ group, added: change.added, removed: change.removed, changed: change.changed }).toEqual({ group, added: [], removed: [], changed: [] });
  expect(body.diff.affected_contracts).toEqual([]);
  expect(body.diff.fingerprint_changes).toEqual([]);
}

let store: LiveStore;

/** Publication is the engine's acknowledgement, independent of the HTTP budget.
 * One readback then checks the same value over the real API without polling it. */
async function expectPublishedValue(contractId: string, expected: { value: unknown; status?: string }) {
  store.start();
  await waitFor(() => {
    expect(store.getSnapshot().connection).toBe('open');
    expect(store.getSnapshot().contracts?.[contractId]?.fields.value).toMatchObject(expected);
  });
  const response = await raw<{ fields: { value: { value: unknown; status: string } } }>(`contracts/${contractId}`);
  expect(response.status).toBe(200);
  expect(response.body.fields.value).toMatchObject(expected);
}

describe.skipIf(!base)('Workbench ↔ real engine (isolated instance)', () => {
  it('validates all additional GUI-authored operators, freshness modes and bindings against the real engine', async () => {
    const capabilities = (await raw<Capabilities>('capabilities')).body;
    await exerciseCoverage(async (document) => {
      const verdict = await raw<{ valid: boolean; issues: unknown[] }>('registry/validate', json('POST', document));
      expect(verdict, JSON.stringify(document)).toMatchObject({ status: 200, body: { valid: true, issues: [] } });
    }, capabilities);
  }, 120_000);
  beforeEach(() => {
    sessionStorage.clear(); localStorage.clear();
    vi.stubGlobal('indexedDB', new IDBFactory());
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => realFetch(typeof input === 'string' && input.startsWith('./') ? `${base}${input.slice(1)}` : input, init));
    HTMLAnchorElement.prototype.click = () => {};
    store = new LiveStore({ createSocket: () => new WebSocket(`${base!.replace(/^http/, 'ws')}/api/v1/ws`) as unknown as SocketLike });
    window.location.hash = '#/registry';
  });
  afterEach(() => {
    store?.stop();
    vi.unstubAllGlobals();
  });

  it('builds the registry by controls, validates, activates, observes it live, exports, re-imports, changes semantics, rolls back and handles a conflict', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Workbench onSetup={() => {}} live={store} />
      </QueryClientProvider>,
    );
    const empty = await raw<unknown[]>('registry/drafts');
    expect(empty.body).toEqual([]); // the instance must be fresh
    expect((await raw('registry/active')).body).toBeNull();

    // 1. Build from an empty draft — controls only — and save.
    await f.createDraft('E2E', "Leere Konfiguration");
    await f.buildExampleRegistry();
    await user.click(await f.saveButton());
    // The UI acknowledges the completed PUT. Read back once afterwards: an
    // async waitFor callback can still be queued in the real 61s budget when its
    // 15s deadline fires, hiding both the response and the actual assertion.
    await screen.findByText('Im Entwurf gespeichert');
    const saved = await raw<{ draft_id: string; draft_version: number; config: JsonObject }[]>('registry/drafts');
    expect(saved.status).toBe(200);
    const drafts = saved.body;
    expect(drafts[0]!.draft_version).toBeGreaterThan(1);
    expect(drafts[0]!.config).toEqual(registry);
    const draftId = drafts[0]!.draft_id;
    expect(drafts[0]!.config).toEqual(registry); // stored by the real server exactly as built

    // 2. The real server validates what the UI produced, then the UI activates it.
    const verdict = await raw<{ valid: boolean; issues: unknown[] }>('registry/validate', json('POST', drafts[0]!.config));
    expect(verdict).toMatchObject({ status: 200, body: { valid: true, issues: [] } });
    const dialog = await f.activateOpenDraft(draftId);
    await within(dialog).findByText("Konfiguration 1 ist übernommen.", {}, { timeout: 20_000 });
    await user.click(within(dialog).getAllByRole('button', { name: /Konfiguration ansehen|Schließen/ }).at(-1)!);
    await expectSameAsActive(registry); // what the server activated is semantically the registry that was built

    // 3. The activated cascade is observed over the real WebSocket (snapshot → deltas).
    await waitFor(() => {
      const snap = store.getSnapshot();
      expect(snap.connection).toBe('open');
      expect(Object.keys(snap.contracts ?? {}).sort()).toEqual(['fixture.condition', 'fixture.either', 'fixture.flow', 'fixture.left', 'fixture.right', 'fixture.timed']);
      expect(snap.registryRevision).toBe(1);
      expect(snap.contracts!['fixture.left']!.fields.value!.status).toBe('valid');
    }, { timeout: 20_000 });
    const before = store.getSnapshot().publicationSeq!;
    await waitFor(() => expect(store.getSnapshot().publicationSeq!).toBeGreaterThan(before), { timeout: 15_000 }); // the feed keeps publishing

    // 4. Export the active registry and import it again: lossless, and the server accepts it.
    const exportedActive = (await raw<JsonObject>('registry/active/export')).body;
    await expectSameAsActive(exportedActive);
    await f.go('#/registry');
    await f.createDraft('E2E-Reimport', "Einlesen (JSON)", JSON.stringify(exportedActive));
    const reimported = (await raw<{ draft_id: string; display_name: string; config: JsonObject }[]>('registry/drafts')).body.find((d) => d.display_name === 'E2E-Reimport')!;
    expect(reimported.config).toEqual(exportedActive); // the draft keeps the imported document verbatim
    await expectSameAsActive(reimported.config);

    // 5. Semantic change through the UI: new immutable version of test.truth, migrate fixture.left.
    await f.go('#/definitions/test.truth/1');
    await screen.findByText(/Übernommene Vorlagenversion/);
    await user.click(screen.getByRole('button', { name: /Neue Version/ }));
    const versioning = await screen.findByRole('dialog', { name: /Neue Version von test.truth/ });
    await user.click(within(versioning).getByRole('checkbox', { name: 'fixture.right' })); // only fixture.left migrates
    await user.click(within(versioning).getByRole('button', { name: "Version anlegen" }));
    await screen.findByRole('tab', { name: 'Allgemein' });
    await f.tab(/Ausgaben/);
    await f.setOp('/definitions/5/outputs/value', 'not'); // v2 negates its input (unlocked, never activated)
    await user.click(await f.saveButton());
    await screen.findByText('Im Entwurf gespeichert');
    const changed = await raw<{ config: { definitions: JsonObject[] } }>(`registry/drafts/${reimported.draft_id}`);
    expect(changed.status).toBe(200);
    expect(changed.body.config.definitions).toHaveLength(6);
    const second = await f.activateOpenDraft(reimported.draft_id);
    await within(second).findByText("Konfiguration 2 ist übernommen.", {}, { timeout: 65_000 });
    await user.click(within(second).getAllByRole('button', { name: /Konfiguration ansehen|Schließen/ }).at(-1)!);
    await waitFor(() => expect(store.getSnapshot().registryRevision).toBe(2), { timeout: 20_000 });

    // 6. Rollback to revision 1 through the UI: a NEW revision with the historical content.
    await f.go('#/changes/versions');
    const rev1 = (await within(await screen.findByRole('main', {}, { timeout: 65_000 })).findByText("Konfiguration 1", {}, { timeout: 65_000 })).closest('li')!;
    const rollbackButton = await within(rev1).findByRole('button', { name: 'Auf diese Konfiguration zurücksetzen' }, { timeout: 65_000 });
    await waitFor(() => expect(rollbackButton).toBeEnabled(), { timeout: 65_000 });
    await user.click(rollbackButton);
    const rollback = await screen.findByRole('dialog', { name: /Auf Konfiguration 1 zurücksetzen/ });
    // The real shared scheduler can wait for its 61-second window after this fast scripted flow.
    await user.click(await within(rollback).findByRole('checkbox', { name: /Ich möchte Konfiguration 1 als neue Konfiguration 3/ }, { timeout: 65_000 }));
    await user.click(within(rollback).getByRole('button', { name: "Zurücksetzen" }));
    await within(rollback).findByText(/Konfiguration 3 ist übernommen/, {}, { timeout: 20_000 });
    await user.click(within(rollback).getAllByRole('button', { name: /Konfiguration ansehen|Schließen/ }).at(-1)!);
    await expectSameAsActive(registry); // revision 3 carries revision 1's content again
    expect((await raw<{ items: { operation: string }[] }>('history/query/activations')).body.items.map((i) => i.operation)).toEqual(['rollback', 'activate', 'activate']);
    await waitFor(() => expect(store.getSnapshot().registryRevision).toBe(3), { timeout: 20_000 });

    // 7. A new working draft is explicit; the completed original stays immutable.
    await f.go('#/changes/drafts');
    await f.createDraft('E2E-Weiterarbeit', 'Einlesen (JSON)', JSON.stringify(exportedActive));
    const working = (await raw<{ draft_id: string; display_name: string }[]>('registry/drafts')).body.find(d => d.display_name === 'E2E-Weiterarbeit')!.draft_id;
    await f.go('#/contracts');
    await user.click(await screen.findByRole('button', { name: /Neuer Vertrag/ }));
    await user.click(await screen.findByRole('button', { name: 'Ohne Gerät anlegen' }));
    const create = await screen.findByRole('dialog', { name: "Neuer Vertrag" });
    await f.type(within(create).getByLabelText("Vertragskennung"), 'fixture.extra');
    await user.selectOptions(within(create).getByLabelText('Vorlage und Version'), 'test.truth@1');
    const addContract = within(create).getByRole('button', { name: 'Anlegen' });
    await waitFor(() => expect(addContract).toBeEnabled());
    await user.click(addContract);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Neuer Vertrag' })).not.toBeInTheDocument(), { timeout: 65_000 });
    await user.click(await screen.findByRole('tab', { name: 'Konfiguration' }));
    await f.type(screen.getByLabelText(/^(Anzeigename|Name)$/), 'Zusatzvertrag');
    const current = (await raw<{ draft_version: number; config: JsonObject }>(`registry/drafts/${working}`)).body;
    const other = await raw(`registry/drafts/${working}`, json('PUT', { config: { ...current.config, other_session: true }, draft_version: current.draft_version }));
    expect(other.status).toBe(200);
    await user.click(await f.saveButton());
    await screen.findByRole('dialog', { name: /Versionskonflikt/ }, { timeout: 65_000 });
    expect(((await raw<{ config: JsonObject }>(`registry/drafts/${working}`)).body.config as JsonObject).other_session).toBe(true); // the other session's data is intact
    await user.click(screen.getByRole('button', { name: 'Änderungen zusammenführen' }));
    await screen.findByText('Im Entwurf gespeichert');
    const merged = await raw<Draft>(`registry/drafts/${working}`);
    expect(merged.status).toBe(200);
    expect(merged.body.config.other_session).toBe(true);
    expect(JSON.stringify(merged.body.config)).toContain('Zusatzvertrag');
  }, 300_000);

  it('routes the untouched UX12 import through guided devices without manual transformations and verifies real engine values', async () => {
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    await f.createDraft('UX12 Geräteführung', 'Einlesen (JSON)', JSON.stringify(ux12));
    const imported = (await raw<{ draft_id: string; display_name: string }[]>('registry/drafts')).body.find(draft => draft.display_name === 'UX12 Geräteführung')!;
    await f.go('#/sources/devices');
    for (const binding of ux12.bindings) {
      await user.click(await screen.findByRole('button', { name: 'Quelle hinzufügen' }));
      const wizard = await screen.findByRole('dialog', { name: 'Gerät aus Home Assistant hinzufügen' });
      const source = ux12.sources.find(source => source.source_id === binding.source_id)!;
      await user.click(await within(wizard).findByRole('radio', { name: source.display_name }, { timeout: 65_000 }));
      await user.click(within(wizard).getByRole('button', { name: 'Gerät hinzufügen' }));
      await waitFor(() => {
        const remaining = screen.queryByRole('dialog');
        if (remaining) throw new Error(`Device picker did not close: ${remaining.textContent}`);
      }, { timeout: 65_000 });
    }
    await user.click(await f.saveButton());
    await screen.findByText('Im Entwurf gespeichert');
    const readback = await raw<{ config: JsonObject; draft_version: number }>(`registry/drafts/${imported.draft_id}`);
    expect(readback.status).toBe(200);
    const saved = readback.body;
    expect(saved.draft_version).toBeGreaterThan(1);
    const document = saved.config;
    const sources = document.sources as JsonObject[];
    const bindings = document.bindings as JsonObject[];
    for (let index = 0; index < bindings.length; index++) {
      expect(bindings[index]!.transform).toEqual({ kind: 'type_cast', target: sources[index]!.source_type === 'boolean' ? 'bool' : 'number' });
    }
    cleanup(); store.stop();
    const definitions = document.definitions as JsonObject[];
    const machine = definitions[3]!;
    wrap(<Stateful initial={machine.machine as JsonObject} onValue={(value) => { machine.machine = value; }}>{(value, set) => <MachineEditor value={value} onChange={set} pointer="/definitions/3/machine" definition={machine} caps={undefined} locked={false} />}</Stateful>);
    expect(screen.getByText('Neustart-Bedingung fehlt')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Neustart, wenn offen Nein ist' }));
    cleanup();
    // Existing delta schema is unitless; correct the old fixture through its field control.
    const schema = definitions[2]!.schema as JsonObject;
    const fields = schema.fields as JsonObject;
    wrap(<Stateful initial={fields.differenz as JsonObject} onValue={(value) => { fields.differenz = value; }}>{(value, set) => <FieldSchemaEditor value={value as FieldSchema} onChange={set} pointer="/definitions/2/schema/fields/differenz" />}</Stateful>);
    await user.clear(screen.getByLabelText('Einheit (freiwillig)'));
    cleanup();
    const verdict = await raw<{ valid: boolean; issues: unknown[] }>('registry/validate', json('POST', document));
    expect(verdict).toMatchObject({ status: 200, body: { valid: true, issues: [] } });
    const active = (await raw<{ revision: number } | null>('registry/active')).body;
    const draft = await raw<{ draft_id: string; draft_version: number }>('registry/drafts', json('POST', { config: document, display_name: 'UX12' }));
    const result = await raw(`registry/drafts/${draft.body.draft_id}/activate`, json('POST', await activationInput(draft.body, active?.revision ?? 0)));
    expect(result.status).toBe(200);
    await expectSameAsActive(document);
    await expectPublishedValue('bad.temperatur', { value: 21.5, status: 'valid' });
    await expectPublishedValue('fenster.offen', { value: false, status: 'valid' });
  }, 120_000);

  it('moves a guided temperature source to text through the checked dialog, versions its template and retains numeric evidence', async () => {
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    await f.createDraft('P8 Quellenumzug', 'Leere Konfiguration');
    await f.go('#/contracts/list/all');
    await user.click(await screen.findByRole('button', { name: 'Neuer Vertrag' }, { timeout: 65_000 }));
    const adding = await screen.findByRole('dialog', { name: 'Neuer Vertrag' });
    await user.click(await within(adding).findByRole('radio', { name: ux12.sources[0]!.display_name }, { timeout: 65_000 }));
    await user.selectOptions(within(adding).getByLabelText('Wann gilt der Wert als aktuell?'), 'periodic_ttl');
    await user.type(within(adding).getByLabelText('Wie oft meldet sich der Sensor spätestens?'), '60');
    await f.type(within(adding).getByLabelText('Name'), 'P8 Temperatur');
    await user.click(within(adding).getByRole('button', { name: 'Vertrag anlegen' }));
    // K.01: the guided objects use the same server comparison in the bar and page.
    await user.click(await f.saveButton());
    await screen.findByText('Im Entwurf gespeichert');
    const drafts = await raw<{ draft_id: string; display_name: string; draft_version: number; config: JsonObject }[]>('registry/drafts');
    expect(drafts.status).toBe(200);
    const guided = drafts.body.find(item => item.display_name === 'P8 Quellenumzug')!;
    expect(guided.draft_version).toBeGreaterThan(1);
    const comparison = (await raw<Verdict>('registry/validate', json('POST', guided.config))).body;
    const objectCount = Object.values(comparison.diff.objects).reduce((total, group) => total + group.added.length + group.changed.length + group.removed.length, 0);
    await f.go(`#/changes/drafts/${guided.draft_id}`);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Änderungen prüfen' }) ?? screen.getByRole('button', { name: 'Übernehmen' })).toBeEnabled());
    const initialCheck = screen.queryByRole('button', { name: 'Änderungen prüfen' });
    if (initialCheck) await user.click(initialCheck);
    await waitFor(() => expect(screen.getAllByLabelText('Anzahl der Änderungen').map(node => node.textContent)).toEqual([`${objectCount} Änderungen`, `${objectCount} Änderungen`]), { timeout: 65_000 });
    const takeover = async () => {
      const details = screen.queryByRole('link', { name: 'Änderungen ansehen' }); if (details) await user.click(details);
      const check = screen.queryByRole('button', { name: 'Änderungen prüfen' }); if (check) await user.click(check);
      const apply = await screen.findByRole('button', { name: 'Übernehmen' });
      await clickReadyButton(user, apply, 'Übernehmen');
      const confirm = await screen.findByRole('dialog', { name: 'Konfiguration übernehmen' });
      const accept = await within(confirm).findByRole('checkbox', { name: /Auswirkungen geprüft/ }, { timeout: 65_000 });
      await waitFor(() => expect(accept).toBeEnabled(), { timeout: 65_000 });
      await user.click(accept);
      await clickReadyButton(user, within(confirm).getByRole('button', { name: 'Jetzt übernehmen' }), 'Jetzt übernehmen');
      await within(confirm).findByText(/Konfiguration \d+ ist übernommen/, {}, { timeout: 65_000 });
      await user.click(within(confirm).getAllByRole('button', { name: /Konfiguration ansehen|Schließen/ }).at(-1)!);
    };
    await takeover();
    expect((await raw<{ lifecycle: string }>(`registry/drafts/${guided.draft_id}`)).body.lifecycle).toBe('completed');
    expect(sessionStorage.getItem('terra.workingDraft')).toBeNull();
    const before = (await raw<JsonObject>('registry/active/export')).body;
    const sourceId = String(objects(before.sources)[0]!.source_id); const contractId = String(objects(before.contracts)[0]!.contract_id);
    await expectPublishedValue(contractId, { value: 21.5 });
    const history = (await raw<{ items: JsonObject[] }>(`history/query/observations?source_id=${encodeURIComponent(sourceId)}`)).body.items;
    expect(history.some(item => item.value_raw === '21.5' && item.value_normalized === 21.5 && item.normalized === true)).toBe(true);
    await f.go('#/sources/devices?item=0');
    // Activation invalidates cached source views asynchronously. Wait for this
    // flow's acknowledged source before invoking an action that captures its ID.
    await user.click(await screen.findByRole('button', { name: name => name.includes(sourceId) }, { timeout: 65_000 }));
    await user.click(await screen.findByRole('button', { name: 'Quelle mit anderem Typ anlegen' }, { timeout: 65_000 }));
    const moving = await screen.findByRole('dialog', { name: 'Quelle umziehen' });
    await user.selectOptions(within(moving).getByLabelText('Neue Werteart'), 'text');
    const currentMove = () => screen.getByRole('dialog', { name: 'Quelle umziehen' });
    await waitFor(() => {
      const current = currentMove();
      expect(within(current).getByLabelText('Neue Werteart')).toHaveValue('text');
      expect(within(current).getByRole('button', { name: 'Umzug prüfen' }), current.textContent ?? '').toBeEnabled();
    }, { timeout: 65_000 });
    await user.click(within(currentMove()).getByRole('button', { name: 'Umzug prüfen' }));
    await within(currentMove()).findByText(/Direkt umgestellte Verträge: 1/, {}, { timeout: 65_000 });
    await user.click(within(currentMove()).getByRole('button', { name: "Im Entwurf speichern" }));
    await takeover();
    const after = (await raw<JsonObject>('registry/active/export')).body;
    expect(objects(after.sources)).toHaveLength(2);
    expect(objects(after.sources)[0]).toEqual(objects(before.sources)[0]);
    expect(objects(after.bindings)).toEqual([expect.objectContaining({ source_id: objects(after.sources)[1]!.source_id, transform: expect.objectContaining({ target: 'text' }) })]);
    expect(objects(after.contracts)[0]).toMatchObject({ type_version: 2, inputs: [expect.objectContaining({ ref: objects(after.sources)[1]!.source_id })] });
    await expectPublishedValue(contractId, { value: '21.5', status: 'valid' });
    const retained = (await raw<{ items: JsonObject[] }>(`history/query/observations?source_id=${encodeURIComponent(sourceId)}`)).body.items;
    expect(retained).toEqual(expect.arrayContaining(history));
    expect(retained.every(item => item.value_raw === '21.5' && item.value_normalized === 21.5 && item.normalized === true)).toBe(true);
    await expectSameAsActive(after);
  }, 240_000);

  it('restarts an opted-in machine through confirmation and observes manual and then data-confirmed state over the real WebSocket', async () => {
    const bool = { type: 'bool', required: true, physical_state: false };
    let machine: JsonObject = { id: 'neustart', states: ['bereit', 'fertig'], initial: 'bereit', commands: {}, transitions: [], reinitialize_when: { op: 'not', args: [{ op: 'input', name: 'a' }] } };
    const definition: JsonObject = { type_id: 'test.manueller_neustart', type_version: 1, inputs: [{ name: 'a', schema: bool }], schema: { fields: { value: { type: 'enum', required: true, physical_state: false, value_catalog: ['bereit', 'fertig'] } }, available_projection: ['value'] }, outputs: { value: { op: 'machine' } }, machine };
    wrap(<Stateful initial={machine} onValue={next => { machine = next; }}>{(value, set) => <MachineEditor value={value} onChange={set} pointer="/definitions/0/machine" definition={definition} caps={undefined} locked={false} />}</Stateful>);
    const grant = screen.getByRole('checkbox', { name: 'Administratoren dürfen diesen Ablauf manuell neu starten' });
    expect(grant).not.toBeChecked(); await user.click(grant); cleanup();
    const originalSource = ux12.sources.find(source => source.source_type === 'boolean')!;
    const originalBinding = ux12.bindings.find(binding => binding.source_id === originalSource.source_id)!;
    const document: JsonObject = { config_schema_version: 1, fingerprint_version: 2,
      dependencies: [{ dependency_id: 'dep.neustart', kind: 'ha', monitor: 'internal' }],
      sources: [{ source_id: 'source.neustart', source_type: 'boolean', source_origin: 'local', dependency_id: 'dep.neustart', physical_source_key: 'physical.neustart', value_schema: bool, freshness: { mode: 'event_stateful', future_tolerance_s: 1 } }],
      bindings: [{ binding_id: 'binding.neustart', source_id: 'source.neustart', adapter: { kind: 'ha_state', ha_entity_id: originalBinding.adapter.ha_entity_id }, transform: { kind: 'type_cast', target: 'bool' } }],
      definitions: [{ ...definition, machine }], catalogs: [],
      contracts: [{ contract_id: 'manueller_neustart', display_name: 'Ablauf zum Neustart prüfen', type_id: definition.type_id!, type_version: 1, enabled: true, inputs: [{ name: 'a', ref: 'source.neustart' }], parameters: {} }],
    };
    expect((await raw<{ valid: boolean }>('registry/validate', json('POST', document))).body.valid).toBe(true);
    const active = (await raw<{ revision: number } | null>('registry/active')).body;
    const draft = (await raw<{ draft_id: string; draft_version: number }>('registry/drafts', json('POST', { config: document }))).body;
    const activated = await raw<{ revision: number }>(`registry/drafts/${draft.draft_id}/activate`, json('POST', await activationInput(draft, active?.revision ?? 0)));
    expect(activated.status).toBe(200);
    window.location.hash = '#/contracts/view/manueller_neustart';
    const seen: boolean[] = [];
    const unsubscribe = store.subscribe(() => { const state = store.getSnapshot().contracts?.manueller_neustart?.state_machine; if (state) seen.push(state.manual_restart === true); });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    const start = await screen.findByRole('button', { name: 'Ablauf neu starten' }, { timeout: 65_000 });
    await waitFor(() => expect(start).toBeEnabled(), { timeout: 65_000 }); await user.click(start);
    const confirmation = await screen.findByRole('dialog', { name: 'Ablauf neu starten' });
    expect(within(confirmation).getByText(/Externe Automationen sind Wuschel nicht vollständig bekannt/)).toBeVisible();
    await user.type(within(confirmation).getByLabelText('Warum startest du den Ablauf neu?'), 'Isolierter Engine-Test');
    await user.click(within(confirmation).getByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ }));
    await user.click(within(confirmation).getByRole('button', { name: 'Jetzt manuell neu starten' }));
    await within(confirmation).findByText(/Der Ablauf wurde manuell neu gestartet/, {}, { timeout: 65_000 });
    await waitFor(() => expect(seen).toContain(true), { timeout: 65_000 });
    await waitFor(() => {
      const envelope = store.getSnapshot().contracts?.manueller_neustart;
      expect(envelope?.state_machine?.manual_restart ?? false).toBe(false);
      expect(envelope?.state_machine?.origin).toBe('automatic');
      expect(envelope?.contract_health).toBe('healthy');
    }, { timeout: 65_000 });
    const history = (await raw<{ items: JsonObject[] }>('history/query/transitions?contract_id=manueller_neustart')).body.items;
    expect(history.some(item => item.manual_restart === true && item.origin === 'manual')).toBe(true);
    const audit = (await raw<{ items: JsonObject[] }>('history/query/diagnostics')).body.items.filter(item => item.operation === 'manual_restart' && item.contract_id === 'manueller_neustart');
    expect(audit).toHaveLength(1); expect(audit[0]).toMatchObject({ reason: 'Isolierter Engine-Test', origin: 'manual', actor: { role: 'admin', kind: 'ingress' } });
    unsubscribe();
  }, 180_000);

  it('b3 UX-1/2 continues independent edits, resolves real contradictions and replays one activation receipt', async () => {
    const head = (await raw<RevisionRow>('registry/active')).body;
    const local = structuredClone(head.config); const current = structuredClone(head.config);
    (local.sources as JsonObject[])[0]!.display_name = 'Synthetic draft source';
    (current.contracts as JsonObject[])[0]!.display_name = 'Synthetic active contract';
    const old = (await raw<Draft>('registry/drafts', json('POST', { config: local }))).body;
    const parallel = (await raw<Draft>('registry/drafts', json('POST', { config: current }))).body;
    expect((await raw(`registry/drafts/${parallel.draft_id}/activate`, json('POST', await activationInput(parallel, head.revision)))).status).toBe(200);
    const stale = await raw<{ error: string }>(`registry/drafts/${old.draft_id}/activation-preview`, json('POST', {}));
    expect(stale).toMatchObject({ status: 409, body: { error: 'draft_base_conflict' } });
    type Session = { session_id: string; session_version: number; status: string; conflicts: { path: string; digests: string[] }[] };
    const session = (await raw<Session>(`registry/drafts/${old.draft_id}/continuations`, json('POST', { draft_version: old.draft_version }))).body;
    expect(session.conflicts).toEqual([]);
    const finish = { session_version: session.session_version, request_id: session.session_id };
    const continued = (await raw<Draft>(`registry/continuations/${session.session_id}/finish`, json('POST', finish))).body;
    expect((continued.config.sources as JsonObject[])[0]!.display_name).toBe('Synthetic draft source');
    expect((continued.config.contracts as JsonObject[])[0]!.display_name).toBe('Synthetic active contract');
    expect((await raw<Draft>(`registry/continuations/${session.session_id}/finish`, json('POST', finish))).body).toEqual(continued);
    expect((await raw<Draft>(`registry/drafts/${old.draft_id}`)).body.lifecycle).toBe('continued');
    expect((await raw(`registry/drafts/${old.draft_id}`, json('PUT', { config: local, draft_version: old.draft_version }))).status).toBe(409);
    const request = await activationInput(continued, continued.base_revision!);
    const first = await raw<RevisionRow>(`registry/drafts/${continued.draft_id}/activate`, json('POST', request));
    expect(first.status).toBe(200);
    // The response may be lost: query its durable result and repeat exactly that envelope.
    expect((await raw<{ result: RevisionRow }>(`registry/activation-requests/${request.request_id}`)).body.result.revision).toBe(first.body.revision);
    expect((await raw(`registry/drafts/${continued.draft_id}/activate`, json('POST', request))).body).toEqual(first.body);
    const d = structuredClone(first.body.config); const c = structuredClone(first.body.config);
    (d.sources as JsonObject[])[0]!.display_name = 'Synthetic choice D';
    (c.sources as JsonObject[])[0]!.display_name = 'Synthetic choice C';
    const draft = (await raw<Draft>('registry/drafts', json('POST', { config: d }))).body;
    const newer = (await raw<Draft>('registry/drafts', json('POST', { config: c }))).body;
    const active = (await raw<RevisionRow>('registry/active')).body;
    expect((await raw(`registry/drafts/${newer.draft_id}/activate`, json('POST', await activationInput(newer, active.revision)))).status).toBe(200);
    const conflicted = (await raw<Session>(`registry/drafts/${draft.draft_id}/continuations`, json('POST', { draft_version: draft.draft_version }))).body;
    expect(conflicted.conflicts).toHaveLength(1);
    const row = conflicted.conflicts[0]!;
    const decided = (await raw<Session>(`registry/continuations/${conflicted.session_id}/decisions`, json('PUT', { session_version: conflicted.session_version, decisions: { [row.path]: { digests: row.digests, choice: 'draft' } } }))).body;
    expect(decided.conflicts).toEqual([]);
    const result = (await raw<Draft>(`registry/continuations/${decided.session_id}/finish`, json('POST', { session_version: decided.session_version, request_id: decided.session_id }))).body;
    expect((result.config.sources as JsonObject[])[0]!.display_name).toBe('Synthetic choice D');
    expect((await raw<RevisionRow>('registry/active')).body.config).toEqual(c);
  }, 120_000);

  it('b3 F5 and UX-5-11 compare retention on the server and complete an unchanged draft without a new revision', async () => {
    const active = (await raw<RevisionRow>('registry/active')).body;
    const draft = (await raw<Draft>('registry/drafts', json('POST', { config: active.config }))).body;
    const preview = (await raw<{ no_effect: boolean; retention: { changed: boolean } }>(`registry/drafts/${draft.draft_id}/activation-preview`, json('POST', {}))).body;
    expect(preview.no_effect).toBe(true); expect(preview.retention.changed).toBe(false);
    expect((await raw(`registry/drafts/${draft.draft_id}/complete`, json('POST', await activationInput(draft, active.revision)))).status).toBe(200);
    expect((await raw<RevisionRow>('registry/active')).body.revision).toBe(active.revision);
    expect((await raw<Draft>(`registry/drafts/${draft.draft_id}`)).body.lifecycle).toBe('completed');
    const retained = { ...active.config, history_retention: { raw_days: 7, rollup_days: 30 } };
    const shorter = (await raw<Draft>('registry/drafts', json('POST', { config: retained }))).body;
    const comparison = (await raw<{ retention: { changed: boolean; shortening: string[] }; required_confirmations: string[] }>(`registry/drafts/${shorter.draft_id}/activation-preview`, json('POST', {}))).body;
    expect(comparison.retention.changed).toBe(true);
    expect(comparison.retention.shortening).toEqual(['raw_days', 'rollup_days']);
    expect(comparison.required_confirmations).toContain('retention');
  }, 120_000);

  it('b4 UX-3/4 authors a shared contact template, confirms seven sources, preserves exact units and exports closed references', async () => {
    const config: JsonObject = { config_schema_version: 3, fingerprint_version: 3, dependencies: [{ dependency_id: 'dep.b4', kind: 'mqtt', monitor: 'internal' }], sources: [], bindings: [], contracts: [], catalogs: [] };
    const sources = config.sources as JsonObject[], bindings = config.bindings as JsonObject[], contracts = config.contracts as JsonObject[];
    for (let index = 0; index < 4; index++) {
      const inputs: JsonObject[] = [];
      for (const name of index < 3 ? ['open', 'tilt'] : ['contact']) {
        const id = `source.b4.${index}.${name}`;
        sources.push({ source_id: id, display_name: `Synthetic ${index} ${name}`, source_type: 'boolean', source_origin: 'local', dependency_id: 'dep.b4', physical_source_key: id, value_schema: { type: 'bool', required: true, physical_state: true }, freshness: { mode: 'report_heartbeat', interval_s: sources.length >= 5 ? 10800 : 8400, future_tolerance_s: 1 } });
        bindings.push({ binding_id: id, source_id: id, adapter: { kind: 'mqtt', mqtt_topic: `test/b4/${index}/${name}`, mqtt_value_path: 'value', mqtt_time_path: 'measured_at' } });
        inputs.push({ name, ref: id });
      }
      contracts.push({ contract_id: `test.b4.opening${index}`, type_id: index < 3 ? 'opening.dual_contact' : 'opening.single_contact', type_version: 1, enabled: true, inputs, parameters: { grace_s: 600 } });
    }
    for (let index = 0; index < 3; index++) contracts.push({ contract_id: `test.b4.zone${index}`, type_id: 'opening_zone.facts', type_version: 1, enabled: true, inputs: contracts.slice(0, 4).map((contract, i) => ({ name: `member_${i + 1}`, ref: `${String(contract.contract_id)}.value` })), parameters: {} });
    const staged = await raw<Draft>('registry/drafts', json('POST', { config }));
    expect(staged.status).toBe(200);
    const oldActive = (await raw<RevisionRow>('registry/active')).body;
    expect((await raw(`registry/drafts/${staged.body.draft_id}/activate`, json('POST', await activationInput(staged.body, oldActive?.revision ?? 0)))).status).toBe(200);
    const baseline = (await raw<RevisionRow>('registry/active')).body;
    // Other E2E flows deliberately leave several open drafts. Select this flow's
    // own current draft explicitly; do not rely on list order or auto-selection.
    const working = await raw<Draft>('registry/drafts', json('POST', { config: baseline.config, display_name: 'Synthetic b4 freshness' }));
    expect(working.status).toBe(200);
    sessionStorage.setItem('terra.workingDraft', working.body.draft_id);
    window.location.hash = '#/sources/freshness';
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    const heading = await screen.findByRole('heading', { name: 'Aktualität', level: 1 });
    // The remembered ID is not an acknowledgement that the async reload has
    // finished. Wait for the restored working document before starting an edit.
    await screen.findByText('Synthetic b4 freshness');
    const editTemplate = within(heading.closest('[data-page-header]') as HTMLElement).getByRole('button', { name: 'Bearbeiten' });
    await waitFor(() => expect(editTemplate).toBeEnabled());
    await user.click(editTemplate);
    let dialog = await screen.findByRole('dialog', { name: 'Kontaktsensoren' });
    const label = 'Wie oft meldet sich der Sensor spätestens?';
    expect(within(dialog).getByLabelText(label)).toHaveValue('3');
    await user.selectOptions(within(dialog).getByLabelText('Einheit'), 'minutes');
    expect(within(dialog).getByLabelText(label)).toHaveValue('180');
    await user.selectOptions(within(dialog).getByLabelText('Einheit'), 'hours');
    await user.clear(within(dialog).getByLabelText(label)); await user.type(within(dialog).getByLabelText(label), '1,5');
    await user.click(within(dialog).getByRole('button', { name: 'Im Entwurf speichern' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Kontaktsensoren' })).not.toBeInTheDocument());
    expect((await raw<RevisionRow>('registry/active')).body.checksum).toBe(baseline.checksum);
    await user.click(within(heading.closest('[data-page-header]') as HTMLElement).getByRole('button', { name: 'Bearbeiten' }));
    dialog = await screen.findByRole('dialog', { name: 'Kontaktsensoren' });
    expect(within(dialog).getByLabelText(label)).toHaveValue('90');
    await user.selectOptions(within(dialog).getByLabelText('Einheit'), 'hours');
    expect(within(dialog).getByLabelText(label)).toHaveValue('1,5');
    await user.clear(within(dialog).getByLabelText(label)); await user.type(within(dialog).getByLabelText(label), '3');
    await user.click(within(dialog).getByRole('button', { name: 'Im Entwurf speichern' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Kontaktsensoren' })).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Vorhandene Quellen zuordnen' }));
    dialog = await screen.findByRole('dialog', { name: 'Vorhandene Quellen zuordnen' });
    expect(within(dialog).getByLabelText('Sensortyp bestätigen')).toHaveValue('');
    await user.selectOptions(within(dialog).getByLabelText('Sensortyp bestätigen'), 'contact');
    await user.selectOptions(within(dialog).getByLabelText('Aktualität zuordnen'), 'freshness.contact');
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnung im Entwurf bestätigen' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Vorhandene Quellen zuordnen' })).not.toBeInTheDocument());
    await screen.findByText(/7 Quellen · 4 Verträge · 0 eigene Abweichungen/);
    const drafts = (await raw<Draft[]>('registry/drafts')).body;
    const draft = drafts.find(row => row.draft_id === working.body.draft_id)!;
    expect(draft.config.config_schema_version).toBe(4);
    const verdict = (await raw<{ diff: { effective_freshness_changes: string[] } }>('registry/validate', json('POST', draft.config))).body;
    expect(verdict.diff.effective_freshness_changes).toHaveLength(5);
    const confirmation = await f.activateOpenDraft(draft.draft_id);
    await within(confirmation).findByText(`Konfiguration ${baseline.revision + 1} ist übernommen.`);
    const activated = (await raw<RevisionRow>('registry/active')).body;
    expect((activated.config.sources as JsonObject[]).every(source => (source.freshness_selection as JsonObject).kind === 'shared')).toBe(true);
    const exported = (await raw<{ format_version: number; config: JsonObject; manifest: { object_digests: Record<string, string> } }>('portable/export', json('POST', { expected_active_revision: activated.revision, contract_id: 'test.b4.opening0' }))).body;
    expect(exported.format_version).toBe(2); expect(exported.config.sources).toHaveLength(2); expect(exported.config.freshness_templates).toHaveLength(1);
    expect(exported.manifest.object_digests['freshness_templates:freshness.contact']).toBeTruthy();
    await user.click(within(confirmation).getAllByRole('button', { name: /Konfiguration ansehen|Schließen/ }).at(-1)!);
    const target = (await raw<Draft>('registry/drafts', json('POST', { config: activated.config, display_name: 'Synthetic b4 target' }))).body;
    await f.go(`#/changes/drafts/${target.draft_id}`);
    await waitFor(() => expect(sessionStorage.getItem('terra.workingDraft')).toBe(target.draft_id));
    const initialImport = await raw('portable/preview', json('POST', { package: exported, target_draft_id: target.draft_id, target_draft_version: target.draft_version }));
    expect(initialImport.status, JSON.stringify(initialImport.body)).toBe(200);
    await f.go('#/import-export');
    const text = JSON.stringify(exported); const file = new File([text], 'synthetic-b4.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: async () => text });
    await user.upload(await screen.findByLabelText('Datei auswählen'), file);
    const importPreview = screen.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' });
    await waitFor(() => expect(importPreview).toBeEnabled());
    await user.click(importPreview);
    await screen.findByRole('heading', { name: 'Aktualität zuordnen' });
    await screen.findByText('2 bestehende Quellen bleiben unverändert');
    expect(screen.queryByLabelText('Aktualität zuordnen')).not.toBeInTheDocument();
    await screen.findByText('0 Quellen haben gegenüber der Datei eine andere wirksame Aktualitätsregel.');
    await user.click(screen.getByRole('button', { name: 'Als Entwurf einlesen' }));
    await waitFor(() => expect(window.location.hash).toBe(`#/changes/drafts/${target.draft_id}`));
    await screen.findByText('Im Entwurf gespeichert');
    const importedResponse = await raw<Draft>(`registry/drafts/${target.draft_id}`);
    expect(importedResponse.status).toBe(200);
    const imported = importedResponse.body;
    expect(imported.draft_version).toBe(2);
    const importedSources = imported.config.sources as JsonObject[];
    expect(importedSources).toHaveLength(7);
    for (const original of activated.config.sources as JsonObject[]) {
      expect(importedSources.find(source => source.source_id === original.source_id)?.freshness_selection).toEqual(original.freshness_selection);
    }
    expect((await raw<RevisionRow>('registry/active')).body.checksum).toBe(activated.checksum);
  }, 240_000);
  it('b12 previews a full configuration with forty unchanged organization rows, resolves conflicts and stops before staging', async () => {
    const field = { type: 'bool', required: true, physical_state: false };
    const shared = 'synthetic.b12.shared';
    const config: JsonObject = {
      config_schema_version: 4, fingerprint_version: 4,
      dependencies: [{ dependency_id: 'dep.b12', kind: 'mqtt', monitor: 'internal' }],
      sources: [{ source_id: 'source.b12', source_type: 'boolean', source_origin: 'local', dependency_id: 'dep.b12', physical_source_key: 'synthetic.b12', freshness_selection: { kind: 'own', rule: { mode: 'report_heartbeat', interval_s: 8400, future_tolerance_s: 0 } } }],
      bindings: [{ binding_id: 'binding.b12', source_id: 'source.b12', adapter: { kind: 'mqtt', mqtt_topic: 'test/b12/original' } }],
      contracts: Array.from({ length: 41 }, (_, i) => ({ contract_id: i === 0 ? shared : `synthetic.b12.existing.${i}`, type_id: shared, type_version: 1, enabled: true, inputs: [{ name: 'a', ref: 'source.b12' }], parameters: {} })),
      definitions: [{ type_id: shared, type_version: 1, inputs: [{ name: 'a', schema: field }], schema: { fields: { value: field } }, outputs: { value: { op: 'input', name: 'a' } } }],
      catalogs: [],
    };
    const previous = (await raw<RevisionRow>('registry/active')).body;
    const seed = await raw<Draft>('registry/drafts', json('POST', { config }));
    expect(seed.status).toBe(200);
    expect((await raw(`registry/drafts/${seed.body.draft_id}/activate`, json('POST', await activationInput(seed.body, previous?.revision ?? 0)))).status).toBe(200);
    const baseline = (await raw<RevisionRow>('registry/active')).body;
    const refs = [...(config.contracts as JsonObject[]).map(row => ({ kind: 'contract', id: String(row.contract_id) })), { kind: 'template', id: shared }];
    for (const object of refs) expect((await raw('editorial', json('PUT', { object, category: object.id === shared ? 'time' : 'other', room: { mode: 'none' }, expected_editorial_version: 0 }))).status).toBe(200);
    const incoming = structuredClone(config);
    (incoming.bindings as JsonObject[])[0]!.adapter = { kind: 'mqtt', mqtt_topic: 'test/b12/file' };
    (incoming.contracts as JsonObject[])[0]!.enabled = false;
    (incoming.contracts as JsonObject[]).push({ ...(config.contracts as JsonObject[])[1]!, contract_id: 'synthetic.b12.new' });
    const candidate = (await raw<Draft>('registry/drafts', json('POST', { config: incoming }))).body;
    expect((await raw('editorial', json('PUT', { object: { kind: 'contract', id: 'synthetic.b12.new' }, category: 'other', room: { mode: 'none' }, expected_editorial_version: 0, draft_id: candidate.draft_id, draft_version: candidate.draft_version }))).status).toBe(200);
    const exported = await raw<JsonObject>('portable/export', json('POST', { expected_active_revision: baseline.revision, draft_id: candidate.draft_id, draft_version: candidate.draft_version }));
    expect(exported.status).toBe(200);
    expect(exported.body.scope).toBe('configuration');
    for (const object of refs.filter(ref => ref.id === shared)) expect((await raw('editorial', json('PUT', { object, category: 'other', room: { mode: 'none' }, expected_editorial_version: 1 }))).status).toBe(200);
    const draftsBefore = (await raw<Draft[]>('registry/drafts')).body;
    const uiFetch = globalThis.fetch;
    const stageCalls: unknown[] = [];
    const previews: { status: number; body: { editorial_changed: Record<string, boolean>; unresolved: string[] } }[] = [];
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/portable/stage')) stageCalls.push(init);
      const response = await uiFetch(input, init);
      if (String(input).endsWith('/portable/preview')) previews.push({ status: response.status, body: await response.clone().json() });
      return response;
    });
    window.location.hash = '#/import-export';
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    const text = JSON.stringify(exported.body);
    const file = new File([text], 'synthetic-b12-full.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: async () => text });
    await user.upload(await screen.findByLabelText('Datei auswählen'), file);
    await clickReadyButton(user, screen.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' }), 'Datei prüfen und Vorschau anzeigen');
    await waitFor(() => expect(previews.length).toBeGreaterThan(0));
    expect(previews[0]!.status, JSON.stringify(previews[0]!.body)).toBe(200);
    expect(Object.values(previews[0]!.body.editorial_changed).filter(changed => !changed)).toHaveLength(40);
    await screen.findByText('40 Objekte behalten Kategorie und Raum');
    expect(screen.queryByText(/Vertrag: synthetic.b12.existing.1 ·/)).not.toBeInTheDocument();
    expect(screen.getByText(/Vertrag: synthetic.b12.shared/)).toBeVisible();
    expect(screen.getByText(/Vorlage: synthetic.b12.shared/)).toBeVisible();
    expect(screen.getByText(/Vertrag: synthetic.b12.new/)).toBeVisible();
    expect(screen.getAllByLabelText('Kategorie und Raum')).toHaveLength(2);
    const conflicts = screen.getByRole('region', { name: 'Konflikte (2)' });
    const stage = screen.getByRole('button', { name: 'Als Entwurf einlesen' });
    const recheck = screen.getByRole('button', { name: 'Vorschau neu prüfen' });
    expect(stage).toBeDisabled(); expect(recheck).toBeVisible();
    expect(recheck.parentElement).toBe(stage.parentElement);
    expect(recheck.parentElement).toHaveClass('sticky', 'bottom-0');
    const first = document.getElementById('import-conflict-0')!;
    first.scrollIntoView = vi.fn();
    // The asynchronously loaded active revision can trigger a fresh preview.
    // Its rows remain visible while the jump link waits for that acknowledgement.
    await user.click(await screen.findByRole('link', { name: '2 Konflikte noch nicht entschieden' }));
    expect(first.scrollIntoView).toHaveBeenCalled(); expect(first).toHaveFocus();
    for (const select of within(conflicts).getAllByLabelText('Umgang mit dem vorhandenen Objekt')) await user.selectOptions(select, 'file');
    await clickReadyButton(user, recheck, 'Vorschau neu prüfen');
    await waitFor(() => expect(stage).toBeEnabled());
    expect(screen.queryByRole('link', { name: /Konflikte noch nicht entschieden/ })).not.toBeInTheDocument();
    expect(stageCalls).toEqual([]);
    expect((await raw<Draft[]>('registry/drafts')).body).toEqual(draftsBefore);
    expect((await raw<RevisionRow>('registry/active')).body.checksum).toBe(baseline.checksum);
    cleanup();
  }, 240_000);
  it('b13 loads, checks and saves a browser-rounded published annual curve while the server retains its float bytes', async () => {
    const field = { type: 'number', required: true, physical_state: false };
    const config: JsonObject = {
      config_schema_version: 4, fingerprint_version: 4, dependencies: [], sources: [], bindings: [], catalogs: [],
      definitions: [{ type_id: 'synthetic.b13.curve', type_version: 1, language_version: 5, inputs: [], schema: { fields: { value: field }, available_projection: ['value'] }, outputs: { value: { op: 'annual_curve', at: { op: 'literal', value: '2030-01-01', schema: { type: 'text', required: true, physical_state: false } }, points: [{ day: '01-01', value: 840.5 }, { day: '07-01', value: 900 }] } } }],
      contracts: [{ contract_id: 'synthetic.b13.value', type_id: 'synthetic.b13.curve', type_version: 1, enabled: true, inputs: [], parameters: {} }],
    };
    const head = (await raw<RevisionRow>('registry/active')).body;
    // Seed an actual JSON float token. The normal JS fixture helper would erase .0.
    const seedText = JSON.stringify({ config, expected_active_revision: head?.revision ?? 0 }).replace('"value":840.5', '"value":840.0');
    await reserveRequest();
    const seedResponse = await realFetch(`${base}/api/v1/registry/drafts`, { ...json('POST', {}), body: seedText });
    expect(seedResponse.status).toBe(200);
    const seededText = await seedResponse.text();
    expect(seededText).toMatch(/"value"\s*:\s*840\.0[,}]/);
    const seed = JSON.parse(seededText) as Draft;
    expect((await raw(`registry/drafts/${seed.draft_id}/activate`, json('POST', await activationInput(seed, head?.revision ?? 0)))).status).toBe(200);
    const active = (await raw<RevisionRow>('registry/active')).body;
    const working = (await raw<Draft>('registry/drafts', json('POST', { config: active.config, display_name: 'Synthetic b13 browser' }))).body;
    sessionStorage.setItem('terra.workingDraft', working.draft_id);
    window.location.hash = `#/changes/drafts/${working.draft_id}`;
    const uiFetch = globalThis.fetch;
    const saves: string[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith(`/registry/drafts/${working.draft_id}`) && init?.method === 'PUT') saves.push(String(init.body));
      return uiFetch(input, init);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    await screen.findByDisplayValue('Synthetic b13 browser');
    // The draft page validates automatically on load; await its real API result.
    await screen.findByText('Keine Fehler gefunden');
    await f.type(screen.getByLabelText('Name des Entwurfs'), 'Synthetic b13 saved');
    await clickReadyButton(user, await f.saveButton(), 'Speichern');
    await screen.findByText('Im Entwurf gespeichert');
    expect(saves).toHaveLength(1);
    expect(saves[0]).toMatch(/"value":840[,}]/);
    await reserveRequest();
    const savedResponse = await realFetch(`${base}/api/v1/registry/drafts/${working.draft_id}`);
    expect(savedResponse.status).toBe(200);
    expect(await savedResponse.text()).toMatch(/"value"\s*:\s*840\.0[,}]/);
    await screen.findByText('Keine Fehler gefunden');
    expect((await raw<RevisionRow>('registry/active')).body.checksum).toBe(active.checksum);
    cleanup();
  }, 240_000);
  it('b14 shows concise German sleep/wake values, keeps all details and previews a name-only correction', async () => {
    const previous = (await raw<RevisionRow>('registry/active')).body;
    const seed = (await raw<Draft>('registry/drafts', json('POST', { config: b14Registry }))).body;
    expect((await raw(`registry/drafts/${seed.draft_id}/activate`, json('POST', await activationInput(seed, previous.revision)))).status).toBe(200);
    const active = (await raw<RevisionRow>('registry/active')).body;
    const uiFetch = globalThis.fetch;
    const stageCalls: unknown[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/portable/stage')) stageCalls.push(init);
      return uiFetch(input, init);
    });
    window.location.hash = '#/contracts/list/all';
    store.start();
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={store} /></QueryClientProvider>);
    const table = await screen.findByRole('table', { name: 'Verträge' });
    const sleepLink = await within(table).findByRole('link', { name: 'Schlafzustand' });
    const sleepRow = sleepLink.closest('tr')!;
    await waitFor(() => expect(sleepRow).toHaveTextContent('Zustand: Vor dem Schlaf'));
    expect(sleepRow).not.toHaveTextContent(b14Episode);
    const internal = within(table).getByRole('link', { name: 'Schlafablauf (intern)' }).closest('tr')!;
    expect(internal).not.toHaveTextContent(b14Episode);
    await user.click(sleepLink);
    await screen.findByRole('heading', { name: 'Wert jetzt' });
    expect(screen.getByTestId('value-episode_id')).toHaveTextContent(b14Episode);
    expect(screen.getByTestId('value-entry_origin')).toHaveTextContent('Zu Beginn');
    expect(screen.getByTestId('value-last_action_origin')).toHaveTextContent('Bestätigt');
    await f.go('#/contracts/list/all');
    const wakeLink = await screen.findByRole('link', { name: 'Weckplan' });
    await waitFor(() => expect(wakeLink.closest('tr')).toHaveTextContent('Plan heute: Geplant'));
    await user.click(wakeLink);
    await screen.findByRole('heading', { name: 'Wert jetzt' });
    expect(screen.getByTestId('value-decision')).toHaveTextContent('Weckregel');
    expect(screen.getByTestId('value-minimum_sleep_applied')).toHaveTextContent('Nein');

    const corrected = structuredClone(active.config);
    (corrected.contracts as JsonObject[]).find(row => row.contract_id === 'synthetic.b14.state')!.display_name = 'Neuer Schlafzustand';
    const draft = (await raw<Draft>('registry/drafts', json('POST', { config: corrected }))).body;
    const exported = await raw<JsonObject>('portable/export', json('POST', { expected_active_revision: active.revision, draft_id: draft.draft_id, draft_version: draft.draft_version }));
    expect(exported.status).toBe(200);
    await f.go('#/import-export');
    const text = JSON.stringify(exported.body);
    const file = new File([text], 'synthetic-b14-names.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: async () => text });
    await user.upload(await screen.findByLabelText('Datei auswählen'), file);
    await clickReadyButton(user, screen.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' }), 'Datei prüfen und Vorschau anzeigen');
    await screen.findByText('„Schlafzustand“ wird zu „Neuer Schlafzustand“');
    expect(screen.queryByRole('region', { name: /Konflikte/ })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Als Entwurf einlesen' })).toBeEnabled());
    expect(stageCalls).toEqual([]);
    expect((await raw<RevisionRow>('registry/active')).body.checksum).toBe(active.checksum);
    cleanup();
  }, 240_000);
});
