import { useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Notice, PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { asArray, asObject, emptyRegistry, str } from '@/features/authoring/model';
import { api, describeError } from '@/lib/api';
import { deepEqual, isObject, type JsonObject } from '@/lib/json';
import { useActiveRevision } from '@/lib/queries';
import { durationText } from '@/lib/duration';
import { t } from '@/i18n';
import { FreshnessSettings } from './FreshnessSettings';
import { FreshnessAssignment, assignFreshness, type FreshnessChoices } from './FreshnessAssignment';
import { sensorTypeName, sensorTypes, upgradeFreshness } from './freshness';
import { ConfirmSensorTypes, ManageSensorTypes, ShareSensorRules } from './SensorTypes';

interface Usage { source_ids: string[]; own_source_ids: string[]; deviation_source_ids: string[]; contract_ids: string[] }
const emptyConfig = emptyRegistry();
export default function FreshnessPage() {
  const doc = useWorkingDocument(); const active = useActiveRevision();
  const config = doc.editing && doc.config && (doc.draft?.lifecycle ?? 'open') === 'open' ? doc.config : active.isSuccess ? active.data?.config ?? emptyConfig : undefined;
  const [editing, setEditing] = useState<{ base: JsonObject; kind: string; initial: JsonObject; existing?: JsonObject } | null>(null);
  const [rule, setRule] = useState<JsonObject>({});
  const [assigning, setAssigning] = useState<JsonObject | null>(null); const [choices, setChoices] = useState<FreshnessChoices>({});
  const [error, setError] = useState(''); const [saved, setSaved] = useState(false);
  const [typeEditor, setTypeEditor] = useState<{ base: JsonObject; mode: 'manage' | 'confirm' | 'share' } | null>(null);
  const resolution = useQuery({ queryKey: ['freshness', config], enabled: !!config, retry: false, queryFn: () => api<{ templates: Record<string, Usage> }>('registry/freshness', { method: 'POST', body: config! }) });
  const templates = asArray(config?.freshness_templates).filter(isObject);
  const sources = asArray(config?.sources).filter(isObject).filter(source => source.origin !== 'manual');
  const unconfirmed = sources.filter(source => !source.sensor_kind || !source.sensor_kind_origin);
  const version5 = config?.config_schema_version === 5;
  const TypeEditor = typeEditor?.mode === 'confirm' ? ConfirmSensorTypes : typeEditor?.mode === 'share' ? ShareSensorRules : ManageSensorTypes;
  const start = (kind: string, templateId?: string) => {
    if (!config) return;
    const existing = templates.find(item => templateId ? item.template_id === templateId : item.sensor_kind === kind);
    const initial = existing ? asObject(existing.rule) : kind === 'contact' ? { mode: 'report_heartbeat', interval_s: 10800, future_tolerance_s: 1 } : {};
    setRule(initial); setEditing({ base: config, kind, initial, existing });
  };
  const usage = (template: JsonObject) => resolution.data?.templates[str(template.template_id)];
  const selected = editing?.existing;
  const counts = selected ? usage(selected) : undefined;
  return <>
    <PageHeader title={t('b4.title')} description={t('b4.description')} actions={<Button variant="primary" disabled={active.isPending || active.isError || doc.starting} onClick={() => void start('contact')}>{t('b4.edit')}</Button>} />
    {config === doc.config && <Notice tone="info">{t('b4.inDraft')}</Notice>}{saved && <Notice tone="ok">{t('b4.saved')} <Link to={`/changes/drafts/${doc.draft?.draft_id}`}>{t('b4.review')}</Link></Notice>}{error && <Notice tone="bad">{error}</Notice>}
    {active.isPending && !config && <Spinner />}{(active.isError || resolution.isError) && <Notice tone="bad">{t('b4.unavailable')}<Button onClick={() => { void active.refetch(); void resolution.refetch(); }}>{t('b4.retry')}</Button></Notice>}
    <div className="my-4 flex flex-wrap gap-2">
      <Button disabled={!config} onClick={() => {
        if (config && version5) setTypeEditor({ base: config, mode: 'confirm' });
        else { setChoices({}); setAssigning(config ?? null); }
      }}>{t('b4.assign')}</Button>
      <Button disabled={!config} onClick={() => config && setTypeEditor({ base: config, mode: 'manage' })}>{t('b31.sensor.manage')}</Button>
      {version5 && config && <Button onClick={() => setTypeEditor({ base: config, mode: 'share' })}>{t('b31.sensor.share')}</Button>}
    </div>
    {unconfirmed.length > 0 && <Notice tone="info">{t(unconfirmed.length === 1 ? 'b26.freshness.unconfirmedOne' : 'b26.freshness.unconfirmed', { count: unconfirmed.length, own: unconfirmed.filter(source => asObject(source.freshness_selection).kind === 'own' || !!source.freshness).length })}</Notice>}
    <div className="grid gap-4 md:grid-cols-2">{sensorTypes(config ?? emptyConfig).flatMap(({ sensor_kind: kind, display_name: kindName }) => {
      const matching = templates.filter(item => item.sensor_kind === kind);
      const ownCount = sources.filter(source => source.sensor_kind === kind && source.sensor_kind_origin === 'confirmed' && (asObject(source.freshness_selection).kind === 'own' || !!source.freshness)).length;
      return (matching.length ? matching : [undefined]).map(template => {
      const used = template ? usage(template) : undefined;
      return <Panel key={str(template?.template_id) || kind} className="space-y-3 p-4"><h2>{template ? str(template.display_name) : kindName}</h2><p>{template ? asObject(template.rule).mode === 'event_stateful' ? t('b4.noInterval') : durationText(Number(asObject(template.rule).interval_s)) : t(ownCount === 1 ? 'b26.freshness.noTemplateOne' : 'b26.freshness.noTemplate', { count: ownCount })}</p>{used && <p>{t(used.source_ids.length === 1 ? 'b4.sourceOne' : 'b4.sourceCount', { count: used.source_ids.length })} · {t(used.contract_ids.length === 1 ? 'b4.contractOne' : 'b4.contractCount', { count: used.contract_ids.length })} · {t(used.deviation_source_ids.length === 1 ? 'b4.deviationOne' : 'b4.deviations', { count: used.deviation_source_ids.length })}</p>}{used && !used.source_ids.length && <p>{t('b4.empty')}</p>}<Button onClick={() => void start(kind, template ? str(template.template_id) : undefined)}>{t('b4.edit')}</Button><Link className="inline-flex min-h-11 items-center underline" to={`/sources/devices?freshness=${encodeURIComponent(str(template?.template_id))}`}>{t('b4.viewSources')}</Link>{template && <Button disabled={!used || !!used.source_ids.length || !!used.own_source_ids.length} disabledReason={t('b4.deleteBlocked')} onClick={() => void doc.ensureDraft().then(() => doc.editAndSave(current => ({ ...current, freshness_templates: asArray(current.freshness_templates).filter(item => !isObject(item) || item.template_id !== template.template_id) }))).catch(failure => setError(describeError(failure)))}>{t('b4.delete')}</Button>}</Panel>;
    }); })}</div>
    {editing && <DraftEditor base={editing.base} input={rule} dirty={!editing.existing || !deepEqual(rule, editing.initial)} ready={!!rule.mode} title={sensorTypeName(editing.base, editing.kind)} onClose={() => setEditing(null)} onSaved={() => setSaved(true)} plan={() => {
      const next = upgradeFreshness(editing.base);
      const rows = asArray(next.freshness_templates).filter(isObject);
      const existing = rows.find(item => item.template_id === editing.existing?.template_id);
      const clean = { ...rule }; delete clean.liveness_source;
      const row = { template_id: existing?.template_id || `freshness.${editing.kind}`, display_name: existing?.display_name || sensorTypeName(editing.base, editing.kind), sensor_kind: editing.kind, rule: clean };
      return { config: { ...next, freshness_templates: existing ? rows.map(item => item === existing ? row : item) : [...rows, row] } };
    }}>
      <FreshnessSettings value={rule} onChange={setRule} template />{editing.kind === 'contact' && <p>{t('b4.heartbeatHelp')}</p>}<p>{t('b4.affected', { sources: t(counts?.source_ids.length === 1 ? 'b4.sourceOne' : 'b4.sourceCount', { count: counts?.source_ids.length ?? 0 }), contracts: t(counts?.contract_ids.length === 1 ? 'b4.contractOne' : 'b4.contractCount', { count: counts?.contract_ids.length ?? 0 }) })}</p><p>{t(counts?.own_source_ids.length === 1 ? 'b4.keepOwnOne' : 'b4.keepOwn', { count: counts?.own_source_ids.length ?? 0 })}</p>
    </DraftEditor>}
    {typeEditor && <TypeEditor key={typeEditor.mode} base={typeEditor.base} onClose={() => setTypeEditor(null)} onSaved={() => setSaved(true)} />}
    {assigning && <DraftEditor base={assigning} input={choices} dirty={Object.keys(choices).length > 0} ready={Object.keys(choices).length > 0} title={t('b4.assign')} onClose={() => setAssigning(null)} onSaved={() => setSaved(true)} plan={() => ({ config: assignFreshness(assigning, choices) })}>
      <FreshnessAssignment config={assigning} choices={choices} onChange={setChoices} />
    </DraftEditor>}
  </>;
}
