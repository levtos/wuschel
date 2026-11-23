import { t } from '@/i18n';
import { codeLabel, fieldLabel } from '@/i18n/codes';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Collapsible } from '@/components/ui/controls';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { Empty, Notice, Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { asArray, str } from '@/features/authoring/model';
import { api, ApiError, describeError } from '@/lib/api';
import { formatNumber, formatTime } from '@/lib/format';
import { isObject, type JsonObject } from '@/lib/json';
import { useActiveRevision } from '@/lib/queries';
import { CalendarReading, isCalendarCache } from '@/components/CalendarReading';

const ENUM_FIELDS = new Set(['adapter', 'observation_kind', 'availability', 'transform_error', 'source_origin']);
function cell(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return t(value ? 'lib.status.ja_cde9e5' : 'lib.status.nein_90ebc1');
  if (typeof value === 'number') return formatNumber(value);
  if (ENUM_FIELDS.has(key)) return codeLabel(value);
  if (key.endsWith('_at') || ['device_time', 'ha_last_changed', 'ha_last_updated', 'ha_last_reported', 'ha_time_fired'].includes(key)) return formatTime(String(value));
  return String(value);
}
function rows(value: JsonObject, prefix = ''): { key: string; label: string; value: unknown }[] {
  return Object.entries(value).flatMap(([key, item]) => {
    const label = `${prefix}${fieldLabel(key)}`;
    if (isCalendarCache(item)) return [{ key, label, value: item }];
    return isObject(item) ? rows(item, `${label} · `) : Array.isArray(item) ? item.flatMap((entry, index) => isObject(entry) ? rows(entry, `${t('alpha7.evidence.indexed', { label, n: index + 1 })} · `) : [{ key, label: t('alpha7.evidence.indexed', { label, n: index + 1 }), value: entry }]) : [{ key, label, value: item }];
  });
}
/** Stored facts only: receipt is never relabelled as measurement, no reconstruction. */
export function EvidenceTable({ observation }: { observation: JsonObject }) {
  return <div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">{t('alpha7.evidence.table')}</caption><thead><tr className="border-b border-line"><th scope="col" className="px-3 py-2">{t('alpha7.evidence.field')}</th><th scope="col" className="px-3 py-2">{t('alpha7.evidence.value')}</th></tr></thead><tbody>{rows(observation).map((row, index) => <tr key={`${row.label}:${index}`} className="border-b border-line/60"><th scope="row" className="w-64 px-3 py-2 font-medium text-muted">{row.label}</th><td className="break-words px-3 py-2">{isCalendarCache(row.value) ? <CalendarReading cache={row.value} /> : cell(row.key, row.value)}</td></tr>)}</tbody></table></div>;
}
function useEvidence(reference: string | null) {
  return useQuery({ queryKey: ['evidence', 'reference', reference], queryFn: () => api<JsonObject>(`evidence/reference/${encodeURIComponent(reference!)}`), enabled: !!reference, retry: false });
}
export function EvidenceResult({ reference }: { reference: string }) {
  const query = useEvidence(reference);
  if (query.isPending) return <Spinner />;
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.code === 'evidence_outside_retention') return <Notice tone="info" title={t('alpha8.retention.evidenceTitle')}>{describeError(query.error)}</Notice>;
    const notFound = query.error instanceof ApiError && query.error.code === 'evidence_not_found';
    return <Notice tone={notFound ? 'info' : 'bad'} title={t(notFound ? 'features.diagnostics.EvidenceLookup.keine_aufgezeichnete_evidenz_b25154' : 'features.diagnostics.EvidenceLookup.evidenz_nicht_lesbar_f315c2')}>{notFound ? t('features.diagnostics.EvidenceLookup.zu_dieser_referenz_liegt_keine_beobachtung_vor_z_825a45') : describeError(query.error)}</Notice>;
  }
  return <EvidenceTable observation={query.data} />;
}
export function EvidenceDialog({ reference, onClose }: { reference: string; onClose: () => void }) {
  return <Dialog open onOpenChange={(open) => !open && onClose()} size="lg" title={t('features.diagnostics.EvidenceLookup.evidenz_88ffe3')}><EvidenceResult reference={reference} /></Dialog>;
}
export function EvidenceTab({ initialReference = '' }: { initialReference?: string }) {
  const active = useActiveRevision();
  const sources = asArray(active.data?.config.sources).filter(isObject);
  const [sourceId, setSourceId] = useState('');
  const [input, setInput] = useState(initialReference);
  const [reference, setReference] = useState(initialReference);
  useEffect(() => { setInput(initialReference); setReference(initialReference); }, [initialReference]);
  const source = useQuery({ queryKey: ['evidence', 'source', sourceId], queryFn: () => api<JsonObject[]>(`evidence/${encodeURIComponent(sourceId)}`), enabled: !!sourceId, retry: false });
  return <div className="space-y-4">
    <Panel>
      <PanelHeader title={t('alpha7.evidence.title')} description={t('alpha7.evidence.help')} />
      <div className="space-y-4 p-4">
        {active.isPending ? <Spinner /> : active.isError ? <Notice tone="bad" title={t('alpha7.evidence.sourcesFailed')}>{describeError(active.error)}</Notice> : sources.length === 0 ? <Empty title={t('alpha7.evidence.noSources')} /> : <Field label={t('alpha7.evidence.source')}>{props => <Select {...props} value={sourceId} onChange={event => { setSourceId(event.target.value); setReference(''); }}><option value="">{t('alpha7.evidence.choose')}</option>{sources.map(item => <option key={str(item.source_id)} value={str(item.source_id)}>{str(item.display_name) || str(item.source_id)}</option>)}</Select>}</Field>}
        {sourceId && source.isPending && <Spinner />}
        {source.isError && <Notice tone="bad" title={t('features.diagnostics.EvidenceLookup.nicht_lesbar_05abf0')}>{describeError(source.error)}</Notice>}
        {sourceId && source.data && (source.data.length === 0 ? <Empty title={t('features.diagnostics.EvidenceLookup.keine_aktuelle_beobachtung_4e0775')} /> : source.data.map((observation, index) => <section key={`${sourceId}:${index}`} className="rounded-lg border border-line"><h3 className="border-b border-line px-3 py-2 font-medium">{t('alpha7.evidence.observation', { n: index + 1 })}</h3><EvidenceTable observation={observation} /></section>))}
      </div>
    </Panel>
    <Collapsible title={t('alpha7.evidence.advanced')} defaultOpen={!!initialReference}>
      <div className="space-y-3">
        <Field label={t('features.diagnostics.EvidenceLookup.evidence_referenz_fd58d0')} hint={t('alpha7.evidence.referenceHelp')}>{props => <Input {...props} value={input} onChange={event => setInput(event.target.value)} />}</Field>
        <Button disabled={!input.trim()} onClick={() => setReference(input.trim())}>{t('features.diagnostics.EvidenceLookup.auflosen_6413ae')}</Button>
        {!input.trim() && <p className="text-xs text-muted">{t('alpha7.evidence.enterReference')}</p>}
        {reference && <EvidenceResult reference={reference} />}
      </div>
    </Collapsible>
  </div>;
}
