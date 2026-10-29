import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { uuid } from '@/lib/uuid';
import { Button } from '@/components/ui/button';
import { Field, Select } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { Notice, Spinner } from '@/components/ui/layout';
import type { Catalog, Entry } from './CatalogPage';

export function MasterVariants({ catalog, entry, onOpen }: { catalog: Catalog; entry: Entry; onOpen: (row: Entry) => void }) {
  const [open, setOpen] = useState(false); const [after, setAfter] = useState('');
  const variants = useQuery({ queryKey: ['catalog-details', catalog.catalog_id, entry.identity, after], enabled: open,
    queryFn: () => api<{ variants: Entry[]; next_cursor: string | null }>(`catalogs/care/detail?${new URLSearchParams({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: entry.identity, after })}`) });
  return <details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{t('b17.variants')} · {entry.variant_count}</summary>
    {open && (variants.isPending ? <Spinner /> : variants.isError ? <Notice tone="bad">{describeError(variants.error)}</Notice> : <>
      <ul>{variants.data.variants.map(row => <li key={row.identity}><Button onClick={() => onOpen(row)}>{row.display_name || row.key}</Button></li>)}</ul>
      <Button disabled={!variants.data.next_cursor} onClick={() => setAfter(variants.data.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button>
    </>)}
  </details>;
}

interface CareBody {
  catalog_id: string; definition_version: number; expected_active_revision: number;
  expected_content_revision: number; request_id: string; reason: string; action: string;
  identities: string[]; master?: string; classification?: string;
}
interface Group { members: Entry[]; master: string }
interface Queue { groups: Group[]; content_revision: number; active_revision: number }
interface Impact { affected_keys: number; affected_contracts: number; context_effects: { contract_id?: string; storage?: string; slot?: string }[]; changes: Entry[] }
export function CatalogCare({ catalog, rows, selected, setSelected, revision, activeRevision, parameters }: {
  catalog: Catalog; rows: Entry[]; selected: Record<string, Entry>; setSelected: (value: Record<string, Entry>) => void;
  revision: number; activeRevision: number; parameters: string;
}) {
  const cache = useQueryClient();
  const [body, setBody] = useState<CareBody | null>(null); const [work, setWork] = useState<Queue | null>(null);
  const [members, setMembers] = useState<Entry[]>([]); const [impact, setImpact] = useState<{ value: Impact; body: string } | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const classLabel = (value: string) => catalog.classification_schema.legend?.[value]?.display_name ?? value;
  const begin = (action: string, entries = Object.values(selected), queue: Queue | null = null) => {
    if (!entries.length) return;
    const group = queue?.groups[0]; const master = group?.master ?? entries.find(row => !row.master_key)?.identity ?? entries[0]!.identity;
    setMembers(entries); setError(''); setImpact(null);
    setBody({ catalog_id: catalog.catalog_id, definition_version: catalog.version, expected_active_revision: queue?.active_revision ?? activeRevision,
      expected_content_revision: queue?.content_revision ?? revision, request_id: uuid(), reason: t('b17.careReason'),
      action, identities: entries.map(row => row.identity), master, classification: entries.find(row => row.identity === master)?.classification ?? catalog.classification_schema.values[0] ?? '' });
  };
  const edit = (patch: Partial<CareBody>) => { if (body) { setBody({ ...body, ...patch, request_id: uuid() }); setImpact(null); } };
  const next = (updated: Queue) => {
    setWork(updated);
    if (updated.groups.length) begin('group', updated.groups[0]!.members, updated);
    else { setWork(null); setBody(null); }
  };
  const check = async () => {
    if (!body) return; setBusy(true); setError(''); const submitted = JSON.stringify(body);
    try { setImpact({ value: await api<Impact>('catalogs/care/preview', { method: 'POST', body }), body: submitted }); }
    catch (failure) { setError(describeError(failure)); setImpact(null); } finally { setBusy(false); }
  };
  const save = async () => {
    if (!body || !impact || impact.body !== JSON.stringify(body)) return;
    setBusy(true); setError('');
    try {
      const result = await api<{ content_revision: number }>('catalogs/care/write', { method: 'POST', body });
      setSelected({});
      await cache.invalidateQueries({ queryKey: ['catalog-entries'] });
      await cache.invalidateQueries({ queryKey: ['catalog-history'] });
      if (work) next({ ...work, groups: work.groups.slice(1), content_revision: result.content_revision });
      else setBody(null);
    } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); }
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
    <Dialog open={!!body} onOpenChange={open => { if (!open && !busy) { setBody(null); setWork(null); } }} title={t(work ? 'b17.variantsCheck' : 'b17.bulk')} footer={<>
      {work && <><Button disabled={busy} onClick={() => next({ ...work, groups: work.groups.slice(1) })}>{t('b17.skip')}</Button>
        <Button disabled={busy} onClick={() => edit({ action: 'standalone_review' })}>{t('b17.keepIndependent')}</Button></>}
      <Button disabled={busy || !body || body.identities.length > 100} onClick={() => void check()}>{t('alpha10.catalog.preview')}</Button>
      <Button variant="primary" disabled={busy || !body || !impact || impact.body !== JSON.stringify(body)} onClick={() => void save()}>{t('alpha10.catalog.save')}</Button>
    </>}>
      <div className="space-y-4">
        <p>{t('b17.careDescription')}</p><Notice tone="warn">{t('b17.tcWins')}</Notice>
        {work && <p>{t('b17.groupsRemaining', { n: work.groups.length })}</p>}
        {body && <p>{t(`b17.action.${body.action}`)}</p>}
        <ul>{members.map(row => <li key={row.identity}>{row.display_name || row.key}</li>)}</ul>
        {body?.action === 'group' && <Field label={t('alpha10.catalog.master')}>{p => <Select {...p} disabled={busy} value={body.master ?? ''} onChange={event => edit({ master: event.target.value, classification: members.find(row => row.identity === event.target.value)?.classification ?? catalog.classification_schema.values[0] ?? '' })}>{members.filter(row => !row.master_key).map(row => <option key={row.identity} value={row.identity}>{row.display_name || row.key}</option>)}</Select>}</Field>}
        {body && ['classify', 'group', 'ungroup'].includes(body.action) && <Field label={t('alpha10.catalog.class')}>{p => <Select {...p} disabled={busy} value={body.classification ?? ''} onChange={event => edit({ classification: event.target.value })}>{catalog.classification_schema.values.map(value => <option key={value} value={value}>{classLabel(value)}</option>)}</Select>}</Field>}
        {busy && <Spinner />}{error && <Notice tone="bad">{error}</Notice>}
        {impact && impact.body === JSON.stringify(body) && <>
          <p>{t('alpha10.catalog.impact', { keys: impact.value.affected_keys, contracts: impact.value.affected_contracts })}</p>
          <ul>{impact.value.changes.map((row, index) => <li key={index}>{row.display_name || row.key} · {row.classification ? classLabel(row.classification) : row.master_key}</li>)}</ul>
          <ul>{impact.value.context_effects.map((effect, index) => <li key={index}>{effect.contract_id} · {effect.storage} · {effect.slot}</li>)}</ul>
        </>}
      </div>
    </Dialog>
  </div>;
}

export function CatalogDetail({ catalog, entry, onClose, onOpen }: { catalog: Catalog; entry: Entry | null; onClose: () => void; onOpen: (row: Entry) => void }) {
  const [after, setAfter] = useState('');
  const data = useQuery({ queryKey: ['catalog-details', catalog.catalog_id, entry?.identity, after], enabled: !!entry,
    queryFn: () => api<{ entry: Entry; master: Entry | null; variants: Entry[]; next_cursor: string | null; contexts: (NonNullable<Entry['tc_metadata']>['contexts'][number] & { catalog_id: string | null; effective_classification: string | null })[] }>(`catalogs/care/detail?${new URLSearchParams({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: entry!.identity, after })}`) });
  const value = data.data?.entry; const meta = value?.tc_metadata;
  const open = (row: Entry) => { setAfter(''); onOpen(row); };
  return <Dialog open={!!entry} onOpenChange={shown => { if (!shown) { setAfter(''); onClose(); } }} title={t('b17.details')}>
    <p>{t('b17.detailDescription')}</p>
    {data.isPending ? <Spinner /> : data.isError ? <Notice tone="bad">{describeError(data.error)}</Notice> : value && <div className="space-y-4">
      <h3>{value.display_name || value.key}</h3>
      <p>{t('b17.effectiveClass')}: {catalog.classification_schema.legend?.[value.effective_classification]?.display_name ?? value.effective_classification}</p>
      {data.data.master && <Button onClick={() => open(data.data!.master!)}>{t('alpha10.catalog.master')}: {data.data.master.display_name || data.data.master.key}</Button>}
      <p>{t(`b17.status.${value.review_status ?? 'open'}`)}</p>
      {meta && <dl><dt>{t('b17.artist')}</dt><dd>{meta.artist ?? '—'}</dd><dt>{t('b17.title')}</dt><dd>{meta.title ?? '—'}</dd><dt>{t('b17.sourceClass')}</dt><dd>{meta.source_class}</dd></dl>}
      <p>{t('b17.totalSightings', { n: value.seen_count ?? 0, first: value.first_seen ?? '—', last: value.last_seen ?? '—' })}</p>
      <h3>{t('b17.contexts')}</h3>
      <ul>{data.data.contexts?.map((ctx, index) => <li key={index}><p>{ctx.context} · {ctx.source_app} · {ctx.catalog_id ?? t('alpha10.catalog.unassigned')} · {t('b17.override')}: {ctx.enum_override ?? '—'}</p><p>{t('b17.effectiveClass')}: {ctx.effective_classification ?? t('alpha10.catalog.unassigned')}</p><p>{t('b17.totalSightings', { n: ctx.telemetry?.seen_count ?? 0, first: ctx.telemetry?.first_seen ?? '—', last: ctx.telemetry?.last_seen ?? '—' })}</p></li>)}</ul>
      {value.local_sightings && <p>{t('b17.localSightings', { n: value.local_sightings.seen_count })}</p>}
      <h3>{t('b17.variants')}</h3><ul>{data.data.variants.map(row => <li key={row.identity}><Button onClick={() => open(row)}>{row.display_name || row.key}</Button></li>)}</ul>
      <Button disabled={!data.data.next_cursor} onClick={() => setAfter(data.data?.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button>
    </div>}
  </Dialog>;
}
