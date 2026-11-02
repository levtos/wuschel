import { t } from '@/i18n';
import { asArray, asObject, str, uniqueName, type FieldSchema } from '@/features/authoring/model';
import { consumersOfContract, consumersOfSource } from '@/features/authoring/usage';
import { clone, isObject, type JsonObject } from '@/lib/json';
import { sourceCast } from '@/lib/sourceCast';
import type { Registry, RevisionRow } from '@/lib/types';

export class SourceMigrationError extends Error {}
export const objects = (value: unknown): JsonObject[] => Array.isArray(value) ? value.filter(isObject) : [];
export function historicalSource(rows: RevisionRow[], id: string): JsonObject | undefined {
  return [...rows].sort((a, b) => b.revision - a.revision).flatMap(row => objects(row.config.sources)).find(source => source.source_id === id);
}
export function migrationSourceId(config: Registry, rows: RevisionRow[], id: string): string {
  return uniqueName(`${id.slice(0, 110)}_neu`, [...objects(config.sources), ...rows.flatMap(row => objects(row.config.sources))].map(source => str(source.source_id)));
}
export function sourceSchema(source: JsonObject): FieldSchema {
  return (isObject(source.value_schema) ? source.value_schema : { type: source.source_type === 'numeric' ? 'number' : source.source_type === 'boolean' ? 'bool' : 'text', required: true, physical_state: false }) as FieldSchema;
}
export interface MigrationOptions {
  sourceId: string;
  name: string;
  schema: FieldSchema;
  adapter: JsonObject | null;
  contracts: string[];
  /** Explicit enum mapping, or another existing supported transform. */
  transform?: JsonObject;
  templates?: Record<string, { type_id: string; type_version: number }>;
  adaptPassthrough: boolean;
}
function address(adapter: JsonObject | null): string {
  if (!adapter) return '';
  return JSON.stringify(adapter.kind === 'mqtt' ? [adapter.kind, adapter.mqtt_topic ?? null, adapter.mqtt_value_path ?? null] : [adapter.kind, adapter.ha_entity_id ?? null, adapter.ha_attribute ?? null]);
}

