import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/controls';
import { Field, Input } from '@/components/ui/form';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { TypedValueEditor } from '@/editors/common';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import { ApiError, api, describeError } from '@/lib/api';
import { useActiveRevision } from '@/lib/queries';
import { isObject, type Json, type JsonObject } from '@/lib/json';
import { formatTime } from '@/lib/format';
import { displayValue } from '@/lib/status';
import { ForecastValuesEditor, newForecastPoint, OriginIntervalFields } from '@/editors/ValueOriginEditor';
import { effectiveRule } from './freshness';

export function ManualSourcePanel({ sourceId }: { sourceId: string }) {
  const active = useActiveRevision(); const client = useQueryClient(); const config = active.data?.config;
  const source = asArray(config?.sources).filter(isObject).find(item => item.source_id === sourceId && item.origin === 'manual');
  const schema = asObject(source?.value_schema) as FieldSchema;
  const declaredOrigin = asObject(source?.value_origin);
  const wakeException = source?.manual_structured_schema === 'wake_exception_v1';
  const [originMetadata, setOriginMetadata] = useState<JsonObject>({});
  const current = useQuery({ queryKey: ['evidence', sourceId], queryFn: () => api<JsonObject[]>(`evidence/${encodeURIComponent(sourceId)}`), enabled: !!source, retry: false });
  const entry = asObject(current.data?.[0]?.manual);
  const rule = effectiveRule(config ?? {}, source ?? {});
  const start = Date.parse(str(entry.valid_from)); const duration = Number(rule.interval_s);
  const end = Math.min(entry.valid_until ? Date.parse(str(entry.valid_until)) : Infinity, rule.mode === 'periodic_ttl' && Number.isFinite(start) ? start + duration*1000 : Infinity);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const next = Math.min(...[start, end].filter(at => Number.isFinite(at) && at > now)); if (!Number.isFinite(next)) return; const timer = window.setTimeout(() => setNow(Date.now()), Math.min(2147483647, next-Date.now()+10)); return () => window.clearTimeout(timer); }, [start, end, now]);
  const [value, setValue] = useState<Json>(schema.type === 'number' ? 0 : schema.type === 'bool' ? false : schema.type === 'object' ? {} : '');
  const [from, setFrom] = useState(''); const [until, setUntil] = useState(''); const [confirm, setConfirm] = useState(false); const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<JsonObject | null>(null); const [result, setResult] = useState('');
  const [retryAvailable, setRetryAvailable] = useState(false);
  useEffect(() => { setValue(schema.type === 'number' ? 0 : schema.type === 'bool' ? false : schema.type === 'object' ? {} : schema.type === 'enum' ? String(asArray(schema.value_catalog)[0] ?? '') : schema.type === 'timestamp' ? new Date().toISOString() : ''); }, [schema.type]);
  const originSignature = JSON.stringify(declaredOrigin);
  useEffect(() => { const origin = JSON.parse(originSignature) as JsonObject; setOriginMetadata(origin); if (origin.kind === 'forecast') setValue([newForecastPoint(schema, origin)]); }, [originSignature]);
  const contracts = asArray(config?.contracts).filter(isObject).filter(contract => contract.enabled !== false);
  const reached = new Set([sourceId]);
  for (let pass = 0; pass < contracts.length; pass++) {
    const before = reached.size;
    for (const contract of contracts) if (asArray(contract.inputs).map(asObject).some(input => [...reached].some(ref => input.ref === ref || str(input.ref).startsWith(ref + '.')))) reached.add(str(contract.contract_id));
    if (reached.size === before) break;
  }
  const affected = contracts.filter(contract => reached.has(str(contract.contract_id)));
  if (!source) return null;
  const unbounded = rule.mode === 'event_stateful' && !until && !wakeException;
  const settled = (response: JsonObject) => { setResult(t(response.status === 'accepted' ? 'alpha9.manual.recorded' : 'alpha9.manual.failed')); setPending(null); setRetryAvailable(false); void client.invalidateQueries({ queryKey: ['evidence', sourceId] }); };
  const submit = async (body: JsonObject) => {
    setRetryAvailable(false);
    try { settled(await api<JsonObject>(`manual-sources/${encodeURIComponent(sourceId)}/observations`, { method: 'POST', body })); }
    catch (error) {
      // A rejected request has no receipt; a lost response may already have committed.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) { setPending(null); setResult(describeError(error)); }
      else setResult(t('alpha9.manual.unknownResult'));
    } finally { setBusy(false); }
  };
  const send = async (action: 'set' | 'retract') => {
    if (busy || pending) return; setBusy(true); setResult('');
    const body: JsonObject = { request_id: crypto.randomUUID(), expected_active_revision: active.data!.revision, action, value: action === 'set' ? value : null, confirm_unbounded: confirm,
      ...(action === 'set' && originMetadata.kind && !Array.isArray(value) ? { value_origin: originMetadata } : {}),
      ...(from && action === 'set' ? { valid_from: new Date(from).toISOString() } : {}), ...(until && action === 'set' ? { valid_until: new Date(until).toISOString() } : {}) };
    setPending(body);
    await submit(body);
  };
  const check = async () => {
    if (!pending || busy) return; setBusy(true);
    try { settled(await api<JsonObject>(`manual-sources/${encodeURIComponent(sourceId)}/requests/${pending.request_id}`)); }
    catch (error) { const missing = error instanceof ApiError && error.status === 404; setRetryAvailable(missing); setResult(t(missing ? 'alpha9.manual.notRecorded' : 'alpha9.manual.unknownResult')); }
    finally { setBusy(false); }
  };
  return <Panel><PanelHeader title={str(source.display_name) || t('alpha9.manual.origin')} description={t('alpha9.manual.description')} /><div className="space-y-4 p-5">
    <p className="flex gap-2 text-xs text-muted">{t('alpha9.manual.origin')}{!!entry.recorded_at && <time>{formatTime(str(entry.recorded_at))}</time>}</p>
    {!!entry.request_id && entry.revoked !== true && <p>{t('alpha9.manual.value')}: {asArray(current.data?.[0]?.forecast).length ? t('alpha9.valueOrigin.series', { count: asArray(current.data?.[0]?.forecast).length }) : displayValue({ status: 'valid', quality: 'healthy', value: current.data?.[0]?.value_raw ?? null, evidence: [], reasons: [] }, typeof schema.unit === 'string' ? schema.unit : undefined)}</p>}
    {start > now && <p>{t('alpha9.manual.notYet', { time: formatTime(str(entry.valid_from)) })}</p>}
    {entry.revoked === true || end <= now ? <p>{t('alpha9.manual.expired')}</p> : null}
    <p className="text-sm text-muted">{affected.length ? t('alpha9.manual.affected', { names: affected.map(item => str(item.display_name) || str(item.contract_id)).join(', ') }) : t('alpha9.manual.inactive')}</p>
    <fieldset disabled={busy || !!pending || !affected.length} className="space-y-3">
      {wakeException ? <WakeExceptionFields value={asObject(value)} onChange={setValue} /> : declaredOrigin.kind === 'forecast' ? <ForecastValuesEditor value={value} schema={schema} origin={declaredOrigin} onChange={setValue} /> : <><TypedValueEditor label={t('alpha9.manual.value')} pointer="/manual/value" schema={schema} value={value} onChange={setValue} />{!!declaredOrigin.kind && <OriginIntervalFields value={originMetadata} onChange={setOriginMetadata} />}</>}
      {typeof schema.unit === 'string' && schema.unit && <p className="text-xs text-muted">{t('alpha9.manual.unit')}: {schema.unit}</p>}
      {source.manual_allow_future === true && <Field label={t('alpha9.manual.validFrom')}>{props => <Input {...props} type="datetime-local" value={from} onChange={event => setFrom(event.target.value)} />}</Field>}
      {!wakeException && <Field label={t('alpha9.manual.validUntil')}>{props => <Input {...props} type="datetime-local" value={until} onChange={event => setUntil(event.target.value)} />}</Field>}
      {unbounded && <Checkbox checked={confirm} onCheckedChange={setConfirm} label={t('alpha9.manual.unboundedConfirm')} />}
      <div className="flex gap-3"><Button disabled={unbounded && !confirm} onClick={() => void send('set')}>{t('alpha9.manual.enter')}</Button><Button disabled={!entry.request_id} onClick={() => void send('retract')}>{t('alpha9.manual.retract')}</Button></div>
    </fieldset>
    {pending && <Button disabled={busy} onClick={() => void check()}>{t('alpha9.manual.checkResult')}</Button>}
    {pending && retryAvailable && <Button disabled={busy} onClick={() => { setBusy(true); void submit(pending); }}>{t('alpha9.manual.retrySame')}</Button>}
    {result && <p role="status">{result}</p>}
  </div></Panel>;
}

export function WakeExceptionFields({ value, onChange }: { value: JsonObject; onChange: (value: Json) => void }) {
  return <div className="space-y-3">
    <Field label={t('sleep.exception.date')}>{props => <Input {...props} type="date" value={str(value.date)} onChange={event => onChange({ ...value, date: event.target.value })} />}</Field>
    <Checkbox checked={value.skip === true} label={t('sleep.exception.skip')} onCheckedChange={skip => onChange(skip ? { date: value.date ?? '', skip: true } : { date: value.date ?? '', wake_time: '' })} />
    {value.skip !== true && <Field label={t('sleep.exception.time')}>{props => <Input {...props} type="time" value={str(value.wake_time)} onChange={event => onChange({ date: value.date ?? '', wake_time: event.target.value })} />}</Field>}
    <p className="text-sm text-muted">{t('sleep.exception.hint')}</p>
  </div>;
}
