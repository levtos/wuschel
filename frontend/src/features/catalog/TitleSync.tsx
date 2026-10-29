import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { codeLabel } from '@/i18n/codes';
import { api, describeError } from '@/lib/api';
import { uuid } from '@/lib/uuid';
import { useActiveRevision } from '@/lib/queries';
import { Button } from '@/components/ui/button';
import { Field, Input, Select } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';

interface Target { catalog_id: string; definition_version: number; scope: string; profile: string }
interface Mapping { mappings: Record<string, Target>; standalone: string[] }
interface Finding { row: number; key?: string; standalone_key?: string; reasons: string[] }
interface Preview {
  plan_id: string; plan_digest: string; at: string; counts: Record<string, number>;
  can_apply: boolean; mapping: Mapping; mapping_version: number; page: number; page_count: number; page_source_rows: number;
  contexts: { key: string; context: string; source_app: string }[];
  error_groups: { context: string; reason: string; count: number; details: Finding[] }[];
}
interface Row { catalog_id: string; old_class: string; new_class: string; master?: string; change: string;
  after: { key: string; display_name: string; reviewed_at?: string; hidden_at?: string;
    tc_metadata: { artist?: string; title?: string; telemetry?: { seen_count: number; first_seen?: string; last_seen?: string } } } }