/** Entity repair changes only the transport address; all logical identities stay. */
export function planEntityChange(config: Registry, sourceId: string, bindingId: string, entityId: string): Registry {
  if (!Array.isArray(config.bindings) || !config.bindings.every(isObject)) throw new SourceMigrationError(t('alpha7.device.invalidConfig'));
  const binding = objects(config.bindings).find(row => row.binding_id === bindingId && row.source_id === sourceId);
  const adapter = asObject(binding?.adapter);
  if (!binding || !['ha_state', 'ha_attribute', 'ha_calendar'].includes(str(adapter.kind)) || !/^[a-z_]+\.[a-z0-9_]+$/.test(entityId)) throw new SourceMigrationError(t('b22.entity.invalid'));
  return { ...config, bindings: objects(config.bindings).map(row => row.binding_id === bindingId ? { ...row, adapter: { ...adapter, ha_entity_id: entityId } } : row) };
}
/** Authors one atomic draft edit. The ordinary server validator remains authoritative. */
export function planSourceMigration(config: Registry, rows: RevisionRow[], oldId: string, options: MigrationOptions) {
  for (const key of ['sources', 'bindings', 'contracts', 'definitions', 'dependencies']) if (config[key] !== undefined && (!Array.isArray(config[key]) || !asArray(config[key]).every(isObject))) throw new SourceMigrationError(t('alpha7.device.invalidConfig'));
  const next = clone(config);
  const sources = objects(next.sources); const bindings = objects(next.bindings); const contracts = objects(next.contracts); const definitions = objects(next.definitions);
  const old = sources.find(source => source.source_id === oldId);
  if (!old) throw new SourceMigrationError(t('alpha7.move.sourceGone'));
  if ([...sources, ...rows.flatMap(row => objects(row.config.sources))].some(source => source.source_id === options.sourceId)) throw new SourceMigrationError(t('alpha7.move.idUsed'));
  const previous = historicalSource(rows, oldId);
  // An attempted forbidden edit becomes the new source; restore the historical old schema.
  if (previous) {
    old.source_type = previous.source_type!;
    if (previous.value_schema === undefined) delete old.value_schema; else old.value_schema = clone(previous.value_schema);
  }
  const oldBindings = bindings.filter(binding => binding.source_id === oldId);
  if (oldBindings.length > 1) throw new SourceMigrationError(t('alpha7.device.invalidConfig'));
  const originalBinding = oldBindings[0];
  const samePath = !!originalBinding && address(asObject(originalBinding.adapter)) === address(options.adapter);
  const selected = new Set(options.contracts);
  const used = consumersOfSource(next, oldId);
  const monitors = objects(next.dependencies).filter(dependency => dependency.source_id === oldId);
  if (samePath && (used.contracts.some(id => !selected.has(id)) || used.sources.length || monitors.length)) throw new SourceMigrationError(t('alpha7.move.samePathRemaining'));
  const source: JsonObject = { ...clone(old), source_id: options.sourceId, display_name: options.name.trim(), source_type: options.schema.type === 'number' ? 'numeric' : options.schema.type === 'bool' ? 'boolean' : options.schema.type === 'text' ? 'text' : 'state', value_schema: clone(options.schema) };
  if (!samePath) source.physical_source_key = uniqueName(`${str(old.physical_source_key).slice(0, 110)}_neu`, sources.map(source => str(source.physical_source_key)));
  sources.push(source);
  if (options.adapter) {
    const transform = options.transform ?? sourceCast(source);
    if (!transform) throw new SourceMigrationError(t('alpha7.move.mappingRequired'));
    if (samePath) bindings.splice(bindings.indexOf(originalBinding!), 1);
    bindings.push({ ...(originalBinding ? clone(originalBinding) : {}), binding_id: uniqueName(`binding.${options.sourceId.replace(/^source\./, '').slice(0, 100)}_neu`, bindings.map(binding => str(binding.binding_id))), source_id: options.sourceId, adapter: clone(options.adapter), transform: clone(transform) });
  }
  const versions = new Map<string, number>();
  for (const definition of [...definitions, ...rows.flatMap(row => objects(row.config.definitions))]) versions.set(str(definition.type_id), Math.max(versions.get(str(definition.type_id)) ?? 0, Number(definition.type_version)));
  const adapted = new Map<string, number>();
  for (const contract of contracts) {
    const id = str(contract.contract_id);
    if (!selected.has(id) || !used.contracts.includes(id)) continue;
    if (!Array.isArray(contract.inputs) || !contract.inputs.every(isObject)) throw new SourceMigrationError(t('alpha7.device.invalidConfig'));
    const inputs = objects(contract.inputs);
    contract.inputs = inputs.map(input => input.ref === oldId ? { ...input, ref: options.sourceId } : input);
    const chosen = options.templates?.[id];
    if (chosen) { contract.type_id = chosen.type_id; contract.type_version = chosen.type_version; continue; }
    if (!options.adaptPassthrough) continue;
    const definition = definitions.find(definition => definition.type_id === contract.type_id && definition.type_version === contract.type_version);
    const input = objects(definition?.inputs)[0]; const output = asObject(asObject(definition?.outputs).value);
    // Only a pure single-value passthrough can be adapted without inventing new rule semantics.
    if (!definition || definition.machine || objects(definition.inputs).length !== 1 || Object.keys(asObject(definition.rules)).length || Object.keys(asObject(definition.outputs)).length !== 1 || Object.keys(asObject(asObject(definition.schema).fields)).length !== 1 || output.op !== 'input' || output.name !== input?.name || inputs.length !== 1 || inputs[0]?.ref !== oldId) continue;
    const key = `${str(definition.type_id)}@${String(definition.type_version)}`;
    let version = adapted.get(key);
    if (!version) {
      version = (versions.get(str(definition.type_id)) ?? 0) + 1;
      versions.set(str(definition.type_id), version); adapted.set(key, version);
      definitions.push({ ...clone(definition), type_version: version, inputs: [{ ...input!, schema: clone(options.schema) }], schema: { ...asObject(definition.schema), fields: { value: clone(options.schema) } } });
    }
    contract.type_version = version;
  }
  const indirect = new Set<string>(); const queue = [...selected];
  while (queue.length) for (const id of consumersOfContract(next, queue.shift()!)) if (!selected.has(id) && !indirect.has(id)) { indirect.add(id); queue.push(id); }
  return { config: { ...next, sources, bindings, contracts, definitions }, sourceId: options.sourceId, direct: used.contracts.filter(id => selected.has(id)), indirect: [...indirect], movedBinding: samePath, newVersions: adapted.size };
}
