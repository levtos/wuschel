import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { newDefinition } from '@/features/authoring/model';
import type { Capabilities } from '@/lib/capabilities';
import { clone, type Json, type JsonObject } from '@/lib/json';
import { caps, Stateful, wrap } from '../../test/harness';
import { DefinitionEditor } from './DefinitionEditor';
import { SchemaForm } from './SchemaForm';

/**
 * T1B-06: the workbench interprets whatever the server's capability metadata
 * declares. These tests vary that metadata synthetically (they do NOT claim that
 * such operators exist in Alpha 4) and prove nothing is hidden, forced into JSON
 * or silently dropped.
 */
const user = userEvent.setup();
const base = '/definitions/0';
const root = `${base}/outputs/value`;
const node = (p: string) => document.querySelector(`[data-pointer="${p}"]`) as HTMLElement;

it('translates known string enums on any field and keeps raw values for saving', async () => {
  const schema = { enum: ['phase', 'time', 'early_night', 'test_custom', 'constructor'] };
  let saved: Json | undefined;
  wrap(<SchemaForm root={schema} node={schema} pointer="/settings/start_choice" label="Beginn wählen" value="phase" onChange={value => { saved = value; }} />);
  const field = screen.getByRole('combobox', { name: 'Beginn wählen' });
  expect(within(field).getByRole('option', { name: 'Tagesphase' })).toHaveValue('phase');
  expect(within(field).getByRole('option', { name: 'Uhrzeit' })).toHaveValue('time');
  expect(within(field).getByRole('option', { name: 'frühe Nacht' })).toHaveValue('early_night');
  expect(within(field).getByRole('option', { name: 'test_custom' })).toHaveValue('test_custom');
  expect(within(field).getByRole('option', { name: 'constructor' })).toHaveValue('constructor');
  await user.selectOptions(field, 'time');
  expect(saved).toBe('time');
});

function variant(mutate: (c: Capabilities) => void): Capabilities {
  const next = clone(caps) as Capabilities;
  mutate(next);
  return next;
}
const defs = (c: Capabilities, name: string, schema: JsonObject) => {
  (c.schemas.definition.$defs as JsonObject)[name] = schema;
  (c.schemas.registry.$defs as JsonObject)[name] = schema;
};

function mount(definition: JsonObject, capabilities: Capabilities) {
  let current = definition;
  wrap(<Stateful initial={definition} onValue={(v) => (current = v)}>{(v, set) => <DefinitionEditor value={v} onChange={set} pointer={base} caps={capabilities} locked={false} />}</Stateful>, { caps: capabilities });
  return () => current;
}
const outputValue = (d: JsonObject) => (d.outputs as JsonObject).value as JsonObject;

describe('operators that only exist in the metadata', () => {
  const synthetic = variant((c) => {
    c.operators.push({ op: 'x_scale', stateful: false, schema_ref: '#/$defs/XScaleRule' });
    defs(c, 'XScaleRule', {
      type: 'object',
      title: 'XScaleRule',
      required: ['op', 'arg', 'factor'],
      additionalProperties: false,
      properties: {
        op: { const: 'x_scale', type: 'string' },
        arg: clone((c.schemas.definition.$defs as { CompareRule: { properties: { left: Json } } }).CompareRule.properties.left),
        factor: { anyOf: [{ type: 'string' }, { type: 'number' }], title: 'Factor' },
        meta: { type: 'object', additionalProperties: true, title: 'Meta' },
        mode: { title: 'Mode', anyOf: [{ const: 'plain' }, { type: 'object', title: 'Windowed', properties: { kind: { const: 'windowed' }, depth: { type: 'integer', minimum: 1 } }, required: ['kind', 'depth'] }] },
        tags: { type: 'array', items: { type: 'string' }, title: 'Tags' },
      },
    });
  });

  it('lets a string-or-number union, an open object, a variant union and a list be set graphically', async () => {
    const result = mount({ ...newDefinition('test.s', 1), outputs: { value: { op: 'x_scale', arg: { op: 'input', name: 'a' }, factor: 2 } } }, synthetic);
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    expect(within(node(root)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]).toHaveValue('x_scale');
    // no raw-JSON detour for any of these
    expect(within(node(root)).queryAllByRole('textbox', { name: /JSON/ })).toHaveLength(0);

    const factor = within(node(root)).getByLabelText('Faktor *') as HTMLInputElement;
    expect(factor.type).toBe('number'); // current value is numeric, the string|number union shows a numeric control
    await user.clear(factor);
    await user.type(factor, '3');
    expect(outputValue(result()).factor).toBe(3);

    // open object: typed key/value rows, no JSON
    await user.click(within(node(root)).getByRole('button', { name: 'Eigenschaft' }));
    expect(outputValue(result()).meta).toEqual({ schluessel1: '' });
    // list of strings
    await user.click(within(node(root)).getByRole('button', { name: 'Eintrag' }));
    expect(outputValue(result()).tags).toEqual(['']);
  });

  it('variant union: switching branches builds that branch graphically', async () => {
    const result = mount({ ...newDefinition('test.s', 1), outputs: { value: { op: 'x_scale', arg: { op: 'input', name: 'a' }, factor: 1, mode: 'plain' } } }, synthetic);
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    const picker = within(node(root)).getByLabelText('Modus: Variante');
    await user.selectOptions(picker, 'Windowed');
    expect(outputValue(result()).mode).toEqual({ kind: 'windowed', depth: 1 });
    const depth = within(node(root)).getByLabelText('depth *') as HTMLInputElement;
    await user.clear(depth);
    await user.type(depth, '4');
    expect(outputValue(result()).mode).toEqual({ kind: 'windowed', depth: 4 });
  });

  it('keeps the rule child editable through the recursive editor and preserves unknown members', async () => {
    const result = mount({ ...newDefinition('test.s', 1), outputs: { value: { op: 'x_scale', arg: { op: 'input', name: 'a' }, factor: 1, x_future: { keep: ['me'] } } } }, synthetic);
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    await user.selectOptions(within(node(`${root}/arg`)).getByLabelText('Eingang'), 'a');
    expect(outputValue(result()).x_future).toEqual({ keep: ['me'] });
  });
});

