import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { asArray, asObject } from '@/features/authoring/model';
import { isObject } from '@/lib/json';
import { CalendarCoverageField, calendarDefaults, planCalendar, validCalendarSettings } from './CalendarSourceDialog';
import { planDevice } from './devicePlan';

const empty = () => ({ config_schema_version: 1, sources: [], bindings: [], contracts: [], catalogs: [], definitions: [], dependencies: [] });
const entity = { entity_id: 'sensor.synthetic_battery', display_name: 'Synthetisches Gerät', unit: '%', current_value: '45', device_class: null, state_class: null, attributes: [] };

describe('P8 address-free authoring and stable repair identities', () => {
  it('keeps addresses exclusively in the binding and reuses the existing device', () => {
    const created = planDevice(empty(), entity, { contract: 'new', freshness: { mode: 'event_stateful', future_tolerance_s: 0 } });
    const { bindings, ...withoutBindings } = created.config;
    expect(JSON.stringify(withoutBindings)).not.toContain(entity.entity_id);
    expect(JSON.stringify(withoutBindings)).not.toContain('synthetic_battery');
    const repaired = planDevice(created.config, { ...entity, entity_id: 'sensor.synthetic_replacement' }, { repairSourceId: created.sourceId, name: 'Neuer synthetischer Name' });
    expect(asArray(repaired.config.sources)).toHaveLength(1);
    expect(asArray(repaired.config.bindings)).toHaveLength(1);
    expect(asArray(repaired.config.contracts)).toEqual(asArray(created.config.contracts));
    const oldBinding = asArray(bindings).filter(isObject)[0]!;
    const newBinding = asArray(repaired.config.bindings).filter(isObject)[0]!;
    expect(newBinding.binding_id).toBe(oldBinding.binding_id);
    expect(newBinding.source_id).toBe(oldBinding.source_id);
    expect(asObject(newBinding.adapter).ha_entity_id).toBe('sensor.synthetic_replacement');
    expect(asArray(repaired.config.sources).filter(isObject)[0]!.physical_source_key).toBe(asArray(created.config.sources).filter(isObject)[0]!.physical_source_key);
    expect(planDevice(repaired.config, { ...entity, entity_id: 'sensor.synthetic_replacement' }).reused).toBe(true);
  });

  it('repairs a calendar under the same source and binding IDs and preserves consumers', () => {
    const created = planCalendar(empty(), 'calendar.synthetic_old', 'Synthetische Feiertage', { ...calendarDefaults, coverageMonths: 12 });
    const source = created.config.sources[0]!;
    const draft = { ...created.config, contracts: [{ contract_id: 'synthetic.holiday', inputs: [{ name: 'calendar', ref: String(source.source_id) }] }] };
    const repaired = planCalendar(draft, 'calendar.synthetic_new', 'Neuer synthetischer Name', { ...calendarDefaults, coverageMonths: 18 }, String(source.source_id));
    expect(repaired.config.sources).toHaveLength(1);
    expect(repaired.config.bindings).toHaveLength(1);
    expect(repaired.config.sources[0]!.source_id).toBe(source.source_id);
    expect(repaired.config.bindings[0]!.binding_id).toBe(created.config.bindings[0]!.binding_id);
    expect(repaired.config.contracts).toEqual(draft.contracts);
    expect(repaired.config.sources[0]!.calendar_coverage_months).toBe(18);
    const { bindings: _bindings, ...outsideBinding } = repaired.config;
    expect(JSON.stringify(outsideBinding)).not.toContain('synthetic_new');
    expect(JSON.stringify(outsideBinding)).not.toContain('synthetic_old');
  });

  it('offers the holiday probe as an explicit optional source parameter with twelve months', async () => {
    const changes: (number | undefined)[] = [];
    const user = userEvent.setup();
    render(<CalendarCoverageField onChange={value => changes.push(value)} />);
    await user.selectOptions(screen.getByLabelText('Abdeckung des Feiertagskalenders prüfen'), 'on');
    expect(changes).toEqual([12]);
    expect(validCalendarSettings({ ...calendarDefaults, coverageMonths: 0 })).toBe(false);
    expect(validCalendarSettings({ ...calendarDefaults, coverageMonths: 12 })).toBe(true);
    expect(validCalendarSettings({ ...calendarDefaults, coverageMonths: 25 })).toBe(false);
  });
});
