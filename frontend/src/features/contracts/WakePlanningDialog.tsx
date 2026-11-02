import { useRef, useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { SchemaForm } from '@/editors/SchemaForm';
import { asArray, asObject } from '@/features/authoring/model';
import { patchObject } from '@/features/authoring/ObjectActions';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { describeError } from '@/lib/api';
import type { JsonSchema } from '@/lib/capabilities';
import { clone, isObject, type JsonObject } from '@/lib/json';

export function WakePlanningDialog({ contractId, parameterSchema, onClose }: { contractId: string; parameterSchema: JsonSchema; onClose: () => void }) {
  const doc = useWorkingDocument(); const owner = useRef(doc.draft?.draft_id);
  const contract = asArray(doc.config?.contracts).filter(isObject).find(row => row.contract_id === contractId);
  const [planning, setPlanning] = useState<JsonObject>(() => clone(asObject(asObject(contract?.parameters).wake_settings)));
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const schema = { ...parameterSchema, properties: { wake_settings: asObject(parameterSchema.properties).wake_settings! }, required: ['wake_settings'] };
  const save = async () => {
    if (owner.current !== doc.draft?.draft_id) { setError(t('b22.object.changed')); return; }
    setBusy(true); setError('');
    try {
      await doc.editAndSave(config => {
        const current = asArray(config.contracts).filter(isObject).find(row => row.contract_id === contractId);
        return patchObject(config, 'contracts', 'contract_id', contractId, { parameters: { ...asObject(current?.parameters), wake_settings: planning } });
      });
      onClose();
    } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); }
  };
  return <Dialog open size="xl" onOpenChange={open => !open && !busy && onClose()} title={t('b22.wake.edit')} description={t('b22.wake.help')} footer={<><Button disabled={busy} onClick={onClose}>{t('common.cancel')}</Button><Button variant="primary" disabled={busy} onClick={() => void save()}>{t('b22.object.save')}</Button></>}>
    <SchemaForm root={schema} node={schema} pointer="/parameters" value={{ wake_settings: planning }} onChange={next => setPlanning(asObject(asObject(next).wake_settings))} />
    {error && <p role="alert">{error}</p>}
  </Dialog>;
}
