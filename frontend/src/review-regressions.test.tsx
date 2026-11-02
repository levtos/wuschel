import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { DraftPicker, SwitchDialog } from '@/features/authoring/DraftBar';
import { useWorkingDocument, WorkingDocumentProvider } from '@/features/authoring/WorkingDocument';
import { ActivateDialog } from '@/features/registry/ActivationDialogs';
import { ExplainPanel } from '@/features/diagnostics/ExplainPanel';
import { SchemaForm } from '@/editors/SchemaForm';
import { BindingEditor } from '@/editors/RegistryObjects';
import type { Draft } from '@/lib/types';
import type { Json, JsonObject } from '@/lib/json';
import { delayedBody, installFakeApi, makeDraft, reply, sampleConfig, type FakeApi } from '../test/fakeApi';
import { caps, idleLive, Stateful, wrap } from '../test/harness';

const user = userEvent.setup();
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
function Editor() {
  const doc = useWorkingDocument();
  return <>
    <button onClick={() => doc.setAt('/marker', 'local')}>Edit</button>
    <button onClick={() => void doc.save()}>Save</button>
    <button onClick={doc.undo}>Undo</button>
    <output data-testid="document">{JSON.stringify({ id: doc.draft?.draft_id, version: doc.draft?.draft_version, config: doc.config, dirty: doc.dirty })}</output>
    <DraftPicker open onOpenChange={() => {}} />
    <SwitchDialog />
  </>;
}
function editing(api: FakeApi) {
  void api;
  wrap(<WorkingDocumentProvider validateDebounceMs={3_600_000}><Editor /></WorkingDocumentProvider>, { live: idleLive() });
}
const doc = () => JSON.parse(screen.getByTestId('document').textContent!);

describe('late responses never replace another working document', () => {
  it('preserves B and its undo history when the save of A arrives after a confirmed switch', async () => {
    const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig()), makeDraft('b', sampleConfig())] });
    const pending = deferred<Response>();
    api.routes.push({ match: /^registry\/drafts\/a$/, method: 'PUT', respond: () => pending.promise });
    editing(api);
    await user.click(await screen.findByRole('button', { name: /^a/ }));
    await user.click(screen.getByText('Edit'));
    await user.click(screen.getByText('Save'));
    await user.click(screen.getByRole('button', { name: /^b/ }));
    await user.click(within(await screen.findByRole('dialog', { name: 'Entwurf wechseln' })).getByRole('button', { name: 'Ungespeicherte Eingaben verwerfen' }));
    await user.click(screen.getByText('Edit'));
    await act(async () => pending.resolve(reply(200, makeDraft('a', sampleConfig({ marker: 'local' }), { draft_version: 2 }))));
    expect(doc()).toMatchObject({ id: 'b', version: 1, dirty: true, config: { marker: 'local' } });
    await user.click(screen.getByText('Undo'));
    expect(doc()).toMatchObject({ id: 'b', dirty: false });
    expect(api.calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  });

  it('keeps edits made while saving and advances only their saved base', async () => {
    const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig())] });
    const pending = deferred<Response>();
    api.routes.push({ match: /^registry\/drafts\/a$/, method: 'PUT', respond: () => pending.promise });
    editing(api);
    await user.click(await screen.findByRole('button', { name: /^a/ }));
    await user.click(screen.getByText('Save'));
    await user.click(screen.getByText('Edit'));
    await user.click(screen.getByText('Save')); // concurrent saves are suppressed
    await act(async () => pending.resolve(reply(200, makeDraft('a', sampleConfig(), { draft_version: 2 }))));
    expect(doc()).toMatchObject({ id: 'a', version: 2, dirty: true, config: { marker: 'local' } });
    expect(api.calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  });
});

describe('activation checks the exact immutable candidate and base', () => {
  it('rejects a draft changed on the server during validation, even without a prop change', async () => {
    const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig())] });
    const pending = deferred<Response>();
    api.routes.push({ match: /\/validate$/, respond: () => pending.promise });
    wrap(<ActivateDialog draft={api.drafts.get('a') as unknown as Draft} open onOpenChange={() => {}} />, { live: idleLive() });
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith('/validate'))).toBe(true));
    api.touchDraft('a', { config: sampleConfig({ marker: 'new candidate' }) });
    await act(async () => pending.resolve(reply(200, { valid: true, issues: [] })));
    await screen.findByText('Prüfergebnis veraltet');
    expect(screen.getByRole('button', { name: "Jetzt übernehmen" })).toBeDisabled();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(api.calls.some((c) => c.path.endsWith('/activate'))).toBe(false);
  });

  it('rejects a base revision changed while validation is in flight', async () => {
    const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig())] });
    const pending = deferred<Response>();
    api.routes.push({ match: /\/validate$/, respond: () => pending.promise });
    wrap(<ActivateDialog draft={api.drafts.get('a') as unknown as Draft} open onOpenChange={() => {}} />, { live: idleLive() });
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith('/validate'))).toBe(true));
    api.revisions.push({ revision: 1, config: sampleConfig(), checksum: 'a'.repeat(64), created_at: 'x' });
    await act(async () => pending.resolve(reply(200, { valid: true, issues: [] })));
    await screen.findByText('Dein Entwurf braucht den aktuellen Stand.');
    expect(screen.getByRole('button', { name: "Jetzt übernehmen" })).toBeDisabled();
  });

  it('discards A’s delayed validation after the same dialog switches to B', async () => {
    const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig()), makeDraft('b', sampleConfig())] });
    const pending = deferred<Response>();
    api.routes.push({ match: /^registry\/drafts\/a\/validate$/, respond: () => delayedBody(pending.promise) });
    function Switch() {
      const [id, setId] = useState('a');
      return <><button onClick={() => setId('b')}>Switch</button><ActivateDialog draft={api.drafts.get(id) as unknown as Draft} open onOpenChange={() => {}} /></>;
    }
    wrap(<Switch />, { live: idleLive() });
    await waitFor(() => expect(api.calls.some((c) => c.path === 'registry/drafts/a/validate')).toBe(true));
    await user.click(screen.getByText('Switch'));
    await screen.findByRole('checkbox', { name: /Auswirkungen geprüft/ });
    await act(async () => pending.resolve(reply(200, { valid: false, issues: [] })));
    await user.click(screen.getByRole('checkbox', { name: /Auswirkungen geprüft/ }));
    await user.click(screen.getByRole('button', { name: "Jetzt übernehmen" }));
    await screen.findByText("Konfiguration 1 ist übernommen.");
    expect(api.calls.filter((c) => c.path.endsWith('/activate')).map((c) => c.path)).toEqual(['registry/drafts/b/activate']);
  });
});

