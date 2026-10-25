import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { newDefinition, newFieldSchema } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';
import { caps, Stateful, wrap } from '../../test/harness';
import { CatalogEditor } from './RegistryObjects';
import { DefinitionEditor } from './DefinitionEditor';

const user = userEvent.setup();
const node = (p: string) => document.querySelector(`[data-pointer="${p}"]`) as HTMLElement;
const base = '/definitions/0';
const root = `${base}/outputs/value`;

function mount(definition: JsonObject, locked = false) {
  let current = definition;
  wrap(<Stateful initial={definition} onValue={(v) => (current = v)}>{(v, set) => <DefinitionEditor value={v} onChange={set} pointer={base} caps={caps} locked={locked} />}</Stateful>);
  return () => current;
}
const withEnumInput = (): JsonObject => ({ ...newDefinition('test.x', 1), inputs: [{ name: 'mode', schema: { ...newFieldSchema('enum'), value_catalog: ['on', 'off'] } }] });

describe('operators beyond the example', () => {
  it('offers first_usable in German and keeps the argument-order hint', async () => {
    const result = mount({ ...newDefinition('test.priority', 1), outputs: { value: { op: 'fusion', strategy: 'first_healthy', args: [{ op: 'input', name: 'a' }] } } });
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    const strategy = within(node(root)).getByRole('combobox', { name: /^Strategie/ });
    await user.selectOptions(strategy, 'first_usable');
    expect(within(strategy).getByRole('option', { name: 'Erster verwertbarer Wert (Reihenfolge)' })).toHaveProperty('selected', true);
    expect((result().outputs as JsonObject).value).toMatchObject({ strategy: 'first_usable' });
    expect(screen.getByText('Die Reihenfolge der Argumente ist die Priorität.')).toBeInTheDocument();
  });

  it('preserves unknown members and a stateful node ID when changing the operator graphically', async () => {
    const result = mount({ ...newDefinition('test.preserve', 1), outputs: { value: { op: 'bucket', id: 'stable-node', initial: false, lower: 0, upper: 1, arg: { op: 'input', name: 'a' }, x_future: { keep: [null, 1] } } } });
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    await user.selectOptions(within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!, 'temporal');
    expect((result().outputs as JsonObject).value).toMatchObject({ op: 'temporal', id: 'stable-node', x_future: { keep: [null, 1] } });
  });
  it('map: keys follow the input type (catalog values are offered and stored as canonical JSON strings)', async () => {
    const result = mount({ ...withEnumInput(), outputs: { value: { op: 'input', name: 'mode' } } });
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    await user.selectOptions(within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!, 'map');
    const out = (result().outputs as JsonObject).value as JsonObject;
    expect(out.op).toBe('map');
    // seeded entry is converted by the user to the catalog value
    await user.selectOptions(within(node(root)).getAllByLabelText('Wenn Eingang =')[0]!, '"on"');
    expect(Object.keys(((result().outputs as JsonObject).value as JsonObject).entries as JsonObject)).toEqual(['"on"']);
    await user.click(within(node(root)).getByRole('button', { name: 'Eintrag' }));
    expect(Object.keys(((result().outputs as JsonObject).value as JsonObject).entries as JsonObject)).toEqual(['"on"', '"off"']);
  });

  it('first_match, bucket and not_applicable get their own parameters', async () => {
    const result = mount(newDefinition('test.y', 1));
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    const select = () => within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!;
    await user.selectOptions(select(), 'first_match');
    await user.click(within(node(root)).getByRole('button', { name: 'Fall' }));
    expect((((result().outputs as JsonObject).value as JsonObject).cases as unknown[]).length).toBe(2);
    await user.selectOptions(select(), 'bucket');
    expect((result().outputs as JsonObject).value).toMatchObject({ op: 'bucket', id: 'bucket', initial: false, lower: 0, upper: 1 });
    await user.selectOptions(select(), 'not_applicable');
    expect((result().outputs as JsonObject).value).toMatchObject({ op: 'not_applicable', arg: { op: 'input', name: 'a' } });
  });

  it('stateful ids are proposed uniquely', async () => {
    const result = mount(newDefinition('test.z', 1));
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    const select = within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!;
    await user.selectOptions(select, 'and');
    await user.selectOptions(within(node(`${root}/args/0`)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!, 'bucket');
    await user.selectOptions(within(node(`${root}/args/1`)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!, 'bucket');
    const ids = (((result().outputs as JsonObject).value as JsonObject).args as JsonObject[]).map((a) => a.id);
    expect(new Set(ids).size).toBe(2);
  });

  it('an unknown operator is shown and preserved, never dropped', async () => {
    const result = mount({ ...newDefinition('test.u', 1), outputs: { value: { op: 'future_op', x: 1 } } });
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    expect(within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]).toHaveValue('future_op');
    expect(screen.getByText("Unbekannter Rechenschritt – Daten unter Technische Details prüfen")).toBeInTheDocument();
    expect((result().outputs as JsonObject).value).toEqual({ op: 'future_op', x: 1 });
  });

  it('stateless guards cannot select stateful operators', async () => {
    const def = newDefinition('test.g', 1);
    def.machine = { id: 'm', states: ['a', 'b'], initial: 'a', sessions: false, commands: {}, transitions: [{ source: 'a', target: 'b', event: 'input', guard: { op: 'input', name: 'a' }, reason: 'r' }], reinitialize_when: { op: 'input', name: 'a' } };
    mount(def);
    await user.click(screen.getByRole('tab', { name: /Ablauf/ }));
    const guard = within(node(`${base}/machine/transitions/0/guard`)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!;
    const options = [...guard.querySelectorAll('option')].map((o) => o.value);
    expect(options).not.toContain('temporal');
    expect(options).not.toContain('bucket');
    expect(options).not.toContain('machine');
    expect(options).toContain('compare');
  });

  it('locked (activated) definitions cannot change logic', async () => {
    mount(newDefinition('test.l', 1), true);
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    expect(within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]).toBeDisabled();
  });
});

describe('catalog editor', () => {
  it('edits keys and values of every JSON type without JSON text', async () => {
    let current: JsonObject = { catalog_id: 'catalog.neu', kind: 'map', version: 1, entries: { an: true } };
    wrap(<Stateful initial={current} onValue={(v) => (current = v)}>{(v, set) => <CatalogEditor value={v} onChange={set} pointer="/catalogs/0" />}</Stateful>);
    await user.clear(screen.getByLabelText("Kennung der Werteliste"));
    await user.type(screen.getByLabelText("Kennung der Werteliste"), 'catalog.modus');
    await user.selectOptions(screen.getByLabelText('Wert für an: Typ'), 'string');
    await user.clear(screen.getByLabelText('Wert für an'));
    await user.type(screen.getByLabelText('Wert für an'), 'ein');
    expect(current).toEqual({ catalog_id: 'catalog.modus', kind: 'map', version: 1, entries: { an: 'ein' } });
    // structured values keep their type
    await user.selectOptions(screen.getByLabelText('Wert für an: Typ'), 'object');
    await user.click(screen.getByRole('button', { name: 'Wert für an: Eigenschaft hinzufügen' }));
    expect(current.entries).toEqual({ an: { schluessel1: '' } });
    await user.selectOptions(screen.getByLabelText('Wert für an: Typ'), 'array');
    expect(current.entries).toEqual({ an: [{ schluessel1: '' }] }); // the structure is kept, wrapped in a list
    await user.selectOptions(screen.getByLabelText('Wert für an: Typ'), 'null');
    expect(current.entries).toEqual({ an: null });
  });

  it('shows imported object, array and null values with their real type', () => {
    wrap(<CatalogEditor value={{ catalog_id: 'c', kind: 'map', version: 1, entries: { o: { fixture: true }, l: [1, 'x'], n: null } }} onChange={() => {}} pointer="/catalogs/0" />);
    expect(screen.getByLabelText('Wert für o: Typ')).toHaveValue('object');
    expect(screen.getByLabelText('Wert für l: Typ')).toHaveValue('array');
    expect(screen.getByLabelText('Wert für n: Typ')).toHaveValue('null');
    expect(document.body.textContent).not.toContain('[object Object]');
    expect(screen.getByLabelText('Wert für o.fixture')).toHaveValue('true');
  });
});
