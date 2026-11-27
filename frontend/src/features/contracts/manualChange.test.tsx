import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { ContractEditor } from '@/editors/RegistryObjects';
import { refOptions, type TypeEntry } from '@/features/authoring/catalog';
import { contractFieldLabel, contractFieldValue } from './presentation';
import { Stateful, wrap } from '../../../test/harness';
import type { FieldValue } from '@/lib/types';
import type { JsonObject } from '@/lib/json';

const type: TypeEntry = {
  key: 'ha.manual_change@1', type_id: 'ha.manual_change', type_version: 1,
  origin: 'package', declarative: false, machine: false,
  inputs: [{ name: 'source', required: true }],
  fields: { value: { type: 'bool', required: true, physical_state: false } },
  parameterSchema: { type: 'object', properties: {
    attribution_wait_s: { type: 'integer', title: 'Zuordnungsfrist', minimum: 1, maximum: 60, default: 5, unit: 's', display: 'plain' },
  } },
};

it('offers only direct HA sources and saves the attribution window as seconds', async () => {
  const refs = refOptions({
    sources: [{ source_id: 'source.switch' }, { source_id: 'source.mqtt' }],
    bindings: [
      { source_id: 'source.switch', adapter: { kind: 'ha_state' } },
      { source_id: 'source.mqtt', adapter: { kind: 'mqtt' } },
    ],
    contracts: [{ contract_id: 'fixture.other', type_id: 'ha.manual_change', type_version: 1 }],
  }, [type]);
  const initial = { contract_id: 'fixture.manual', type_id: 'ha.manual_change', type_version: 1, inputs: [], parameters: {} };
  let saved: JsonObject = initial;
  wrap(<Stateful initial={initial} onValue={v => { saved = v; }}>{(value, set) =>
    <ContractEditor key="fixture" value={value} onChange={set} pointer="/contracts/0" types={[type]} refs={refs} />
  }</Stateful>);
  const choice = screen.getByRole('combobox', { name: 'Verknüpfter Wert' });
  expect(within(choice).getAllByRole('option').map(option => (option as HTMLOptionElement).value)).toEqual(['', 'source.switch']);
  expect(screen.getByRole('option', { name: /Manuell geändert/ })).toBeInTheDocument();
  const user = userEvent.setup();
  await user.selectOptions(choice, 'source.switch');
  const wait = screen.getByRole('spinbutton', { name: /Zuordnungsfrist/ });
  expect(wait).toHaveValue(5);
  fireEvent.change(wait, { target: { value: '8' } });
  expect(saved.parameters).toEqual({ attribution_wait_s: 8 });
  expect(saved.inputs).toEqual([{ name: 'source', ref: 'source.switch' }]);
});

it('labels the manual decision and all four origins without altering other contracts', () => {
  const field = (value: FieldValue['value']): FieldValue => ({ status: 'valid', value, quality: 'healthy', reasons: [], evidence: [] });
  expect(contractFieldLabel(type.type_id, 'value')).toBe('Manuell');
  expect(contractFieldValue(type.type_id, 'value', field(true))).toBe('Manuell');
  expect(contractFieldValue(type.type_id, 'value', field(false))).toBe('Automatik');
  expect(['engine', 'ha_automation', 'ha_user', 'unattributed'].map(value => contractFieldValue(type.type_id, 'origin', field(value)))).toEqual([
    'Automatik: Steuerung', 'Automatik: HA-Automation oder Skript',
    'Manuell: über Home Assistant', 'Manuell: am Gerät oder ohne Beleg',
  ]);
  expect(contractFieldValue('fixture.other', 'origin', field('engine'))).toBe('engine');
});