describe('schemas outside the fixtures keep actual JSON types', () => {
  it('keeps transform metadata when its graphical kind changes', async () => {
    let value: JsonObject = { binding_id: 'b', source_id: 's', adapter: { kind: 'mqtt', mqtt_topic: 'synthetic' }, transform: { kind: 'type_cast', target: 'number', x_future: [null, { id: 'keep' }] } };
    wrap(<Stateful initial={value} onValue={(next) => { value = next; }}>{(v, set) => <BindingEditor value={v} onChange={set} pointer="/bindings/0" sourceIds={['s']} catalogIds={[]} schema={caps.schemas.registry} onPickEntity={() => {}} />}</Stateful>);
    await user.selectOptions(screen.getByLabelText("Umwandlung"), 'unit_scale');
    expect(value.transform).toMatchObject({ kind: 'unit_scale', x_future: [null, { id: 'keep' }] });
  });

  it('keeps recursive allOf references bounded and still renders declared properties', async () => {
    const root: JsonObject = { $defs: { Recursive: { type: 'object', properties: { name: { type: 'string' } }, allOf: [{ $ref: '#/$defs/Recursive', title: 'Recursive' }] } } };
    let value: JsonObject = { name: 'original', x: [null] };
    wrap(<Stateful initial={value} onValue={(next) => { value = next; }}>{(v, set) => <SchemaForm root={root} node={{ $ref: '#/$defs/Recursive' }} value={v} onChange={(n) => set(n as JsonObject)} pointer="/object" />}</Stateful>);
    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'edited');
    expect(value).toEqual({ name: 'edited', x: [null] });
  });
  it('distinguishes colliding enum labels, including structured members and null', async () => {
    let current: Json | undefined;
    function Field() {
      const [value, set] = useState<Json>(1);
      current = value;
      return <SchemaForm root={{}} node={{ enum: [1, '1', null, { nested: [false] }] }} value={value} onChange={(v) => set(v!)} pointer="/value" label="Choice" />;
    }
    wrap(<Field />);
    await user.selectOptions(screen.getByLabelText('Choice'), screen.getByLabelText('Choice').querySelector<HTMLOptionElement>('option[value="1"]')!);
    expect(current).toBe('1');
    await user.selectOptions(screen.getByLabelText('Choice'), '2');
    expect(current).toBeNull();
    await user.selectOptions(screen.getByLabelText('Choice'), '3');
    expect(current).toEqual({ nested: [false] });
  });

  it('edits chained refs, allOf fields and implicitly open objects without dropping unknown members', async () => {
    const schema: JsonObject = { $defs: { Alias: { $ref: '#/$defs/Object' }, Object: { allOf: [{ type: 'object', properties: { name: { type: 'string' } } }, { type: 'object', properties: { count: { type: 'integer' } }, required: ['count'] }] } } };
    let current: JsonObject = { name: 'old', count: 1, future: { ids: ['stable'] } };
    wrap(<Stateful initial={current} onValue={(v) => { current = v; }}>{(value, set) => <SchemaForm root={schema} node={{ $ref: '#/$defs/Alias' }} value={value} onChange={(v) => set(v as JsonObject)} pointer="/object" label="Object" />}</Stateful>);
    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'new');
    await user.click(screen.getByRole('button', { name: 'Eigenschaft' }));
    expect(current).toEqual({ name: 'new', count: 1, future: { ids: ['stable'] }, schluessel2: '' });
  });

  it('offers boolean and null separately for a nullable type array', async () => {
    function Field() {
      const [value, set] = useState<Json>(null);
      return <><SchemaForm root={{}} node={{ type: ['boolean', 'null'] }} value={value} onChange={(v) => set(v!)} pointer="/b" label="Nullable" /><output data-testid="nullable">{JSON.stringify(value)}</output></>;
    }
    wrap(<Field />);
    await user.click(screen.getByRole('button', { name: 'Wert festlegen' }));
    expect(screen.getByTestId('nullable')).toHaveTextContent('false');
    await user.click(screen.getByRole('checkbox', { name: 'Nullable' }));
    expect(screen.getByTestId('nullable')).toHaveTextContent('true');
  });
});

it('renders incomplete explain data without crashing or inventing a fusion decision', () => {
  wrap(<ExplainPanel trace={{ steps: [{ op: 'input', status: 'valid' }, { op: 'fusion_selection' }] }} />);
  expect(screen.getByText('Qualität nicht aufgezeichnet')).toBeInTheDocument();
  expect(screen.getByText('Auswahl nicht aufgezeichnet')).toBeInTheDocument();
  expect(screen.getByText(/Gewählt: nicht aufgezeichnet/)).toBeInTheDocument();
});
