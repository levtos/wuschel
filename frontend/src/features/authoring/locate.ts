import { t as uiText } from '@/i18n';
import { isObject, type JsonObject } from '@/lib/json';
import { asArray, str } from './model';

export interface Location {
  to: string;
  label: string;
}

const COLLECTIONS: Record<string, { tab: string; singular: string }> = {
  sources: { tab: 'sources', singular: uiText("features.authoring.locate.source_0e570c") },
  bindings: { tab: 'bindings', singular: uiText("features.authoring.locate.binding_164c69") },
  catalogs: { tab: 'catalogs', singular: uiText("features.authoring.locate.katalog_1c58e0") },
  dependencies: { tab: 'dependencies', singular: uiText("features.authoring.locate.dependency_711595") },
};

const DEFINITION_TABS: Record<string, string> = {
  inputs: 'inputs',
  refresh_inputs: 'general',
  schema: 'outputs',
  outputs: 'outputs',
  rules: 'rules',
  machine: 'machine',
};

/** Where in the workbench the object addressed by a JSON Pointer is edited. */
export function locateIssue(path: string, config: JsonObject | null | undefined): Location {
  const parts = path.split('/').slice(1).map((p) => p.replace(/~1/g, '/').replace(/~0/g, '~'));
  const [collection, index, section] = parts;
  const i = Number(index);
  if (collection && COLLECTIONS[collection] && Number.isInteger(i)) {
    const item = asArray(config?.[collection]).filter(isObject)[i];
    const idKey = collection === 'sources' ? 'source_id' : collection === 'bindings' ? 'binding_id' : collection === 'catalogs' ? 'catalog_id' : 'dependency_id';
    const tab = COLLECTIONS[collection]!.tab;
    return { to: `/sources/${tab === 'sources' ? 'devices' : tab}?tab=${tab}&item=${i}`, label: uiText("features.authoring.locate.v0_v1_24a420", { v0: (COLLECTIONS[collection]!.singular), v1: (item ? str(item[idKey]) : uiText("features.authoring.locate.v0_cdb96d", { v0: (i) })) }) };
  }
  if (collection === 'contracts' && Number.isInteger(i)) {
    const contract = asArray(config?.contracts).filter(isObject)[i];
    return contract ? { to: `/contracts/view/${encodeURIComponent(str(contract.contract_id))}?tab=config&edit=1`, label: uiText("features.authoring.locate.contract_v0_fef1f2", { v0: (str(contract.display_name) || str(contract.contract_id)) }) } : { to: "/contracts/list/all", label: uiText("features.authoring.locate.contracts_0488ac") };
  }
  if (collection === 'definitions' && Number.isInteger(i)) {
    const definition = asArray(config?.definitions).filter(isObject)[i];
    if (definition) {
      const tab = section ? (DEFINITION_TABS[section] ?? 'general') : 'general';
      return { to: `/templates/details/${encodeURIComponent(str(definition.type_id))}/${String(definition.type_version)}?tab=${tab}`, label: uiText("features.authoring.locate.definition_v0_v1_71ce19", { v0: (str(definition.type_id)), v1: (String(definition.type_version)) }) };
    }
  }
  return { to: "/changes/drafts", label: uiText("features.authoring.locate.registry_dokument_fb6686") };
}

/** Pointer-level structural difference (display only; the semantic analysis is the server's). */
export function changedPaths(before: unknown, after: unknown, path = ''): string[] {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (isObject(before) && isObject(after)) {
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
    return keys.flatMap((k) => {
      const child = `${path}/${k.replace(/~/g, '~0').replace(/\//g, '~1')}`;
      return !(k in before) || !(k in after) ? [child] : changedPaths(before[k], after[k], child);
    });
  }
  if (Array.isArray(before) && Array.isArray(after)) {
    const out: string[] = [];
    for (let i = 0; i < Math.max(before.length, after.length); i += 1) {
      out.push(...(i >= before.length || i >= after.length ? [`${path}/${i}`] : changedPaths(before[i], after[i], `${path}/${i}`)));
    }
    return out;
  }
  return [path || '/'];
}
