import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { t } from '@/i18n';
import { fieldLabel } from '@/i18n/codes';
import { Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { ContractLiveState } from '@/components/ContractLiveState';
import { CommandsPanel } from '@/features/diagnostics/CommandsPanel';
import { useHaCatalog } from '@/features/sources/useHaCatalog';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import { formatDuration, formatTime } from '@/lib/format';
import { api } from '@/lib/api';
import { valueText } from '@/lib/valueText';
import { displayValue, FIELD_STATUS } from '@/lib/status';
import { POLL_MS } from '@/lib/queries';
import { useHistoryWindow, useWindowHistory } from './historyWindow';
import type { Envelope, FieldValue, HistoryItem, Registry } from '@/lib/types';
import { problemAdvice } from '@/features/overview/overviewModel';
import { contractSources, contractState } from './presentation';
import { ContractStateChip } from './ContractStateChip';
import { MachineRestartPanel } from './MachineRestartPanel';
import { ManualSourcePanel } from '@/features/sources/ManualSourcePanel';

interface Point { at: string; field: FieldValue | null; sampleAt?: string }
export function historyPoints(rows: HistoryItem[], contractId: string, field: string, revision?: number, gaps: HistoryItem[] = []): Point[] {
  const points = rows.filter(row => row.contract_id === contractId && (revision === undefined || row.registry_revision === revision) && typeof row.published_at === 'string' && Number.isFinite(Date.parse(row.published_at)))
    .sort((a, b) => Date.parse(String(a.published_at)) - Date.parse(String(b.published_at)) || Number(a.publication_seq ?? 0) - Number(b.publication_seq ?? 0))
    .map(row => ({ at: String(row.published_at), field: isObject(asObject(row.fields)[field]) ? asObject(row.fields)[field] as unknown as FieldValue : null }));
  return [...points, ...gaps.filter(row => typeof row.at === 'string' && Date.parse(row.at) >= Date.parse(points[0]?.at ?? '')).map(row => ({ at: String(row.at), field: null }))].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

/** Stored samples plus the currently confirmed boolean tail; gaps stay gaps. */
export function MiniHistory({ points, schema, name, current, now = Date.now(), since, until }: { points: Point[]; schema?: FieldSchema; name: string; current?: FieldValue; now?: number; since?: number; until?: number }) {
  if (since !== undefined && until !== undefined) {
    const boundary = points.filter(point => Date.parse(point.at) < since).at(-1);
    points = [...(boundary ? [{ ...boundary, sampleAt: boundary.at, at: new Date(since).toISOString() }] : []), ...points.filter(point => Date.parse(point.at) >= since && Date.parse(point.at) <= until)];
  }
  const tail = points.at(-1);
  const extend = current?.status === 'valid' && typeof current.value === 'boolean' && tail?.field?.status === 'valid' && tail.field.value === current.value && Date.parse(tail.at) < now;
  const drawn = extend ? [...points, { at: new Date(now).toISOString(), field: current }] : points;
  const categories = schema?.type === 'enum' ? asArray(schema.value_catalog).filter((value): value is string => typeof value === 'string') : [...new Set(points.map(point => point.field?.value).filter((value): value is string => typeof value === 'string'))].sort();
  const ordinate = (field: FieldValue | null) => !field || !['valid', 'held'].includes(field.status) || field.value === null ? null : typeof field.value === 'number' && Number.isFinite(field.value) ? field.value : typeof field.value === 'boolean' ? Number(field.value) : typeof field.value === 'string' ? categories.indexOf(field.value) : null;
  const values = points.map(point => ordinate(point.field)).filter((value): value is number => value !== null);
  const boolean = schema?.type === 'bool' || points.some(point => typeof point.field?.value === 'boolean');
  const low = boolean || categories.length || !values.length ? 0 : Math.min(...values); const high = boolean || !values.length ? 1 : categories.length ? categories.length - 1 : Math.max(...values) === low ? low + 1 : Math.max(...values); const range = high - low || 1;
  const first = since ?? Date.parse(drawn[0]?.at ?? ''); const last = until ?? Date.parse(drawn.at(-1)?.at ?? '');
  const xFor = (at: string) => 110 + (Date.parse(at) - first) / (last - first || 1) * 870;
  const yFor = (value: number) => categories.length ? 32 + value / range * 100 : 132 - (value - low) / range * 100;
  const title = (point: Point) => `${formatTime(point.sampleAt ?? point.at)} · ${point.field ? displayValue(point.field, typeof schema?.unit === 'string' ? schema.unit : undefined, schema) : t('alpha7.contract.noValue')} · ${point.field ? FIELD_STATUS[point.field.status].label : t('alpha7.contract.noValue')}`;
  const segments: { x: number; y: number; title: string }[][] = [[]];
  for (const point of drawn) {
    const value = ordinate(point.field);
    if (value === null) { if (segments.at(-1)!.length) segments.push([]); continue; }
    const x = xFor(point.at); const y = yFor(value);
    segments.at(-1)!.push({ x, y, title: title(point) });
  }
  const label = (value: number) => boolean ? t(value ? 'lib.status.ja_cde9e5' : 'lib.status.nein_90ebc1') : categories.length ? valueText(categories[value], schema) : displayValue({ status: 'valid', value, quality: 'healthy', evidence: [], reasons: [] }, typeof schema?.unit === 'string' ? schema.unit : undefined);
  const path = (segment: { x: number; y: number }[]) => segment.flatMap((point, index) => (boolean || categories.length > 0) && index > 0 ? [`${point.x},${segment[index - 1]!.y}`, `${point.x},${point.y}`] : [`${point.x},${point.y}`]).join(' ');
  return <div className="space-y-2">
    <h3 className="text-sm font-medium">{fieldLabel(name)}</h3>
    {drawn.length > 0 ? <svg viewBox="0 0 1000 165" role="img" aria-label={t('alpha7.contract.chart', { name: fieldLabel(name) })} className="block h-auto w-full text-muted">
      <path d="M110 24V140H980" fill="none" stroke="currentColor" opacity="0.5" />
      {(categories.length ? categories.map((_, index) => index) : boolean ? [0, 1] : values.length ? [low, (low + high) / 2, high] : []).map(value => <g key={value}><text x="98" y={yFor(value) + 4} textAnchor="end" fontSize="13" fill="currentColor">{label(value)}</text><path d={`M110 ${yFor(value)}H980`} fill="none" stroke="currentColor" opacity="0.15" /></g>)}
      {segments.filter(segment => segment.length).map((segment, index) => <g key={index}><polyline data-interpolation={boolean || categories.length ? 'step' : 'linear'} points={path(segment)} fill="none" stroke="currentColor" strokeWidth="2" />{segment.map((point, n) => <circle key={n} cx={point.x} cy={point.y} r="3" fill="currentColor" tabIndex={0}><title>{point.title}</title></circle>)}</g>)}
      {points.filter(point => ordinate(point.field) === null).map((point, index) => <rect key={index} x={xFor(point.at) - 3} y="145" width="6" height="6" tabIndex={0}><title>{title(point)}</title></rect>)}
    </svg> : <p className="text-sm text-muted">{t('alpha7.contract.noHistoryValue')}</p>}
    {points.some(point => ordinate(point.field) === null) && <p className="text-xs text-muted">{t('beta5.history.gaps')}</p>}
    {drawn.length > 0 && <div className="flex justify-between gap-4 text-xs text-muted"><time dateTime={new Date(first).toISOString()}>{formatTime(new Date(first).toISOString())}</time><time dateTime={new Date(last).toISOString()}>{formatTime(new Date(last).toISOString())}</time></div>}
    <details className="text-xs"><summary className="cursor-pointer text-muted">{t('alpha7.contract.historyTable')}</summary><table className="mt-2 w-full text-left"><caption className="sr-only">{t('alpha7.contract.historyTable')}</caption><thead><tr><th>{t('alpha7.contract.time')}</th><th>{t('alpha7.contract.value')}</th></tr></thead><tbody>{points.slice(-8).reverse().map((point, index) => <tr key={index}><td className="py-1">{formatTime(point.sampleAt ?? point.at)}</td><td>{point.field ? displayValue(point.field, typeof schema?.unit === 'string' ? schema.unit : undefined, schema) : t('alpha7.contract.noValue')}</td></tr>)}</tbody></table></details>
  </div>;
}

/** Measurement clock from stored observations, never the receipt time or reason onset. */
export function observationTime(observation: JsonObject): string | undefined {
  const haTime = observation.observation_kind === 'report' ? observation.ha_last_reported : observation.observation_kind === 'live_change' ? observation.ha_time_fired || observation.ha_last_changed : observation.ha_last_reported || observation.ha_last_changed || observation.ha_time_fired;
  const value = str(observation.device_time || haTime);
  return value && Number.isFinite(Date.parse(value)) ? value : undefined;
}

import { freshnessLabel } from '@/features/sources/freshness';

export function ContractSummary({ envelope, stale, fields, config, machine }: { envelope: Envelope; stale: boolean; fields: Record<string, FieldSchema>; config: Registry | null; machine: boolean }) {
  const [advanced, setAdvanced] = useState(false);
  const ha = useHaCatalog(asArray(config?.bindings).filter(isObject).some(binding => isObject(binding.adapter) && String(binding.adapter.kind).startsWith('ha_')));
  const sources = contractSources(envelope.contract_id, config, ha.entities);
  const window = useHistoryWindow();
  const history = useWindowHistory('contracts', envelope.contract_id, window.since, window.until);
  const gaps = useWindowHistory('gaps', undefined, window.since, window.until);
  const reasons = Object.values(envelope.fields).flatMap(field => field.reasons);
  const contract = asArray(config?.contracts).filter(isObject).find(item => item.contract_id === envelope.contract_id);
  const affected = sources.find(source => reasons.some(reason => source.id === reason.source_id || source.id === reason.input || asArray(contract?.inputs).filter(isObject).some(input => input.name === reason.input && input.ref === source.id)));
  const affectedName = affected ? str(asArray(config?.sources).filter(isObject)[affected.sourceIndex]?.display_name) || affected.name : '';
  const evidence = useQuery({ queryKey: ['evidence', 'source', affected?.id, envelope.registry_revision], queryFn: () => api<JsonObject[]>(`evidence/${encodeURIComponent(affected!.id)}`), enabled: !!affected, refetchInterval: POLL_MS, retry: false });
  const measured = (evidence.isPlaceholderData ? [] : evidence.data ?? []).map(observationTime).filter((at): at is string => !!at).sort().at(-1);
  const age = measured && Date.parse(measured) <= Date.now() ? formatDuration((Date.now() - Date.parse(measured)) / 1000) : null;
  const why = stale ? t('alpha7.contract.state.oldConnectionHelp') : reasons.length ? problemAdvice(reasons, config, envelope.contract_id).text : contractState(envelope, true).hint;
  return <div className="space-y-4">
    {Object.values(envelope.fields).some(field => field.configuration?.complete === false) && <Panel className="p-5"><h3 className="font-medium">{t('alpha8.configuration.title')}</h3><p className="text-sm text-muted">{t('alpha8.configuration.validHint')}</p><ul className="mt-2 text-sm">{Object.entries(envelope.fields).flatMap(([name, field]) => (field.configuration?.hints ?? []).map((hint, index) => <li key={`${name}:${index}`}>{hint.message || t('alpha8.configuration.unassigned')}</li>))}</ul></Panel>}
    <Panel><PanelHeader title={t('alpha7.contract.now')} aside={<ContractStateChip envelope={envelope} enabled={envelope.enabled} stale={stale} />} />
      <div className="flex flex-wrap gap-x-12 gap-y-5 p-6">{Object.entries(envelope.fields).map(([name, field]) => <div key={name}>
        {Object.keys(envelope.fields).length > 1 && <p className="mb-1 text-sm text-muted">{fieldLabel(name)}</p>}
        <p className="text-4xl font-semibold tabular-nums" data-testid={`value-${name}`}>{displayValue(field, typeof fields[name]?.unit === 'string' ? fields[name].unit : undefined, fields[name])}</p>
        {field.value_origin && <p className="mt-2 text-sm text-muted">{t('alpha9.valueOrigin.label')}: {t(`alpha9.valueOrigin.${field.value_origin.kind}`)}</p>}
        {!!field.input_origins?.length && <p className="mt-1 text-xs text-muted">{t('alpha9.valueOrigin.inputs', { kinds: [...new Set(field.input_origins.map(origin => t(`alpha9.valueOrigin.${origin.kind}`)))].join(', ') })}</p>}
        {field.plausibility === 'suspicious' && <p className="mt-2 text-sm text-warn">{t('alpha9.sensor.suspicious')}</p>}
        {!!field.valid_until && <p className="mt-1 text-xs text-muted">{t('alpha9.manual.validUntil')}: {formatTime(field.valid_until)}</p>}
        {(field.attribution || sources.some(source => source.entityId)) && <p className="mt-2 text-xs text-muted">{t(field.attribution?.kind === 'known_automatic' ? 'alpha9.context.automatic' : 'alpha9.context.unknown')}</p>}
      </div>)}</div>
      <p className="px-6 pb-4 text-xs text-muted">{t('alpha7.contract.liveVersion', { version: envelope.registry_revision, time: formatTime(envelope.published_at) })}</p>
    </Panel>
    <Panel><PanelHeader title={t('alpha7.contract.why')} /><p className="p-5 text-sm">{why}{affected && <> {t(age ? 'post1.why.inputAge' : 'post1.why.inputAgeUnknown', { name: affectedName, age })}</>}</p></Panel>
    <Panel><PanelHeader title={t('alpha7.contract.where')} /><div className="p-5">
      {sources.length ? <ul className="space-y-3">{sources.map((source, index) => <li key={index}><Link className="font-medium hover:text-fg" to={`/sources/devices?item=${source.sourceIndex}`} title={source.id}>{source.name}</Link>{config && <p className="text-sm text-muted">{freshnessLabel(config, asArray(config.sources).filter(isObject)[source.sourceIndex] ?? {})}</p>}{source.entityId && source.entityId !== source.name && <p className="mt-1 max-w-full truncate text-xs text-muted" title={source.entityId}>{source.entityId}</p>}</li>)}</ul> : <p className="text-sm text-muted">{t('alpha7.contract.noSource')}</p>}
      {ha.isError && <p className="mt-2 text-xs text-muted">{t('alpha7.contract.namesUnavailable')}</p>}
    </div></Panel>
    <Panel><PanelHeader title={t('alpha7.contract.history')} description={t('alpha7.contract.historyScope')} /><div className="space-y-5 p-5">
      <label className="text-sm">{t('beta5.history.window')} <select value={window.hours} onChange={event => window.select(Number(event.target.value) === 168 ? 168 : 24)}><option value={24}>{t('beta5.history.day')}</option><option value={168}>{t('beta5.history.week')}</option></select></label>
      {history.isPending || gaps.isPending ? <Spinner /> : history.isError || gaps.isError ? <p role="alert" className="text-sm text-bad">{t('alpha7.contract.historyUnavailable')}</p> : Object.keys(envelope.fields).map(name => <MiniHistory key={name} name={name} schema={fields[name]} since={window.since} until={window.until} now={window.until} current={stale ? undefined : envelope.fields[name]} points={historyPoints(history.data ?? [], envelope.contract_id, name, envelope.registry_revision, gaps.data ?? [])} />)}
    </div></Panel>
    {sources.filter(source => asArray(config?.sources).map(asObject)[source.sourceIndex]?.origin === 'manual').map(source => <ManualSourcePanel key={source.id} sourceId={source.id} />)}
    {machine && <CommandsPanel contractId={envelope.contract_id} onContract={() => {}} fixed />}
    {machine && <MachineRestartPanel key={envelope.contract_id} contractId={envelope.contract_id} />}
    <details onToggle={event => setAdvanced(event.currentTarget.open)}><summary className="cursor-pointer py-2 text-sm text-muted">{t('alpha7.contract.advanced')}</summary>{advanced && <ContractLiveState envelope={envelope} stale={stale} fields={fields} />}</details>
  </div>;
}
