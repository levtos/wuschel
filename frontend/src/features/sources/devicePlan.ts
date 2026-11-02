import { t } from '@/i18n';
import { asArray, asObject, str } from '@/features/authoring/model';
import { clone, deepEqual, isObject, type JsonObject } from '@/lib/json';
import { effectiveRule, ownRule, upgradeFreshness } from './freshness';
import { sourceCast } from '@/lib/sourceCast';
import type { HaEntity, Registry } from '@/lib/types';

export type DevicePreset = 'temperature' | 'contact' | 'motion' | 'counter' | 'other';
export const DEVICE_PRESETS: DevicePreset[] = ['temperature', 'contact', 'motion', 'counter', 'other'];
export const FRESHNESS_MODES = ['event_stateful', 'report_heartbeat', 'periodic_ttl', 'liveness_source'] as const;
export class DevicePlanError extends Error {}
export function freshnessPreset(preset: DevicePreset): JsonObject {
  return preset === 'contact' ? { mode: 'report_heartbeat', interval_s: 10800, future_tolerance_s: 1 } : {};
}
export function presetFor(entity: HaEntity): DevicePreset {
  if (entity.device_class === 'temperature') return 'temperature';
  if (['door', 'window', 'opening', 'garage_door'].includes(entity.device_class ?? '')) return 'contact';
  if (['motion', 'occupancy', 'presence'].includes(entity.device_class ?? '')) return 'motion';
  if (['total', 'total_increasing'].includes(entity.state_class ?? '')) return 'counter';
  return 'other';
}
export function fieldTypeFor(entity: HaEntity): 'number' | 'bool' | 'text' {
  if (entity.entity_id.startsWith('binary_sensor.')) return 'bool';
  if (entity.unit || entity.device_class === 'temperature' || entity.state_class || entity.current_value !== null && entity.current_value !== undefined && /^[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?$/i.test(entity.current_value)) return 'number';
  if (['on', 'off', 'true', 'false'].includes(entity.current_value ?? '')) return 'bool';
  return 'text';
}
function suffix(value: string): string {
  const name = value.toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss').replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 90) || 'geraet';
  return /^[a-z]/.test(name) ? name : `geraet_${name}`;
}
function unique(base: string, ids: string[]): string {
  let id = base; let index = 2; while (ids.includes(id)) id = `${base}_${index++}`; return id;
}
export interface DeviceOptions {
  preset?: DevicePreset; freshness?: JsonObject; name?: string;
  freshnessSelection?: JsonObject;
  fieldType?: 'number' | 'bool' | 'text';
  sourceId?: string; contractId?: string;
  repairSourceId?: string;
  contract?: 'new' | { id: string; input: string };
}

/** Configuration authoring only: the server still validates types, units, identity and the whole graph. */
export function planDevice(config: Registry, entity: HaEntity, options: DeviceOptions = {}): { config: Registry; sourceId: string; sourceIndex: number; contractId?: string; reused: boolean } {
  for (const key of ['sources', 'bindings', 'dependencies', 'contracts', 'definitions']) if (config[key] !== undefined && (!Array.isArray(config[key]) || !asArray(config[key]).every(isObject))) throw new DevicePlanError(t('alpha7.device.invalidConfig'));
  const next = clone(config);
  const sources = asArray(next.sources).filter(isObject); const bindings = asArray(next.bindings).filter(isObject);
  const dependencies = asArray(next.dependencies).filter(isObject); const contracts = asArray(next.contracts).filter(isObject);
  const definitions = asArray(next.definitions).filter(isObject);
  const existing = options.repairSourceId ? bindings.find(binding => binding.source_id === options.repairSourceId && asObject(binding.adapter).kind === 'ha_state') : bindings.find(binding => asObject(binding.adapter).kind === 'ha_state' && asObject(binding.adapter).ha_entity_id === entity.entity_id);
  if (options.repairSourceId && !existing) throw new DevicePlanError(t('alpha7.device.missingConfiguredSource'));
  if (options.repairSourceId && bindings.some(binding => binding !== existing && asObject(binding.adapter).ha_entity_id === entity.entity_id)) throw new DevicePlanError(t('alpha7.device.duplicateSource'));
  let source = sources.find(item => item.source_id === existing?.source_id);
  if (existing && !source) throw new DevicePlanError(t('alpha7.device.missingConfiguredSource'));
  const proposedLabel = options.name?.trim() || entity.display_name;
  const label = proposedLabel && proposedLabel !== entity.entity_id ? proposedLabel : t('alpha7.device.name');
  const type = options.fieldType ?? fieldTypeFor(entity);
  const schema = { type, required: true, physical_state: false, ...(type === 'number' && entity.unit ? { unit: entity.unit } : {}) };
  if (!source) {
    if (!options.freshness?.mode) throw new DevicePlanError(t('alpha7.device.badFreshness'));
    const dependency = dependencies.find(item => item.kind === 'ha' && item.monitor === 'internal');
    const dependencyId = dependency ? str(dependency.dependency_id) : unique('dep.ha', dependencies.map(item => str(item.dependency_id)));
    if (!dependency) dependencies.push({ dependency_id: dependencyId, kind: 'ha', monitor: 'internal' });
    const sourceId = options.sourceId || unique('source.device', sources.map(item => str(item.source_id)));
    if (sources.some(item => item.source_id === sourceId)) throw new DevicePlanError(t('alpha7.device.duplicateSource'));
    source = { source_id: sourceId, display_name: label, source_type: type === 'number' ? 'numeric' : type === 'bool' ? 'boolean' : 'text', value_schema: schema, source_origin: 'local', dependency_id: dependencyId, physical_source_key: unique('physical.device', sources.map(item => str(item.physical_source_key))), freshness: options.freshness ?? freshnessPreset(options.preset ?? presetFor(entity)) };
    sources.push(source);
    if (options.preset && options.preset !== 'other') { source.sensor_kind = options.preset; source.sensor_kind_origin = 'confirmed'; }
    bindings.push({ binding_id: unique('binding.device', bindings.map(item => str(item.binding_id))), display_name: label, source_id: sourceId, adapter: { kind: 'ha_state', ha_entity_id: entity.entity_id }, transform: sourceCast(source)! });
  } else if (existing) {
    const cast = sourceCast(source);
    if (!cast) throw new DevicePlanError(t('alpha7.device.advancedMapping'));
    if (options.fieldType && options.fieldType !== cast.target) throw new DevicePlanError(t('alpha7.device.existingTypeLocked'));
    if (cast.target === 'number' && (asObject(source.value_schema).unit ?? '') !== (entity.unit ?? '')) throw new DevicePlanError(t('alpha7.device.existingTypeLocked'));
    if (options.repairSourceId) existing.adapter = { ...asObject(existing.adapter), ha_entity_id: entity.entity_id };
    if (options.name?.trim()) source.display_name = options.name.trim();
    if (options.freshness && !deepEqual(options.freshness, effectiveRule(next, source))) Object.assign(source, ownRule(source, options.freshness));
    // Complete a metadata import without reinterpreting the immutable existing source schema.
    if (!existing.transform) existing.transform = cast;
  }
  let contractId: string | undefined;
  if (options.contract === 'new') {
    contractId = options.contractId || unique('device.state', contracts.map(item => str(item.contract_id)));
    if (contracts.some(item => item.contract_id === contractId)) throw new DevicePlanError(t('alpha7.device.duplicateContract'));
    const valueSchema = isObject(source.value_schema) ? source.value_schema : { type: source.source_type === 'numeric' ? 'number' : source.source_type === 'boolean' ? 'bool' : 'text', required: true, physical_state: false };
    const typeId = unique(`test.geraet_${suffix(contractId)}`, definitions.map(item => str(item.type_id)));
    definitions.push({ type_id: typeId, type_version: 1, display_name: t('alpha7.device.passthroughName', { name: label }), inputs: [{ name: 'geraet', schema: valueSchema }], schema: { fields: { value: valueSchema }, available_projection: ['value'] }, outputs: { value: { op: 'input', name: 'geraet' } } });
    contracts.push({ contract_id: contractId, display_name: label, type_id: typeId, type_version: 1, enabled: true, inputs: [{ name: 'geraet', ref: str(source.source_id) }], parameters: {} });
  } else if (options.contract) {
    const target = options.contract;
    const contract = contracts.find(item => item.contract_id === target.id);
    if (!contract) throw new DevicePlanError(t('alpha7.device.contractGone'));
    contract.inputs = [...asArray(contract.inputs).filter(item => !isObject(item) || item.name !== target.input), { name: target.input, ref: str(source.source_id) }];
    contractId = target.id;
  }
  const result = upgradeFreshness({ ...next, sources, bindings, dependencies, contracts, definitions });
  const authored = asArray(result.sources).filter(isObject).find(item => item.source_id === source!.source_id)!;
  if (options.preset && options.preset !== 'other') { authored.sensor_kind = options.preset; authored.sensor_kind_origin = 'confirmed'; }
  if (options.freshnessSelection) {
    authored.freshness_selection = options.freshnessSelection;
  }
  return { config: result, sourceId: str(source.source_id), sourceIndex: sources.findIndex(item => item.source_id === source.source_id), contractId, reused: !!existing };
}
