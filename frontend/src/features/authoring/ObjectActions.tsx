import { useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input } from '@/components/ui/form';
import { describeError } from '@/lib/api';
import { isObject, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { asArray, str } from './model';
import { useWorkingDocument } from './WorkingDocument';

/** Identity, not the selected list index, owns an edit after opening a draft. */
export function patchObject(config: Registry, collection: string, key: string, id: string, patch: JsonObject): Registry {
  if (!asArray(config[collection]).some(row => isObject(row) && row[key] === id)) throw new Error(t('b22.object.missing'));
  return { ...config, [collection]: asArray(config[collection]).map(row => isObject(row) && row[key] === id ? { ...row, ...patch, [key]: id } : row) };
}

export function ObjectActions({ collection, idKey, value, editing, onEdit, onSaved, rename = true, toggle = false, primary = false }: {
  collection: string; idKey: string; value: JsonObject; editing: boolean;
  onEdit: () => void | Promise<void>; onSaved?: () => void; rename?: boolean; toggle?: boolean; primary?: boolean;
}) {
  const doc = useWorkingDocument();
  const [renaming, setRenaming] = useState(false); const [name, setName] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const id = str(value[idKey]);
  const save = async (patch: JsonObject) => {
    setBusy(true); setError('');
    try { await doc.ensureDraft(); await doc.editAndSave(config => patchObject(config, collection, idKey, id, patch)); setRenaming(false); onSaved?.(); }
    catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  return <>
    <div className="flex flex-wrap items-center gap-2" aria-label={t('b22.object.actions')}>
      <Button variant={primary ? 'primary' : 'secondary'} disabled={busy || doc.starting || doc.saving} onClick={() => void onEdit()}>{t(editing ? 'b22.object.finish' : 'b22.object.edit')}</Button>
      {rename && <Button disabled={busy || doc.starting || doc.saving} onClick={() => { setName(str(value.display_name)); setError(''); setRenaming(true); }}>{t('b22.object.rename')}</Button>}
      {toggle && <Button disabled={busy || doc.starting || doc.saving} onClick={() => void save({ enabled: value.enabled === false })}>{t(value.enabled === false ? 'b22.object.enable' : 'b22.object.disable')}</Button>}
    </div>
    {error && !renaming && <p role="alert">{error}</p>}
    <Dialog open={renaming} onOpenChange={open => !busy && setRenaming(open)} title={t('b22.object.rename')} description={t('b22.object.renameHelp')} footer={<><Button disabled={busy} onClick={() => setRenaming(false)}>{t('common.cancel')}</Button><Button variant="primary" disabled={busy || !name.trim()} onClick={() => void save({ display_name: name.trim() })}>{t('b22.object.save')}</Button></>}>
      <Field label={t('b22.object.name')}>{props => <Input {...props} autoFocus value={name} maxLength={128} onChange={event => setName(event.target.value)} />}</Field>
      {error && <p role="alert">{error}</p>}
    </Dialog>
  </>;
}
