import { useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { codeLabel } from '@/i18n/codes';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { SchemaForm } from '@/editors/SchemaForm';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { asArray, asObject, str } from '@/features/authoring/model';
import { deepEqual, type Json, type JsonObject } from '@/lib/json';
import { useCapabilities } from '@/lib/queries';

/** Registration describes trusted producers; this panel never runs a policy or an action. */
export function ContextProducersPanel() {
  const source = useAuthoringSource(); const caps = useCapabilities();
  const [base, setBase] = useState<JsonObject | null>(null);
  const [producers, setProducers] = useState<Json[]>([]);
  const root = caps.data?.schemas.registry;
  const current = asArray(source.config?.context_producers).map(asObject);
  return <Panel><PanelHeader title={t('b32.producers.title')} description={t('b32.producers.description')}
    aside={<Button disabled={source.loading || !root} onClick={() => { setBase(source.config ?? {}); setProducers(asArray(source.config?.context_producers)); }}>{t('b32.producers.edit')}</Button>} />
    <div className="space-y-4 p-4">
    <p className="text-sm text-muted">{t('b32.producers.hint')}</p>
    {!source.loading && (current.length > 0
      ? <ul className="divide-y divide-line">{current.map(row => <li key={str(row.producer_id)} className="py-2">
        <span>{str(row.display_name)}</span><span className="ml-3 text-sm text-muted">{str(row.producer_id)} · {codeLabel(str(row.kind))}</span>
      </li>)}</ul>
      : <p className="text-sm text-muted">{t('b32.producers.empty')}</p>)}
    {root && base && <DraftEditor title={t('b32.producers.edit')} description={t('b32.producers.activate')} size="lg" base={base} input={producers} dirty={!deepEqual(producers, asArray(base.context_producers))} onClose={() => setBase(null)} plan={() => ({ config: { ...base, config_schema_version: Math.max(2, Number(base.config_schema_version ?? 1)), context_producers: producers } })}>
      <SchemaForm root={root} node={asObject(root.properties).context_producers} value={producers} pointer="/context_producers" label={t('b32.producers.title')} onChange={value => { if (Array.isArray(value)) setProducers(value); }} />
    </DraftEditor>}
  </div></Panel>;
}
