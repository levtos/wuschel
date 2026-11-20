import { t } from '@/i18n';
import { ActionCancelled } from '@/lib/api';
import { deepEqual, isObject, type Json } from '@/lib/json';
import type { Registry } from '@/lib/types';

export class EditorInputError extends Error {}
export class SavedDraftAccepted extends ActionCancelled {}

export const registryIdentities: Record<string, string[]> = {
  contracts: ['contract_id'], definitions: ['type_id', 'type_version'],
  sources: ['source_id'], bindings: ['binding_id'], dependencies: ['dependency_id'],
  catalogs: ['catalog_id', 'version'], freshness_templates: ['template_id'],
};

/** Apply the local changes onto the chosen draft, preserving unrelated draft edits. */
export function applyEditorChanges(base: Registry, edited: Registry, target: Registry): Registry {
  return apply(base, edited, target) as Registry;
}

function apply(base: Json | undefined, edited: Json | undefined, target: Json | undefined, collection?: string): Json | undefined {
  if (deepEqual(base, edited) || deepEqual(edited, target)) return target;
  if (deepEqual(base, target)) return edited;

  if (isObject(base) && isObject(edited) && isObject(target)) {
    return Object.fromEntries([...new Set([...Object.keys(base), ...Object.keys(edited), ...Object.keys(target)])].flatMap(key => {
      const value = apply(base[key], edited[key], target[key], collection === undefined ? key : '');
      return value === undefined ? [] : [[key, value]];
    }));
  }

  const keys = collection ? registryIdentities[collection] : undefined;
  if (keys && Array.isArray(edited) && Array.isArray(target) && (base === undefined || Array.isArray(base))) {
    const indexed = (rows: Json[]) => {
      const entries = new Map<string, Json>();
      for (const row of rows) {
        if (!isObject(row) || keys.some(key => row[key] === undefined)) return null;
        const id = JSON.stringify(keys.map(key => row[key]));
        if (entries.has(id)) return null;
        entries.set(id, row);
      }
      return entries;
    };
    const before = indexed(base ?? []);
    const after = indexed(edited);
    const current = indexed(target);
    if (before && after && current) {
      return [...new Set([...current.keys(), ...after.keys()])].flatMap(id => {
        const row = apply(before.get(id), after.get(id), current.get(id), '');
        return row === undefined ? [] : [row];
      });
    }
  }
  throw new EditorInputError(t('a2.edit.targetConflict'));
}
