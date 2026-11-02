import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { uuid } from '@/lib/uuid';
import { Button } from '@/components/ui/button';
import { Field, Input, Select } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { Notice, Spinner } from '@/components/ui/layout';
import { Badge } from '@/components/ui/badge';
import type { Catalog, Entry } from './CatalogPage';
import { titleLabel } from './titleLabel';
import { catalogLabel, contextLabel } from './catalogLabels';

export function MasterVariants({ catalog, entry, onOpen, renderEntry, parameters = '' }: { catalog: Catalog; entry: Entry; onOpen: (row: Entry) => void; renderEntry?: (row: Entry) => ReactNode; parameters?: string }) {
  const [open, setOpen] = useState(false); const [after, setAfter] = useState('');
  const autoExpand = entry.matches_filter === false && (entry.matching_variant_count ?? 0) > 0;
  useEffect(() => { if (autoExpand) { setOpen(true); setAfter(''); } }, [autoExpand, entry.identity, entry.matching_variant_count]);
  const params = new URLSearchParams(parameters);
  for (const [key, value] of Object.entries({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: entry.identity, after })) params.set(key, value);
  const variants = useQuery({ queryKey: ['catalog-details', catalog.catalog_id, catalog.version, entry.identity, after, parameters], enabled: open,
    queryFn: () => api<{ variants: Entry[]; next_cursor: string | null }>(`catalogs/care/detail?${params}`) });
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="mt-3 border-l-2 border-line pl-4">
    <summary>{t('b17.variants')} · {entry.variant_count}</summary>
    {open && (variants.isPending ? <Spinner /> : variants.isError ? <Notice tone="bad">{describeError(variants.error)}</Notice> : <>
      <ul className="divide-y divide-line" aria-label={t('b17.variants')}>{variants.data.variants.map(row => renderEntry ? renderEntry(row) : <li key={row.identity}><Badge>{t('alpha10.catalog.variant')}</Badge><Button onClick={() => onOpen(row)}>{titleLabel(row)}</Button></li>)}</ul>
      <Button disabled={!variants.data.next_cursor} onClick={() => setAfter(variants.data.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button>
    </>)}
  </details>;
}

interface CareBody {
  catalog_id: string; definition_version: number; expected_active_revision: number;
  expected_content_revision: number; request_id: string; reason: string; action: string;
  identities: string[]; master?: string; new_master_name?: string; classification?: string;
}
interface Group { members: Entry[]; master: string }
interface Queue { groups: Group[]; content_revision: number; active_revision: number }
interface Impact { affected_keys: number; affected_contracts: number; context_effects: { contract_id?: string; storage?: string; slot?: string }[]; changes: Entry[]; timings_ms?: Record<string, number> }
export function CatalogCare({ catalog, rows, selected, setSelected, revision, activeRevision, parameters }: {
  catalog: Catalog; rows: Entry[]; selected: Record<string, Entry>; setSelected: (value: Record<string, Entry>) => void;
  revision: number; activeRevision: number; parameters: string;
}) {
  const cache = useQueryClient();
  const [body, setBody] = useState<CareBody | null>(null); const [work, setWork] = useState<Queue | null>(null);
  const [members, setMembers] = useState<Entry[]>([]); const [impact, setImpact] = useState<{ value: Impact; body: string } | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const sequence = useRef(0); const latest = useRef('');
  const [timings, setTimings] = useState<Record<string, number>>({});
  latest.current = JSON.stringify(body);
  const classLabel = (value: string) => catalog.classification_schema.legend?.[value]?.display_name ?? value;
  const begin = (action: string, entries = Object.values(selected), queue: Queue | null = null) => {
    if (!entries.length) return;
    const group = queue?.groups[0]; const master = group?.master ?? [...entries].filter(row => !row.master_key).sort((a, b) => (a.master_order ?? Number.MAX_SAFE_INTEGER) - (b.master_order ?? Number.MAX_SAFE_INTEGER))[0]?.identity ?? entries[0]!.identity;
    setMembers(entries); setError(''); setImpact(null);
    setBody({ catalog_id: catalog.catalog_id, definition_version: catalog.version, expected_active_revision: queue?.active_revision ?? activeRevision,
      expected_content_revision: queue?.content_revision ?? revision, request_id: uuid(), reason: t('b17.careReason'),
      action, identities: entries.map(row => row.identity), master, classification: entries.find(row => row.identity === master)?.classification ?? catalog.classification_schema.values[0] ?? '' });
  };
  const edit = (patch: Partial<CareBody>) => { if (body) { const next = { ...body, ...patch, request_id: uuid() }; if (next.action !== 'group') delete next.new_master_name; setBody(next); setImpact(null); } };
  const groupReady = !body || body.action !== 'group' || (body.new_master_name !== undefined ? !!body.new_master_name.trim() : !!body.master);
  const next = (updated: Queue) => {
    setWork(updated);
    if (updated.groups.length) begin('group', updated.groups[0]!.members, updated);
    else { setWork(null); setBody(null); }
  };
  const check = async () => {
    if (!body || !groupReady) return; const seq = ++sequence.current; setBusy(true); setError(''); const submitted = JSON.stringify(body); const started = performance.now();
    try { const value = await api<Impact>('catalogs/care/preview', { method: 'POST', body }); if (seq === sequence.current && latest.current === submitted) { setImpact({ value, body: submitted }); setTimings(previous => ({ ...previous, ...(value.timings_ms ?? {}), preview: performance.now() - started })); } }
    catch (failure) { if (seq === sequence.current && latest.current === submitted) { setError(describeError(failure)); setImpact(null); } } finally { if (seq === sequence.current) setBusy(false); }
  };
  const payloadKey = JSON.stringify(body);
  useEffect(() => { if (!body || !groupReady) { if (!saving) setBusy(false); return; } setImpact(null); const timer = window.setTimeout(() => void check(), 200); return () => { window.clearTimeout(timer); sequence.current++; }; }, [payloadKey, revision, activeRevision]);
  const save = async () => {
    if (!body || !groupReady || !impact || impact.body !== JSON.stringify(body) || busy || error) return;
    setBusy(true); setSaving(true); setError('');
    try {
      const started = performance.now();
      const result = await api<{ content_revision: number }>('catalogs/care/write', { method: 'POST', body });
      const write = performance.now() - started; const refreshing = performance.now();
      setSelected({});
      await cache.invalidateQueries({ queryKey: ['catalog-entries'] });
      await cache.invalidateQueries({ queryKey: ['catalog-history'] });
      await cache.invalidateQueries({ queryKey: ['catalog-details'] });
      await cache.invalidateQueries({ queryKey: ['catalog-masters'] });
      setTimings(previous => ({ ...previous, write, refresh: performance.now() - refreshing }));
      if (work) next({ ...work, groups: work.groups.slice(1), content_revision: result.content_revision });
      else setBody(null);
    } catch (failure) { setError(describeError(failure)); setImpact(null); } finally { setBusy(false); setSaving(false); }
  };
  const startQueue = async () => {
    setBusy(true); setError('');
    try {
      const result = await api<Queue>(`catalogs/care/queue?${parameters}`);
      if (!result.groups.length) { setError(t('b17.noCandidates')); return; }
      setWork(result); begin('group', result.groups[0]!.members, result);
    } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); }
  };
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      <Button onClick={() => { const next = { ...selected }; const all = rows.every(row => !!selected[row.identity]); for (const row of rows) { if (all) delete next[row.identity]; else next[row.identity] = row; } setSelected(next); }}>{t('b17.selectVisible')}</Button>
      <Button disabled={!Object.keys(selected).length} onClick={() => setSelected({})}>{t('b17.clearSelection')}</Button>
      <p>{t('b17.selected', { n: Object.keys(selected).length })}</p>
    </div>
    <div className="flex flex-wrap gap-2">{['classify', 'group', 'review', 'open', 'hide', 'unhide', 'ungroup'].map(action => <Button key={action} disabled={busy || !Object.keys(selected).length || Object.keys(selected).length > 100} onClick={() => { setWork(null); begin(action); }}>{t(`b17.action.${action}`)}</Button>)}
      <Button disabled={busy} onClick={() => void startQueue()}>{t('b17.variantsCheck')}</Button>
    </div>
    {Object.keys(selected).length > 100 && <Notice tone="warn">{t('b17.selectionLimit')}</Notice>}
    {error && !body && <Notice tone="bad">{error}</Notice>}
    {!!Object.keys(timings).length && <details><summary>{t('b20.timing.title')}</summary><dl>{Object.entries(timings).map(([name, ms]) => <div key={name}><dt>{t(`b20.timing.${name}`)}</dt><dd>{t('b20.timing.value', { ms: Math.round(ms) })}</dd></div>)}</dl></details>}
    <Dialog open={!!body} onOpenChange={open => { if (!open && !busy) { setBody(null); setWork(null); } }} title={t(work ? 'b17.variantsCheck' : 'b17.bulk')} footer={<>
      {work && <><Button disabled={busy} onClick={() => next({ ...work, groups: work.groups.slice(1) })}>{t('b17.skip')}</Button>
        <Button disabled={busy} onClick={() => edit({ action: 'standalone_review' })}>{t('b17.keepIndependent')}</Button></>}
      <Button disabled={busy || !body || !groupReady || body.identities.length > 100} onClick={() => void check()}>{t('alpha10.catalog.preview')}</Button>
      <Button variant="primary" disabled={busy || !!error || !groupReady || !body || !impact || impact.body !== JSON.stringify(body)} onClick={() => void save()}>{t('alpha10.catalog.save')}</Button>
    </>}>
      <div className="space-y-4">
        <p>{t('b17.careDescription')}</p><Notice tone="warn">{t('b17.tcWins')}</Notice>
        {work && <p>{t('b17.groupsRemaining', { n: work.groups.length })}</p>}
        {body && <p>{t(`b17.action.${body.action}`)}</p>}
        <ul>{members.map(row => <li key={row.identity}>{titleLabel(row)}</li>)}</ul>
        {body?.action === 'group' && <><Field label={t('alpha10.catalog.master')}>{p => <Select {...p} disabled={busy} value={body.new_master_name !== undefined ? '__new_master__' : body.master ?? ''} onChange={event => edit(event.target.value === '__new_master__' ? { master: undefined, new_master_name: '' } : { master: event.target.value, new_master_name: undefined, classification: members.find(row => row.identity === event.target.value)?.classification ?? catalog.classification_schema.values[0] ?? '' })}>{members.filter(row => !row.master_key).map(row => <option key={row.identity} value={row.identity}>{titleLabel(row)}</option>)}<option value="__new_master__">{t('b22.catalog.newMaster')}</option></Select>}</Field>{body.new_master_name !== undefined && <><Field label={t('b22.catalog.masterName')}>{p => <Input {...p} disabled={saving} maxLength={1024} value={body.new_master_name} onChange={event => edit({ new_master_name: event.target.value })} />}</Field><p className="text-sm text-muted">{t('b22.catalog.newMasterHelp')}</p></>}</>}
        {body && ['classify', 'group', 'ungroup'].includes(body.action) && <Field label={t(body.action === 'group' ? 'b22.catalog.masterClass' : 'alpha10.catalog.class')}>{p => <Select {...p} disabled={busy} value={body.classification ?? ''} onChange={event => edit({ classification: event.target.value })}>{catalog.classification_schema.values.map(value => <option key={value} value={value}>{classLabel(value)}</option>)}</Select>}</Field>}
        {busy && <Spinner />}{error && <Notice tone="bad">{error}</Notice>}
        {impact && impact.body === JSON.stringify(body) && <>
          <p>{t('alpha10.catalog.impact', { keys: impact.value.affected_keys, contracts: impact.value.affected_contracts })}</p>
          <ul>{impact.value.changes.map((row, index) => <li key={index}>{titleLabel(row)} · {row.classification ? classLabel(row.classification) : row.master_key ? titleLabel({ key: row.master_key }) : ''}</li>)}</ul>
          <ul>{impact.value.context_effects.map((effect, index) => <li key={index}>{effect.contract_id} · {effect.storage} · {effect.slot}</li>)}</ul>
        </>}
      </div>
    </Dialog>
  </div>;
}

