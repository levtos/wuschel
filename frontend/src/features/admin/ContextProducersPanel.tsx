import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Collapsible } from '@/components/ui/controls';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { SchemaForm } from '@/editors/SchemaForm';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { asObject } from '@/features/authoring/model';
import { useCapabilities } from '@/lib/queries';

/** Registration describes trusted producers; this panel never runs a policy or an action. */
export function ContextProducersPanel() {
  const source = useAuthoringSource(); const doc = useWorkingDocument(); const caps = useCapabilities();
  const root = caps.data?.schemas.registry;
  return <Panel><PanelHeader title={t('alpha9.context.automatic')} /><div className="space-y-4 p-4">
    {!source.editable && <Button disabled={doc.starting} onClick={() => void doc.ensureDraft().catch(() => { /* Shared DraftBar displays preparation errors. */ })}>{t('alpha7.mode.edit')}</Button>}
    <p className="text-sm text-muted">{t('alpha8.parameters.draftHint')}</p>
    <Collapsible title={t('alpha7.device.advanced')}><fieldset disabled={!source.editable}>
      {root && <SchemaForm root={root} node={asObject(root.properties).context_producers} value={source.config?.context_producers ?? []} pointer="/context_producers" label={t('alpha9.context.automatic')} onChange={value => {
        if (Array.isArray(value)) doc.edit(current => ({ ...current, config_schema_version: Math.max(2, Number(current.config_schema_version ?? 1)), context_producers: value }));
      }} />}
    </fieldset></Collapsible>
  </div></Panel>;
}
