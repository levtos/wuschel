import { asArray, asObject, definitionRoots, str, walkRule, type Rule } from '@/features/authoring/model';
import { consumersOfContract, contractsUsingDefinition } from '@/features/authoring/usage';
import { clone, deepEqual, isObject, type Json, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';

const objects = (value: Json | undefined) => asArray(value).filter(isObject);

function lookups(definition: JsonObject, catalog: string, version: number): Rule[] {
  const result: Rule[] = [];
  for (const { rule } of definitionRoots(definition)) walkRule(rule, node => {
    if (node.op === 'managed_catalog_lookup' && node.catalog_id === catalog && node.definition_version === version) result.push(node);
  });
  return result;
}

export function indirectConsumers(config: Registry, direct: string[]) {
  const seen = new Set(direct);
  const queue = [...direct];
  for (const id of queue) for (const linked of consumersOfContract(config, id)) {
    if (seen.has(linked)) continue;
    seen.add(linked);
    queue.push(linked);
  }
  return queue.slice(direct.length);
}

export function catalogUsage(config: Registry, catalog: string, version: number) {
  const definitions = objects(config.definitions).filter(definition => lookups(definition, catalog, version).length > 0);
  const direct = [...new Set(definitions.flatMap(definition => contractsUsingDefinition(config, str(definition.type_id), Number(definition.type_version))))];
  return { definitions, direct, indirect: indirectConsumers(config, direct) };
}

export function nextCatalogVersion(config: Registry, catalog: string, history: Registry[]) {
  return Math.max(0, ...[config, ...history].flatMap(value => objects(value.catalogs))
    .filter(item => item.catalog_id === catalog).map(item => Number(item.version))) + 1;
}

export function switchCatalogConsumers(config: Registry, catalog: string, from: number, to: number, selected: string[], history: Registry[]): Registry {
  const definitions = [...objects(config.definitions)];
  const contracts = objects(config.contracts);
  const versions = [...definitions, ...history.flatMap(value => objects(value.definitions))];
  const available = catalogUsage(config, catalog, from);
  const migrated = new Map<string, number>();
  for (const raw of available.definitions) {
    const type = str(raw.type_id);
    const oldVersion = Number(raw.type_version);
    if (!contracts.some(contract => contract.type_id === type && contract.type_version === oldVersion && selected.includes(str(contract.contract_id)))) continue;
    const candidate = clone(raw);
    for (const rule of lookups(candidate, catalog, from)) rule.definition_version = to;
    const previouslyCreated = definitions.find(value => value.type_id === type && deepEqual({ ...value, type_version: oldVersion }, candidate));
    const version = previouslyCreated ? Number(previouslyCreated.type_version) : Math.max(oldVersion, ...versions.filter(value => value.type_id === type).map(value => Number(value.type_version))) + 1;
    migrated.set(type + '@' + oldVersion, version);
    if (previouslyCreated) continue;
    const next = { ...candidate, type_version: version };
    definitions.push(next);
    versions.push(next);
  }
  return {
    ...config,
    definitions,
    contracts: contracts.map(contract => {
      const version = migrated.get(str(contract.type_id) + '@' + Number(contract.type_version));
      return version && selected.includes(str(contract.contract_id)) ? { ...contract, type_version: version } : contract;
    }),
  };
}

export function catalogVersionName(config: Registry, id: string) {
  const row = objects(config.contracts).find(value => value.contract_id === id);
  return str(asObject(row).display_name) || id;
}