export function CatalogDetail({ catalog, entry, onClose, onOpen }: { catalog: Catalog; entry: Entry | null; onClose: () => void; onOpen: (row: Entry) => void }) {
  const [after, setAfter] = useState('');
  const data = useQuery({ queryKey: ['catalog-details', catalog.catalog_id, catalog.version, entry?.identity, after], enabled: !!entry,
    queryFn: () => api<{ entry: Entry; master: Entry | null; variants: Entry[]; next_cursor: string | null; contexts: (NonNullable<Entry['tc_metadata']>['contexts'][number] & { catalog_id: string | null; effective_classification: string | null })[] }>(`catalogs/care/detail?${new URLSearchParams({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: entry!.identity, after })}`) });
  const value = data.data?.entry; const meta = value?.tc_metadata;
  const open = (row: Entry) => { setAfter(''); onOpen(row); };
  return <Dialog open={!!entry} onOpenChange={shown => { if (!shown) { setAfter(''); onClose(); } }} title={t('b17.details')}>
    <p>{t('b17.detailDescription')}</p>
    {data.isPending ? <Spinner /> : data.isError ? <Notice tone="bad">{describeError(data.error)}</Notice> : value && <div className="space-y-4">
      <h3>{titleLabel(value)}</h3>
      <details><summary>{t('alpha10.technical')}</summary><p>{catalog.catalog_id} · {catalog.version}</p><p>{value.signal_type === 'app' ? value.key : value.identity} · {value.scope} · {value.media_type} · {value.signal_type}</p></details>
      <p>{t('b17.effectiveClass')}: {catalog.classification_schema.legend?.[value.effective_classification]?.display_name ?? value.effective_classification}</p>
      {data.data.master && <Button onClick={() => open(data.data!.master!)}>{t('alpha10.catalog.master')}: {titleLabel(data.data.master)}</Button>}
      <p>{t(`b17.status.${value.review_status ?? 'open'}`)}</p>
      {meta && <dl><dt>{t('b17.artist')}</dt><dd>{meta.artist ?? '—'}</dd><dt>{t('b17.title')}</dt><dd>{meta.title ?? '—'}</dd><dt>{t('b17.sourceClass')}</dt><dd>{meta.source_class}</dd></dl>}
      <p>{t('b17.totalSightings', { n: value.seen_count ?? 0, first: value.first_seen ?? '—', last: value.last_seen ?? '—' })}</p>
      <h3>{t('b17.contexts')}</h3>
      <ul>{data.data.contexts?.map((ctx, index) => <li key={index}><p>{contextLabel(ctx.context)} · {ctx.source_app ? contextLabel(ctx.source_app) : ''} · {ctx.catalog_id ? catalogLabel({ catalog_id: ctx.catalog_id, version: 1 }) : t('alpha10.catalog.unassigned')} · {t('b17.override')}: {ctx.enum_override ?? '—'}</p><p>{t('b17.effectiveClass')}: {ctx.effective_classification ?? t('alpha10.catalog.unassigned')}</p><p>{t('b17.totalSightings', { n: ctx.telemetry?.seen_count ?? 0, first: ctx.telemetry?.first_seen ?? '—', last: ctx.telemetry?.last_seen ?? '—' })}</p></li>)}</ul>
      {value.local_sightings && <p>{t('b17.localSightings', { n: value.local_sightings.seen_count })}</p>}
      <h3>{t('b17.variants')}</h3><ul>{data.data.variants.map(row => <li key={row.identity}><Button onClick={() => open(row)}>{titleLabel(row)}</Button></li>)}</ul>
      <Button disabled={!data.data.next_cursor} onClick={() => setAfter(data.data?.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button>
    </div>}
  </Dialog>;
}
