import { useState } from 'react';
import { t } from '@/i18n';
import { SchemaForm } from '@/editors/SchemaForm';
import { asArray, asObject } from '@/features/authoring/model';
import { patchObject } from '@/features/authoring/ObjectActions';
import { DraftEditor, useEditorBase } from '@/features/authoring/DraftEditor';
import type { JsonSchema } from '@/lib/capabilities';
import { clone, deepEqual, isObject } from '@/lib/json';
import type { Registry } from '@/lib/types';

export function WakePlanningDialog({ base: openedConfig, contractId, parameterSchema, onClose }: { base?: Registry; contractId: string; parameterSchema: JsonSchema; onClose: () => void }) {
  const base = useEditorBase(openedConfig);
  const index = asArray(base.contracts).findIndex(row => isObject(row) && row.contract_id === contractId);
  const parameters = asObject(asObject(asArray(base.contracts)[index]).parameters);
  const initial = asObject(parameters.wake_settings);
  const [planning, setPlanning] = useState(() => clone(initial));
  const schema = { ...parameterSchema, properties: { wake_settings: asObject(parameterSchema.properties).wake_settings! }, required: ['wake_settings'] };
  return <DraftEditor base={base} dirty={!deepEqual(initial, planning)} input={planning} plan={() => ({ config: patchObject(base, 'contracts', 'contract_id', contractId, { parameters: { ...parameters, wake_settings: planning } }) })} size="xl" onClose={onClose} title={t('b22.wake.edit')} description={t('b22.wake.help')}>
    <SchemaForm root={schema} node={schema} pointer={`/contracts/${index}/parameters`} value={{ wake_settings: planning }} onChange={next => setPlanning(asObject(asObject(next).wake_settings))} />
  </DraftEditor>;
}
