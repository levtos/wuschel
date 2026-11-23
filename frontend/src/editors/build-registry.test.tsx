import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import example from '../../test/fixtures/example-registry.json';
import { refOptions, typeCatalog } from '@/features/authoring/catalog';
import type { JsonObject } from '@/lib/json';
import { caps, Stateful, wrap } from '../../test/harness';
import { BindingEditor, ContractEditor, DependencyEditor, SourceEditor } from './RegistryObjects';

/** Acceptance 3 (non-definition part): the example's dependency, sources, bindings and contracts built via the editors. */
const user = userEvent.setup();
const doc = example as unknown as Record<string, JsonObject[]>;
const schema = caps.schemas.registry;
const type = async (el: HTMLElement, text: string) => {
  await user.clear(el);
  await user.type(el, text);
};
function mount(initial: JsonObject, render: (value: JsonObject, set: (n: JsonObject) => void) => React.ReactNode) {
  let current = initial;
  wrap(<Stateful initial={initial} onValue={(v) => (current = v)}>{render}</Stateful>);
  return () => current;
}

describe('registry objects are fully editable without JSON', () => {
  it('dependency', async () => {
    const result = mount({ dependency_id: 'dep.neu', kind: 'ha', monitor: 'internal' }, (v, set) => <DependencyEditor value={v} onChange={set} pointer="/dependencies/0" sourceIds={[]} schema={schema} />);
    await type(screen.getByLabelText("Kennung der Verbindung"), 'dep.test');
    expect(result()).toEqual(doc.dependencies![0]);
  });

  it('source with freshness mode and physical key', async () => {
    const result = mount(
      { source_id: 'source.neu', source_type: 'boolean', source_origin: 'local', freshness: { mode: 'event_stateful', future_tolerance_s: 1 }, dependency_id: 'dep.test', physical_source_key: 'physical.neu' },
      (v, set) => <SourceEditor value={v} onChange={set} pointer="/sources/0" dependencyIds={['dep.test']} sourceIds={['source.a']} schema={schema} />,
    );
    const sourceEditor = screen.getByLabelText('Name').parentElement!.parentElement!;
    await user.click(within(sourceEditor).getAllByRole('button', { name: 'Erweitert' })[0]!);
    await type(screen.getByLabelText("Quellenkennung"), 'source.a');
    await type(screen.getByLabelText("Kennung der Messquelle"), 'physical.a');
    expect(result()).toEqual(doc.sources![0]);
  });

  it('source freshness shows exactly the fields the schema requires for the mode', async () => {
    mount(
      { source_id: 'source.x', source_type: 'numeric', source_origin: 'local', freshness: { mode: 'event_stateful', future_tolerance_s: 1 }, dependency_id: 'd', physical_source_key: 'p' },
      (v, set) => <SourceEditor value={v} onChange={set} pointer="/sources/0" dependencyIds={['d']} sourceIds={['source.x', 'source.y']} schema={schema} />,
    );
    expect(screen.queryByLabelText(/Intervall/)).toBeNull();
    await user.selectOptions(screen.getByLabelText('Modus'), 'liveness_source');
    expect(screen.getByLabelText(/Intervall/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Quelle für die Erreichbarkeit/)).toBeInTheDocument();
  });

  it('binding with HA entity and optional transform', async () => {
    const result = mount({ binding_id: 'binding.neu', source_id: 'source.a', adapter: { kind: 'ha_state' } }, (v, set) => (
      <BindingEditor value={v} onChange={set} pointer="/bindings/0" sourceIds={['source.a', 'source.b']} sources={doc.sources as JsonObject[]} catalogIds={[]} schema={schema} onPickEntity={() => {}} />
    ));
    await type(screen.getByLabelText("Kennung der Verknüpfung"), 'binding.a');
    await type(screen.getByLabelText(/^Gerät oder Wert in Home Assistant \*/), 'sensor.example_1');
    await user.click(screen.getByRole('button', { name: 'Umwandlung ergänzen' }));
    expect(result()).toEqual(doc.bindings![0]);
    await user.selectOptions(screen.getByLabelText("Umwandlung"), 'type_cast');
    expect(result().transform).toEqual({ kind: 'type_cast', target: 'bool' });
    await user.selectOptions(screen.getByLabelText("Umwandlung"), '');
    expect('transform' in result() && result().transform !== undefined).toBe(false);
  });

  it('MQTT binding shows topic fields instead of HA fields', async () => {
    mount({ binding_id: 'b', source_id: 'source.a', adapter: { kind: 'mqtt' } }, (v, set) => (
      <BindingEditor value={v} onChange={set} pointer="/bindings/0" sourceIds={['source.a']} catalogIds={[]} schema={schema} onPickEntity={() => {}} />
    ));
    expect(screen.getByLabelText(/MQTT-Kanal/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Gerät oder Wert in Home Assistant \*/)).toBeNull();
  });

  it('contract instance: type, enabled and typed input references', async () => {
    const types = typeCatalog(caps, example as unknown as JsonObject);
    const refs = refOptions(example as unknown as JsonObject, types, 'fixture.either');
    const result = mount({ contract_id: 'x', type_id: 'test.either', type_version: 1, enabled: true, inputs: [], parameters: {} }, (v, set) => (
      <ContractEditor value={v} onChange={set} pointer="/contracts/2" types={types} refs={refs} />
    ));
    await type(screen.getByLabelText("Vertragskennung"), 'fixture.either');
    const refSelects = screen.getAllByLabelText("Verknüpfter Wert");
    await user.selectOptions(refSelects[0]!, 'fixture.left');
    await user.selectOptions(refSelects[1]!, 'fixture.right');
    expect(result()).toEqual(doc.contracts![2]);
    // This legacy definition declares no instance settings; the section stays available.
    expect(within(document.body).getByText('Einstellungen für diesen Vertrag')).toBeInTheDocument();
  });

  it('package-type parameters come from the type schema, not from hard-coded forms', async () => {
    const types = typeCatalog(caps, null);
    const result = mount({ contract_id: 'p', type_id: 'test.temporal', type_version: 1, enabled: true, inputs: [], parameters: {} }, (v, set) => (
      <ContractEditor value={v} onChange={set} pointer="/contracts/0" types={types} refs={[]} />
    ));
    expect(screen.getByLabelText(/Aktion/i)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Aktion/i), 'stable_for');
    expect((result().parameters as JsonObject).operation).toBe('stable_for');
  });
});