describe('new schema properties on known operators and types are not lost or hidden', () => {
  it('shows a newly declared optional property of an existing operator and edits it', async () => {
    const extended = variant((c) => {
      for (const schema of [c.schemas.definition, c.schemas.registry]) {
        ((schema.$defs as JsonObject).CompareRule as { properties: JsonObject }).properties.tolerance = { type: 'number', title: 'Tolerance' };
      }
    });
    const compare = { op: 'compare', comparison: 'gt', left: { op: 'input', name: 'a' }, right: { op: 'input', name: 'a' }, tolerance: 0.5 };
    const result = mount({ ...newDefinition('test.c', 1), outputs: { value: compare } }, extended);
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    const field = within(node(root)).getByLabelText(/^Tolerance/) as HTMLInputElement;
    expect(field.value).toBe('0.5');
    await user.selectOptions(within(node(root)).getByLabelText('Vergleich'), 'lt'); // editing a known field keeps the new one
    expect(outputValue(result())).toMatchObject({ comparison: 'lt', tolerance: 0.5 });
    await user.clear(field);
    await user.type(field, '2');
    expect(outputValue(result()).tolerance).toBe(2);
  });

  it('shows members the schema does not know (kept, editable, removable) instead of hiding them', async () => {
    const def = { ...newDefinition('test.u', 1), x_note: 'wichtig', outputs: { value: { op: 'input', name: 'a', x_hint: [1, 2] } } };
    const result = mount(def, caps);
    expect(screen.getAllByText(/zusätzliche Angabe, bleibt erhalten/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    expect(within(node(root)).getAllByText(/x_hint/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('tab', { name: 'Allgemein' }));
    await user.type(screen.getByLabelText(/Anzeigename/), 'N');
    expect(result()).toMatchObject({ x_note: 'wichtig', display_name: 'N' });
    expect(outputValue(result()).x_hint).toEqual([1, 2]);
  });

  it('a new FieldSchema property appears in every field-schema editor', async () => {
    const extended = variant((c) => {
      for (const schema of [c.schemas.definition, c.schemas.registry]) {
        ((schema.$defs as JsonObject).FieldSchema as { properties: JsonObject }).properties.display_hint = { type: 'string', title: 'Display hint' };
      }
    });
    const result = mount(newDefinition('test.f', 1), extended);
    await user.click(screen.getByRole('tab', { name: /Eingänge/ }));
    const hint = screen.getByLabelText('Display hint');
    await user.type(hint, 'kompakt');
    expect(((result().inputs as JsonObject[])[0]!.schema as JsonObject).display_hint).toBe('kompakt');
    expect(((result().inputs as JsonObject[])[0]!.schema as JsonObject).type).toBe('bool');
  });
});

describe('SchemaForm on recursive and open structures', () => {
  const root2: JsonObject = {
    $defs: {
      Node: { type: 'object', title: 'Node', required: ['label'], properties: { label: { type: 'string' }, children: { type: 'array', items: { $ref: '#/$defs/Node' } } } },
      Bag: { type: 'object', additionalProperties: { anyOf: [{ type: 'string' }, { type: 'number' }, { type: 'boolean' }] } },
    },
  };

  it('edits a recursive tree to depth three', async () => {
    let current: Json | undefined = { label: 'root', children: [{ label: 'a', children: [{ label: 'b' }] }] };
    wrap(
      <Stateful initial={current as JsonObject} onValue={(v) => (current = v)}>
        {(v, set) => <SchemaForm root={root2} node={{ $ref: '#/$defs/Node' }} value={v} pointer="/tree" label="Tree" onChange={(n) => set(n as JsonObject)} />}
      </Stateful>,
    );
    const labels = screen.getAllByLabelText(/^Bezeichnung/);
    expect(labels).toHaveLength(3);
    await user.clear(labels[2]!);
    await user.type(labels[2]!, 'deep');
    expect(((((current as JsonObject).children as JsonObject[])[0]!.children as JsonObject[])[0] as JsonObject).label).toBe('deep');
  });

  it('open object with a typed value union: add, rename and edit entries', async () => {
    let current: Json | undefined = { speed: 3 };
    wrap(
      <Stateful initial={current as JsonObject} onValue={(v) => (current = v)}>
        {(v, set) => <SchemaForm root={root2} node={{ $ref: '#/$defs/Bag' }} value={v} pointer="/bag" label="Bag" onChange={(n) => set(n as JsonObject)} />}
      </Stateful>,
    );
    await user.click(screen.getByRole('button', { name: 'Eigenschaft' }));
    expect(Object.keys(current as JsonObject)).toEqual(['speed', 'schluessel2']);
    const key = screen.getByLabelText('Bag: Schlüssel schluessel2');
    await user.clear(key);
    await user.type(key, 'name');
    await user.tab();
    expect(Object.keys(current as JsonObject)).toEqual(['speed', 'name']);
  });
});
