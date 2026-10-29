import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useInfo } from '@/lib/queries';
import type { JsonObject } from '@/lib/json';
import { str } from '@/features/authoring/model';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';

function remembered(key: string) { try { return sessionStorage.getItem(key); } catch { return null; } }

/** Remember identities, never indexes or values; each installation/draft/collection has its own selection. */
export function useSourceSelection(name: string, list: JsonObject[], idKey: string, editing: boolean) {
  const info = useInfo(); const doc = useWorkingDocument(); const [params] = useSearchParams();
  const scope = `${doc.editing ? doc.draft?.draft_id ?? '' : 'active'}:${name}`;
  const key = `terra.sourceSelection:${info.data?.installation_id ?? ''}:${scope}`;
  const [choice, setChoice] = useState(() => ({ key, id: remembered(key) }));
  const initialKey = `terra.sourceSelection::${scope}`;
  const id = choice.key === key || choice.key === initialKey ? choice.id : remembered(key);
  const found = id !== null ? list.findIndex(item => item[idKey] === id) : -1;
  const last = useRef<{ key: string; index: number; length: number } | null>(null);
  if (found >= 0) last.current = { key, index: found, length: list.length };
  const selected = found >= 0 ? found : editing && id !== null && last.current?.key === key && last.current.length === list.length && list[last.current.index] ? last.current.index : -1;
  const selectIdentity = (next: string | null) => {
    setChoice({ key, id: next });
    if (!info.data?.installation_id) return;
    try { if (next) sessionStorage.setItem(key, next); else sessionStorage.removeItem(key); } catch { /* optional UI convenience */ }
  };
  const setSelected = (index: number | null) => selectIdentity(index === null ? null : str(list[index]?.[idKey]) || null);
  useEffect(() => {
    if (info.data?.installation_id && choice.key === initialKey) selectIdentity(choice.id);
  }, [key, initialKey, info.data?.installation_id]);
  // Navigation links may specify an index, including older validation links. Persist its identity once loaded.
  const requested = params.has('item') && (!params.has('tab') || params.get('tab') === name) ? Number(params.get('item')) : null;
  const requestedId = name === 'catalogs' && params.get('catalog_id') ? params.get('catalog_id')! : requested !== null && Number.isInteger(requested) ? str(list[requested]?.[idKey]) : '';
  useEffect(() => {
    if (requestedId && (choice.key !== key || choice.id !== requestedId)) {
      setChoice({ key, id: requestedId });
      try { if (info.data?.installation_id) sessionStorage.setItem(key, requestedId); } catch { /* optional UI convenience */ }
    }
  }, [requestedId, key, info.data?.installation_id]); // choice is deliberately read only when navigation changes
  useEffect(() => {
    if (selected >= 0 && str(list[selected]?.[idKey]) !== id) selectIdentity(str(list[selected]?.[idKey]));
    else if (editing && id && list.length && !list.some(item => item[idKey] === id)) setSelected(list.length - 1);
  }, [editing, id, list.length, key, selected, list[selected]?.[idKey]]); // follows an in-place rename without unmounting the editor
  return [selected >= 0 ? selected : null, setSelected, selectIdentity] as const;
}
