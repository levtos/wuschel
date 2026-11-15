import { useState } from 'react';
import { Power, PowerOff, TextCursorInput } from 'lucide-react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { EditButton } from '@/components/EditButton';
import { ConfirmDialog, Dialog } from '@/components/ui/dialog';
import { Field, Input } from '@/components/ui/form';
import { describeError } from '@/lib/api';
import { isObject, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { asArray, str } from './model';
import { consumersOfContract, resolveRef } from './usage';
import { typeCatalog } from './catalog';
import { useCapabilities } from '@/lib/queries';
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
  const caps = useCapabilities();
  const [renaming, setRenaming] = useState(false); const [name, setName] = useState('');
  const [confirming, setConfirming] = useState<'rename' | 'toggle' | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const id = str(value[idKey]); const objectName = str(value.display_name) || id;
  const affected = new Set<string>();
  const visit = (contract: string) => { for (const child of consumersOfContract(doc.config, contract)) if (!affected.has(child)) { affected.add(child); visit(child); } };
  if (collection === 'contracts') visit(id);
  const affectedNames = [...affected].map(target => str(asArray(doc.config?.contracts).filter(isObject).find(row => row.contract_id === target)?.display_name) || target);
  const types = typeCatalog(caps.data, doc.config);
  const requiredConsumers = collection !== 'contracts' ? [] : asArray(doc.config?.contracts).filter(isObject).filter(row => {
    if (row.enabled === false || row.contract_id === id) return false;
    const type = types.find(item => item.type_id === row.type_id && item.type_version === row.type_version);
    return asArray(row.inputs).filter(isObject).some(input => resolveRef(str(input.ref), doc.config)?.id === id && type?.inputs.some(slot => slot.name === input.name && slot.required));
  }).map(row => `${str(row.display_name) || str(row.contract_id)} (${str(row.contract_id)})`);
  const save = async (patch: JsonObject) => {
    if (!editing) return;
    setBusy(true); setError('');
    try { await doc.ensureDraft(); await doc.editAndSave(config => patchObject(config, collection, idKey, id, patch)); setConfirming(null); setRenaming(false); onSaved?.(); }
    catch (failure) { setConfirming(null); setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  const disabled = busy || doc.starting || doc.saving;
  return <>
    <div className="flex flex-nowrap items-center gap-2" aria-label={t('b22.object.actions')}>
      {editing ? <Button variant={primary ? 'primary' : 'secondary'} disabled={disabled} onClick={() => void onEdit()}>{t('b22.object.finish')}</Button> : <EditButton variant={primary ? 'primary' : 'ghost'} disabled={disabled} onClick={() => void onEdit()} />}
      {editing && rename && <Button disabled={disabled} aria-label={t('b22.object.rename')} onClick={() => { setName(str(value.display_name)); setError(''); setRenaming(true); }}><TextCursorInput size={16} aria-hidden="true" />{t('b22.object.rename')}</Button>}
      {editing && toggle && <Button disabled={disabled} aria-label={t(value.enabled === false ? 'b22.object.enable' : 'b22.object.disable')} onClick={() => setConfirming('toggle')}>{value.enabled === false ? <Power size={16} aria-hidden="true" /> : <PowerOff size={16} aria-hidden="true" />}{t(value.enabled === false ? 'b22.object.enable' : 'b22.object.disable')}</Button>}
    </div>
    {error && !renaming && <p role="alert">{error}</p>}
    <Dialog open={editing && renaming} onOpenChange={open => !busy && setRenaming(open)} title={t('b22.object.rename')} description={t('b22.object.renameHelp')} footer={<><Button disabled={busy} onClick={() => setRenaming(false)}>{t('common.cancel')}</Button><Button variant="primary" disabled={busy || !name.trim() || name.trim() === str(value.display_name)} onClick={() => setConfirming('rename')}>{t('b22.object.save')}</Button></>}>
      <Field label={t('b22.object.name')}>{props => <Input {...props} autoFocus value={name} maxLength={128} onChange={event => setName(event.target.value)} />}</Field>
      {error && <p role="alert">{error}</p>}
    </Dialog>
    <ConfirmDialog open={editing && confirming !== null} onOpenChange={open => !open && !busy && setConfirming(null)} busy={busy} title={t(confirming === 'rename' ? 'b22.object.rename' : value.enabled === false ? 'b22.object.enable' : 'b22.object.disable')} description={confirming === 'rename' ? t('b26.object.renameConfirm', { old: objectName, next: name.trim() }) : t('b26.object.toggleConfirm', { name: objectName, action: t(value.enabled === false ? 'b22.object.enable' : 'b22.object.disable'), affected: affectedNames.join(', ') || t('b26.object.none') })} confirmLabel={t('b22.object.save')} onConfirm={() => void save(confirming === 'rename' ? { display_name: name.trim() } : { enabled: value.enabled === false })}>{confirming === 'toggle' && value.enabled !== false && requiredConsumers.length > 0 && <p>{t('b26.object.requiredConsumers', { names: requiredConsumers.join(', ') })}</p>}</ConfirmDialog>
  </>;
}