interface Page extends Preview { items: Row[] }
export interface SyncCatalog { catalog_id: string; version: number }
const date = (value?: string) => value ? new Date(value).toLocaleString('de-DE', { timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : t('b18.empty');

export function TitleSync({ catalogs }: { catalogs: SyncCatalog[] }) {
  const active = useActiveRevision(); const cache = useQueryClient();
  const [open, setOpen] = useState(false); const [preview, setPreview] = useState<Preview | null>(null);
  const [items, setItems] = useState<Row[]>([]); const [mapping, setMapping] = useState<Mapping | null>(null);
  const [dirty, setDirty] = useState(false); const [busy, setBusy] = useState(false); const [complete, setComplete] = useState(false);
  const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const [device, setDevice] = useState(''); const [change, setChange] = useState('');
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const requestId = useRef(uuid()); const sequence = useRef(0); const runId = useRef('');
  const readController = useRef<AbortController | null>(null);
  const expected = useRef(0);
  const release = (id: string) => api('title-sync/cancel', { method: 'POST', body: { plan_id: id } });
  useEffect(() => () => { ++sequence.current; readController.current?.abort(); if (runId.current) void release(runId.current).catch(() => {}); }, []);
  const loadPage = async (id: string, signal?: AbortSignal) => api<Page>(`title-sync/page?${new URLSearchParams({ plan_id: id })}`, { signal });
  const read = async (edit = false) => {
    const serial = ++sequence.current; setBusy(true); setError(''); setMessage(''); setOpen(true);
    const controller = new AbortController(); readController.current = controller;
    expected.current = active.data?.revision ?? 0;
    try {
      if (!edit && runId.current) await release(runId.current);
      if (serial !== sequence.current) return;
      const id = edit ? preview!.plan_id : uuid(); runId.current = id;
      const result = await api<Preview>('title-sync/preview', { method: 'POST', signal: controller.signal, body: {
        expected_active_revision: expected.current,
        ...(edit && mapping ? { plan_id: id, mapping, expected_mapping_version: preview!.mapping_version } : { run_id: id }),
      } });
      if (serial !== sequence.current) { void release(result.plan_id).catch(() => {}); return; }
      runId.current = result.plan_id;
      const page = await loadPage(result.plan_id, controller.signal);
      if (serial !== sequence.current) return;
      setPreview(page); setItems(page.items); setMapping(page.mapping); setDirty(false); setComplete(false);
      requestId.current = uuid(); setDevice(''); setChange('');
    } catch (failure) { if (serial === sequence.current) { setError(describeError(failure)); if (!edit) setPreview(null); void cache.invalidateQueries({ queryKey: ['registry', 'active'] }); } }
    finally { if (serial === sequence.current) setBusy(false); }
  };
  const apply = async () => {
    if (!preview || dirty || !preview.can_apply || complete || expected.current !== active.data?.revision) return;
    const serial = sequence.current; setBusy(true); setError(''); setMessage('');
    try {
      const result = await api<{ changed: number; complete: boolean; page: number }>('title-sync/apply', { method: 'POST', body: {
        plan_id: preview.plan_id, plan_digest: preview.plan_digest, request_id: requestId.current,
      } });
      if (serial !== sequence.current) return;
      setMessage(t(result.complete ? 'b18.finished' : 'b18.pageSaved', { page: result.page }));
      if (result.complete) { setComplete(true); setItems([]); setPreview({ ...preview, can_apply: false }); }
      else {
        const next = await loadPage(preview.plan_id);
        if (serial !== sequence.current) return;
        setPreview(next); setMapping(next.mapping); setItems(next.items); requestId.current = uuid();
      }
      await cache.invalidateQueries({ queryKey: ['catalog-entries'] });
      await cache.invalidateQueries({ queryKey: ['catalog-history'] });
    } catch (failure) { if (serial === sequence.current) { setError(describeError(failure)); void cache.invalidateQueries({ queryKey: ['registry', 'active'] }); } }
    finally { if (serial === sequence.current) setBusy(false); }
  };
  const abort = async () => {
    ++sequence.current; readController.current?.abort(); setError(''); const id = runId.current; runId.current = '';
    setBusy(false); setOpen(false); setPreview(null); setItems([]);
    if (id) { try { await release(id); } catch (failure) { setOpen(true); setError(describeError(failure)); } }
  };
  const update = (key: string, patch: Partial<Target>) => {
    const value = mapping ?? { mappings: {}, standalone: [] };
    setMapping({ ...value, mappings: { ...value.mappings, [key]: { catalog_id: '', definition_version: 1, scope: 'default', profile: 'music', ...value.mappings[key], ...patch } } }); setDirty(true);
  };
  const locked = busy || complete || (preview?.page ?? 1) > 1;
  const filtered = items.filter(row => (!device || row.catalog_id === device) && (!change || row.change === change));
  const contextName = (key: string) => { const row = preview?.contexts.find(row => row.key === key); return row ? [row.context || t('b15.withoutContext'), row.source_app].filter(Boolean).join(' · ') : t('b15.withoutContext'); };
  const panel = open && <section aria-label={t('b17.sync')} className="min-w-0 space-y-4 rounded-lg border border-line bg-panel p-5">
    <h2>{t('b17.sync')}</h2><p>{t('b17.syncDescription')}</p>
    {preview && <>
      <div className="flex flex-wrap gap-5"><dl className="flex flex-wrap gap-5">{['new', 'changed', 'unchanged', 'unmappable'].map(key => <div key={key}><dt>{t(`b17.diff.${key}`)}</dt><dd>{preview.counts[key] ?? 0}</dd></div>)}</dl><p>{t('b18.page', { page: preview.page, total: preview.page_count })}</p></div>
      <p>{t('b17.sourceAt', { at: date(preview.at) })}</p>
      <details open={dirty || !!preview.error_groups.length}><summary>{t('b17.mapping')}</summary><p>{t('b18.mappingHint')}</p>
        {preview.contexts.map(context => { const target = mapping?.mappings[context.key]; return <fieldset key={context.key} className="my-2 grid items-end gap-3 border border-line p-3 lg:grid-cols-4">
          <legend>{contextName(context.key)}</legend>
          <Field label={t('b15.device')}>{p => <Select {...p} disabled={locked} value={target ? `${target.catalog_id}@${target.definition_version}` : ''} onChange={e => { const chosen = catalogs.find(c => `${c.catalog_id}@${c.version}` === e.target.value); update(context.key, { catalog_id: chosen?.catalog_id ?? '', definition_version: chosen?.version ?? 1 }); }}>
            <option value="">{t('b4.choose')}</option>{catalogs.map(c => <option key={`${c.catalog_id}@${c.version}`} value={`${c.catalog_id}@${c.version}`}>{c.catalog_id} · {c.version}</option>)}</Select>}</Field>
          <Field label={t('b15.profile')}>{p => <Select {...p} disabled={locked} value={target?.profile ?? 'music'} onChange={e => update(context.key, { profile: e.target.value })}>{[['music', 'b15.music'], ['game', 'b15.games'], ['private', 'b15.private']].map(([value, label]) => <option key={value} value={value}>{t(label!)}</option>)}</Select>}</Field>
          <Field label={t('alpha10.catalog.field.scope')}>{p => <Input {...p} disabled={locked} value={target?.scope ?? 'default'} onChange={e => update(context.key, { scope: e.target.value })} />}</Field>
        </fieldset>; })}
        <Button disabled={locked || !dirty} onClick={() => void read(true)}>{t('b17.checkMapping')}</Button>
      </details>
      {!!preview.error_groups.length && <section aria-label={t('b18.findings')}>{preview.error_groups.map((group, index) => <details key={index} onToggle={event => { const shown = event.currentTarget.open; setExpanded(previous => ({ ...previous, [index]: shown })); }}><summary>{contextName(group.context)} · {codeLabel(group.reason)} · {t('b18.findingCount', { n: group.count })}</summary>
        {expanded[index] && <ul>{group.details.map((finding, i) => <li key={i}>{finding.key || t('b18.sourceRow', { n: finding.row })}
          {finding.standalone_key && group.reason === 'title_sync_variant_override' && <Button disabled={locked} onClick={() => { if (mapping) { setMapping({ ...mapping, standalone: [...mapping.standalone, finding.standalone_key!] }); setDirty(true); } }}>{t('b17.standalone')}</Button>}</li>)}</ul>}
      </details>)}</section>}
      <Notice tone="warn">{t('b18.tcWins')}</Notice>
      <div className="flex flex-wrap gap-4">
        <Field label={t('b18.changeFilter')}>{p => <Select {...p} value={change} onChange={e => setChange(e.target.value)}><option value="">{t('alpha10.catalog.all')}</option>{['new', 'changed', 'unchanged'].map(key => <option key={key} value={key}>{t(`b17.diff.${key}`)}</option>)}</Select>}</Field>
        <Field label={t('b18.column.device')}>{p => <Select {...p} value={device} onChange={e => setDevice(e.target.value)}><option value="">{t('b18.allDevices')}</option>{[...new Set(catalogs.map(row => row.catalog_id))].map(id => <option key={id} value={id}>{id}</option>)}</Select>}</Field>
      </div><p>{t('b18.filterHint')}</p>
      <div className="overflow-x-auto"><table className="w-full table-fixed text-left text-sm" style={{ minWidth: '100rem' }}>
        <thead><tr>{['device', 'artist', 'title', 'class', 'master', 'status', 'sightings', 'first', 'last', 'change'].map(key => <th key={key} scope="col" className="p-2">{t(`b18.column.${key}`)}</th>)}</tr></thead>
        <tbody>{filtered.map((item, index) => { const row = item.after; const meta = row.tc_metadata; return <tr key={index} className="border-t border-line align-top">
          <td className="break-words p-2">{item.catalog_id}</td><td className="break-words p-2">{meta.artist || t('b18.empty')}</td><td className="break-words p-2">{meta.title || row.display_name || t('b18.empty')}</td>
          <td className="p-2">{item.old_class || t('b18.empty')} → {item.new_class || t('b18.empty')}</td><td className="break-words p-2">{item.master || t('b18.empty')}</td><td className="p-2">{row.hidden_at ? t('b17.status.hidden') : row.reviewed_at ? t('b17.status.reviewed') : t('b18.empty')}</td>
          <td className="p-2">{meta.telemetry ? t('b18.sightings', { n: meta.telemetry.seen_count }) : t('b18.empty')}</td><td className="p-2">{date(meta.telemetry?.first_seen)}</td><td className="p-2">{date(meta.telemetry?.last_seen)}</td><td className="p-2">{t(`b17.diff.${item.change}`)}</td>
        </tr>; })}</tbody>
      </table></div>{!filtered.length && !complete && <p>{t('b18.noRows')}</p>}
    </>}
    <div className="sticky bottom-0 z-10 space-y-3 border-t border-line bg-panel p-3">
      <div className="flex flex-wrap gap-3">
        <Button variant="primary" disabled={busy || dirty || !preview?.can_apply || !items.length || complete || expected.current !== active.data?.revision} onClick={() => void apply()}>{t('b18.applyPage')}</Button>
        <Button disabled={busy} onClick={() => void read()}>{t('b17.syncAgain')}</Button>
        <Button onClick={() => void abort()}>{t('b18.abort')}</Button>
      </div><div aria-live="polite">{busy && <p role="status">{t('b18.busy')}</p>}{error && <Notice tone="bad">{error}</Notice>}{message && <Notice tone="ok">{message}</Notice>}</div>
    </div>
  </section>;
  const host = document.getElementById('title-sync-panel');
  return <><Button variant="primary" disabled={busy || !catalogs.length || active.data === undefined} onClick={() => void read()}>{t('b17.sync')}</Button>{host && panel ? createPortal(panel, host) : panel}</>;
}
