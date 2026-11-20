import { useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Collapsible } from '@/components/ui/controls';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { SchemaForm } from '@/editors/SchemaForm';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { asArray, asObject } from '@/features/authoring/model';
import { deepEqual, type Json, type JsonObject } from '@/lib/json';
import { useCapabilities } from '@/lib/queries';

/** Registration describes trusted producers; this panel never runs a policy or an action. */
export function ContextProducersPanel() {
  const source = useAuthoringSource(); const caps = useCapabilities();
  const [base, setBase] = useState<JsonObject | null>(null);
  const [producers, setProducers] = useState<Json[]>([]);
  const root = caps.data?.schemas.registry;
  return <Panel><PanelHeader title={t('alpha9.context.automatic')} /><div className="space-y-4 p-4">
    <Button disabled={source.loading || !root} onClick={() => { setBase(source.config ?? {}); setProducers(asArray(source.config?.context_producers)); }}>{t('alpha7.mode.edit')}</Button>
    <Collapsible title={t('alpha7.device.advanced')}><fieldset disabled>
      {root && <SchemaForm root={root} node={asObject(root.properties).context_producers} value={source.config?.context_producers ?? []} pointer="/context_producers" label={t('alpha9.context.automatic')} onChange={() => undefined} />}
    </fieldset></Collapsible>
    {root && base && <DraftEditor title={t('alpha9.context.automatic')} size="lg" base={base} input={producers} dirty={!deepEqual(producers, asArray(base.context_producers))} onClose={() => setBase(null)} plan={() => ({ config: { ...base, config_schema_version: Math.max(2, Number(base.config_schema_version ?? 1)), context_producers: producers } })}>
      <SchemaForm root={root} node={asObject(root.properties).context_producers} value={producers} pointer="/context_producers" label={t('alpha9.context.automatic')} onChange={value => { if (Array.isArray(value)) setProducers(value); }} />
    </DraftEditor>}
  </div></Panel>;
}
