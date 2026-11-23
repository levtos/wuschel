import { cleanup, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect } from 'vitest';
import { DefinitionEditor } from '@/editors/DefinitionEditor';
import { BindingEditor, SourceEditor } from '@/editors/RegistryObjects';
import { emptyRegistry, newDefinition } from '@/features/authoring/model';
import type { Capabilities } from '@/lib/capabilities';
import type { JsonObject } from '@/lib/json';
import { gui, node } from './gui';
import { caps, Stateful, wrap } from './harness';

/** Additional manual-first cases beyond the example. The optional callback validates
 * every document with the real Alpha-4 API in the engine E2E suite. */
export async function exerciseCoverage(validate: (document: JsonObject) => Promise<void>, capabilities: Capabilities = caps) {
  const user = userEvent.setup();
  const g = gui(user);
  const base = '/definitions/0';
  const output = `${base}/outputs/value`;
  const cases = ['input', 'literal', 'and', 'or', 'not', 'compare', 'map', 'first_match', 'bucket', 'formula', 'fusion', 'not_applicable', ...capabilities.temporal.map((t) => `temporal:${t.operation}`)];
  for (const name of cases) {
    let value = newDefinition(`test.gui_${name.replace(':', '_')}`, 1);
    wrap(<Stateful initial={value} onValue={(next) => { value = next; }}>{(v, set) => <DefinitionEditor value={v} onChange={set} pointer={base} caps={capabilities} locked={false} />}</Stateful>, { caps: capabilities });
    if (name === 'bucket' || name === 'temporal:delta') {
      await g.tab(/Eingänge/);
      await user.selectOptions(within(node(`${base}/inputs/0/schema`)).getByLabelText('Typ'), 'number');
    }
    await g.tab(/Ausgaben/);
    const numberOutput = name === 'formula' || name === 'temporal:delta' || name === 'temporal:age';
    if (numberOutput || name === 'map') await user.selectOptions(within(node(`${base}/schema/fields/value`)).getByLabelText('Typ'), numberOutput ? 'number' : 'text');
    await g.setOp(output, name.startsWith('temporal:') ? 'temporal' : name);
    if (name.startsWith('temporal:')) {
      await user.selectOptions(within(node(output)).getByLabelText('Operation'), name.split(':')[1]!);
      await g.type(within(node(output)).getByLabelText("Eindeutige Kennung"), `stable_${name.split(':')[1]}`);
      await g.type(within(node(output)).getByLabelText('Dauer (s)'), '3');
    }
    expect(screen.queryByRole('textbox', { name: /JSON/ })).toBeNull();
    await validate({ ...emptyRegistry(), definitions: [value] });
    cleanup();
  }

  for (const mode of ['event_stateful', 'report_heartbeat', 'periodic_ttl', 'liveness_source']) {
    const heartbeat: JsonObject = { source_id: 'source.heartbeat', source_type: 'boolean', source_origin: 'local', dependency_id: 'dep.test', physical_source_key: 'heartbeat', freshness: { mode: 'event_stateful', future_tolerance_s: 1 } };
    let value: JsonObject = { ...heartbeat, source_id: 'source.sample', source_type: 'numeric', physical_source_key: 'sample' };
    wrap(<Stateful initial={value} onValue={(next) => { value = next; }}>{(v, set) => <SourceEditor value={v} onChange={set} pointer="/sources/0" dependencyIds={['dep.test']} sourceIds={['source.heartbeat', 'source.sample']} schema={capabilities.schemas.registry} />}</Stateful>);
    await user.selectOptions(screen.getByLabelText('Modus'), mode);
    if (mode === 'liveness_source') await user.selectOptions(screen.getByLabelText("Quelle für die Erreichbarkeit *"), 'source.heartbeat');
    await validate({ ...emptyRegistry(), dependencies: [{ dependency_id: 'dep.test', kind: 'ha', monitor: 'internal' }], sources: [value, heartbeat] });
    cleanup();
  }

  for (const kind of ['ha_state', 'ha_attribute', 'mqtt']) {
    for (const transform of ['type_cast', 'unit_scale', 'map_ref']) {
      let value: JsonObject = { binding_id: 'binding.sample', source_id: 'source.sample', adapter: { kind: 'ha_state' } };
      wrap(<Stateful initial={value} onValue={(next) => { value = next; }}>{(v, set) => <BindingEditor value={v} onChange={set} pointer="/bindings/0" sourceIds={['source.sample']} sources={[{ source_id: 'source.sample', source_type: 'numeric' }]} catalogIds={['catalog.sample']} schema={capabilities.schemas.registry} onPickEntity={() => {}} />}</Stateful>);
      await user.selectOptions(screen.getByLabelText('Art'), kind);
      await g.type(screen.getByLabelText(kind === 'mqtt' ? "MQTT-Kanal *" : "Gerät oder Wert in Home Assistant *"), kind === 'mqtt' ? 'synthetic/value' : 'sensor.synthetic');
      if (kind === 'ha_attribute') await g.type(screen.getByLabelText('Attribut *'), 'value');
      await user.selectOptions(screen.getByLabelText("Umwandlung"), transform);
      if (transform === 'unit_scale') await g.type(screen.getByLabelText('Faktor'), '2');
      await validate({ ...emptyRegistry(), dependencies: [{ dependency_id: 'dep.test', kind: kind === 'mqtt' ? 'mqtt' : 'ha', monitor: 'internal' }], sources: [{ source_id: 'source.sample', source_type: 'numeric', source_origin: 'local', dependency_id: 'dep.test', physical_source_key: 'sample', freshness: { mode: 'event_stateful', future_tolerance_s: 1 } }], bindings: [value], catalogs: [{ catalog_id: 'catalog.sample', kind: 'map', version: 1, entries: { on: 1, off: 0 } }] });
      cleanup();
    }
  }
}
