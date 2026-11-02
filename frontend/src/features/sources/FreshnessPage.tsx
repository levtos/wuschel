import { useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Notice, PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { asArray, asObject, str } from '@/features/authoring/model';
import { api, describeError } from '@/lib/api';
import { isObject, type JsonObject } from '@/lib/json';
import { useActiveRevision } from '@/lib/queries';
import { durationText } from '@/lib/duration';
import { invalidDurations } from '@/components/SecondsField';
import { t } from '@/i18n';
import { FreshnessSettings } from './FreshnessSettings';
import { FreshnessAssignment, assignFreshness, type FreshnessChoices } from './FreshnessAssignment';
import { SENSOR_KINDS, upgradeFreshness } from './freshness';

interface Usage { source_ids: string[]; own_source_ids: string[]; deviation_source_ids: string[]; contract_ids: string[] }
export default function FreshnessPage() {
  const doc = useWorkingDocument(); const active = useActiveRevision();
  const config = doc.config && (doc.draft?.lifecycle ?? 'open') === 'open' ? doc.config : active.data?.config;
  const [editing, setEditing] = useState<string | null>(null); const [rule, setRule] = useState<JsonObject>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [assigning, setAssigning] = useState(false); const [choices, setChoices] = useState<FreshnessChoices>({});
  const [error, setError] = useState(''); const [saved, setSaved] = useState(false); const [busy, setBusy] = useState(false);
  const resolution = useQuery({ queryKey: ['freshness', config], enabled: !!config, retry: false, queryFn: () => api<{ templates: Record<string, Usage> }>('registry/freshness', { method: 'POST', body: config! }) });
  const templates = asArray(config?.freshness_templates).filter(isObject);
  const start = async (kind: string, templateId?: string) => { setError(''); try { await doc.ensureDraft(); const existing = templates.find(item => templateId ? item.template_id === templateId : item.sensor_kind === kind); setEditingId(existing ? str(existing.template_id) : null); setRule(existing ? asObject(existing.rule) : kind === 'contact' ? { mode: 'report_heartbeat', interval_s: 10800, future_tolerance_s: 1 } : {}); setEditing(kind); } catch (failure) { setError(describeError(failure)); } };
  const save = async () => { if (!editing || invalidDurations.size || !rule.mode) return; setBusy(true); setError(''); try { await doc.editAndSave(current => { const next = upgradeFreshness(current); const rows = asArray(next.freshness_templates).filter(isObject); const existing = rows.find(item => item.template_id === editingId); const clean = { ...rule }; delete clean.liveness_source; next.freshness_templates = [...rows.filter(item => item !== existing), { template_id: existing?.template_id || `freshness.${editing}`, display_name: existing?.display_name || t(`b4.${editing}`), sensor_kind: editing, rule: clean }]; return next; }); setEditing(null); setSaved(true); } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  const usage = (template: JsonObject) => resolution.data?.templates[str(template.template_id)];
  const selected = templates.find(item => item.template_id === editingId);
  const counts = selected ? usage(selected) : undefined;
  return <>
    <PageHeader title={t('b4.title')} description={t('b4.description')} actions={<Button variant="primary" disabled={active.isPending || active.isError || doc.starting} onClick={() => void start('contact')}>{t('b4.edit')}</Button>} />
    {config === doc.config && <Notice tone="info">{t('b4.inDraft')}</Notice>}{saved && <Notice tone="ok">{t('b4.saved')} <Link to={`/changes/drafts/${doc.draft?.draft_id}`}>{t('b4.review')}</Link></Notice>}{error && <Notice tone="bad">{error}</Notice>}
    {active.isPending && !config && <Spinner />}{(active.isError || resolution.isError) && <Notice tone="bad">{t('b4.unavailable')}<Button onClick={() => { void active.refetch(); void resolution.refetch(); }}>{t('b4.retry')}</Button></Notice>}
    <Button className="my-4" onClick={() => void doc.ensureDraft().then(() => { setChoices({}); setAssigning(true); }).catch(failure => setError(describeError(failure)))}>{t('b4.assign')}</Button>
    <div className="grid gap-4 md:grid-cols-2">{SENSOR_KINDS.flatMap(kind => {
      const matching = templates.filter(item => item.sensor_kind === kind);
      return (matching.length ? matching : [undefined]).map(template => {
      const used = template ? usage(template) : undefined;
      return <Panel key={str(template?.template_id) || kind} className="space-y-3 p-4"><h2>{template ? str(template.display_name) : t(`b4.${kind}`)}</h2><p>{template ? asObject(template.rule).mode === 'event_stateful' ? t('b4.noInterval') : durationText(Number(asObject(template.rule).interval_s)) : t('b4.noRule')}</p>{used && <p>{t(used.source_ids.length === 1 ? 'b4.sourceOne' : 'b4.sourceCount', { count: used.source_ids.length })} · {t(used.contract_ids.length === 1 ? 'b4.contractOne' : 'b4.contractCount', { count: used.contract_ids.length })} · {t(used.deviation_source_ids.length === 1 ? 'b4.deviationOne' : 'b4.deviations', { count: used.deviation_source_ids.length })}</p>}{used && !used.source_ids.length && <p>{t('b4.empty')}</p>}<Button onClick={() => void start(kind, template ? str(template.template_id) : undefined)}>{t('b4.edit')}</Button><Link className="inline-flex min-h-11 items-center underline" to={`/sources/devices?freshness=${encodeURIComponent(str(template?.template_id))}`}>{t('b4.viewSources')}</Link>{template && <Button disabled={!used || !!used.source_ids.length || !!used.own_source_ids.length} disabledReason={t('b4.deleteBlocked')} onClick={() => void doc.ensureDraft().then(() => doc.editAndSave(current => ({ ...current, freshness_templates: asArray(current.freshness_templates).filter(item => !isObject(item) || item.template_id !== template.template_id) }))).catch(failure => setError(describeError(failure)))}>{t('b4.delete')}</Button>}</Panel>;
    }); })}</div>
    <Dialog open={editing !== null} onOpenChange={open => !open && setEditing(null)} title={editing ? t(`b4.${editing}`) : t('b4.title')} footer={<Button variant="primary" disabled={busy || !rule.mode} onClick={() => void save()}>{t('b4.save')}</Button>}>
      <FreshnessSettings value={rule} onChange={setRule} template />{editing === 'contact' && <p>{t('b4.heartbeatHelp')}</p>}<p>{t('b4.affected', { sources: t(counts?.source_ids.length === 1 ? 'b4.sourceOne' : 'b4.sourceCount', { count: counts?.source_ids.length ?? 0 }), contracts: t(counts?.contract_ids.length === 1 ? 'b4.contractOne' : 'b4.contractCount', { count: counts?.contract_ids.length ?? 0 }) })}</p><p>{t(counts?.own_source_ids.length === 1 ? 'b4.keepOwnOne' : 'b4.keepOwn', { count: counts?.own_source_ids.length ?? 0 })}</p>
    </Dialog>
    <Dialog open={assigning} onOpenChange={setAssigning} title={t('b4.assign')} footer={<Button variant="primary" disabled={busy || !Object.keys(choices).length} onClick={() => { if (invalidDurations.size) return; setBusy(true); void doc.editAndSave(current => assignFreshness(current, choices)).then(() => { setAssigning(false); setSaved(true); }).catch(failure => setError(describeError(failure))).finally(() => setBusy(false)); }}>{t('b4.confirmAssignment')}</Button>}>
      {config && <FreshnessAssignment config={config} choices={choices} onChange={setChoices} />}
    </Dialog>
  </>;
}
