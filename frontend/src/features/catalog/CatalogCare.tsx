import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { api, ApiError, describeError } from '@/lib/api';
import { formatTime } from '@/lib/format';
import { uuid } from '@/lib/uuid';
import { Button } from '@/components/ui/button';
import { Field, Input, Select } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { EffectStrip } from '@/features/authoring/DraftEditor';
import { useEditorGuard } from '@/features/authoring/EditorGuard';
import { Notice, Spinner } from '@/components/ui/layout';
import { Badge } from '@/components/ui/badge';
import type { Catalog, Entry } from './CatalogPage';
import { titleLabel } from './titleLabel';
import { catalogLabel, contextLabel } from './catalogLabels';
import { entryKey, isGameCatalog } from './CatalogTable';
import { CatalogMediaFacts } from './CatalogMediaFacts';
import { ClassField, ClassFields, classLabel as typedClassLabel, type ClassValue } from './ClassFields';

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
  class_key?: string; class_value?: ClassValue; class_values?: Record<string, ClassValue>;
  confirm_class_change?: boolean;
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
  const [errorCode, setErrorCode] = useState('');
  const [saving, setSaving] = useState(false);
  const [changed, setChanged] = useState(false); const [saved, setSaved] = useState(false);
  const guard = useEditorGuard(body !== null && changed, () => { setBody(null); setWork(null); setChanged(false); }, saving);
  const sequence = useRef(0); const latest = useRef('');
  const [timings, setTimings] = useState<Record<string, number>>({});
  latest.current = JSON.stringify(body);
  const dimensions = catalog.class_definitions ?? [];
  const named = dimensions.length > 0;
  const selectedClass = dimensions.find(item => item.class_key === body?.class_key);
  const classLabel = (value: string) => catalog.classification_schema.legend?.[value]?.display_name ?? value;
  const failed = (failure: unknown) => {
    setError(describeError(failure));
    setErrorCode(failure instanceof ApiError ? failure.code : '');
    setImpact(null);
  };
  const begin = (action: string, entries = Object.values(selected), queue: Queue | null = null) => {
    if (!entries.length) return;
    const group = queue?.groups[0]; const master = group?.master ?? [...entries].filter(row => !row.master_key).sort((a, b) => (a.master_order ?? Number.MAX_SAFE_INTEGER) - (b.master_order ?? Number.MAX_SAFE_INTEGER))[0]?.identity ?? entries[0]!.identity;
    setMembers(entries); setError(''); setErrorCode(''); setImpact(null); setChanged(false);
    setBody({ catalog_id: catalog.catalog_id, definition_version: catalog.version, expected_active_revision: queue?.active_revision ?? activeRevision,
      expected_content_revision: queue?.content_revision ?? revision, request_id: uuid(), reason: t('b17.careReason'),
      action, identities: entries.map(row => row.identity), master,
      ...(named && action === 'classify' ? { class_key: dimensions[0]?.class_key } : {}),
      ...(!named && ['classify', 'ungroup'].includes(action) ? { classification: entries.find(row => row.identity === master)?.classification ?? catalog.classification_schema.values[0] ?? '' } : {}) });
  };
  const edit = (patch: Partial<CareBody>) => { if (body) { const next = { ...body, confirm_class_change: undefined, ...patch, request_id: uuid() }; if (next.action !== 'group') delete next.new_master_name; setBody(next); setError(''); setErrorCode(''); setImpact(null); setChanged(true); setSaved(false); } };
  const groupReady = !body || body.action !== 'group' || (body.new_master_name !== undefined ? !!body.new_master_name.trim() && (named || !!body.classification) : !!body.master);
  const selectedIds = new Set(Object.values(selected).map(row => row.identity));
  const selectionCurrent = !!work || !body || (body.identities.every(id => selectedIds.has(id)) && body.expected_content_revision === revision && body.expected_active_revision === activeRevision);
  const next = (updated: Queue) => {
    setWork(updated);
    if (updated.groups.length) begin('group', updated.groups[0]!.members, updated);
    else { setWork(null); setBody(null); }
  };
  const check = async (interactive = true) => {
    if (!body || !groupReady || !selectionCurrent) return; const seq = ++sequence.current; setBusy(true); setError(''); const submitted = JSON.stringify(body); const started = performance.now();
    try { const value = await api<Impact>('catalogs/care/preview', { method: 'POST', body, interactive }); if (seq === sequence.current && latest.current === submitted) { setImpact({ value, body: submitted }); setTimings(previous => ({ ...previous, ...(value.timings_ms ?? {}), preview: performance.now() - started })); } }
    catch (failure) { if (seq === sequence.current && latest.current === submitted) failed(failure); } finally { if (seq === sequence.current) setBusy(false); }
  };
  const payloadKey = JSON.stringify(body);
  useEffect(() => { if (!body || !groupReady || !selectionCurrent) { if (!saving) setBusy(false); return; } setImpact(null); const timer = window.setTimeout(() => void check(false), 200); return () => { window.clearTimeout(timer); sequence.current++; }; }, [payloadKey, revision, activeRevision, selectionCurrent]);
  const save = async () => {
    if (!body || !groupReady || !selectionCurrent || !impact || impact.body !== JSON.stringify(body) || busy || error) return;
    setBusy(true); setSaving(true); setError('');
    try {
      const started = performance.now();
      const result = await api<{ content_revision: number }>('catalogs/care/write', { method: 'POST', body, interactive: true });
      setSaved(true); setChanged(false);
      const write = performance.now() - started; const refreshing = performance.now();
      setSelected({});
      await cache.invalidateQueries({ queryKey: ['catalog-entries'] });
      await cache.invalidateQueries({ queryKey: ['catalog-history'] });
      await cache.invalidateQueries({ queryKey: ['catalog-details'] });
      await cache.invalidateQueries({ queryKey: ['catalog-masters'] });
      setTimings(previous => ({ ...previous, write, refresh: performance.now() - refreshing }));
      if (work) next({ ...work, groups: work.groups.slice(1), content_revision: result.content_revision });
      else setBody(null);
    } catch (failure) { failed(failure); } finally { setBusy(false); setSaving(false); }
  };
  const startQueue = async () => {
    setBusy(true); setError('');
    try {
      const result = await api<Queue>(`catalogs/care/queue?${parameters}`);
      if (!result.groups.length) { setError(t('b17.noCandidates')); return; }
      setWork(result); begin('group', result.groups[0]!.members, result);
    } catch (failure) { failed(failure); } finally { setBusy(false); }
  };
  const reloadSelection = async () => {
    if (work) { await startQueue(); return; }
    await cache.invalidateQueries({ queryKey: ['catalog-entries'] });
    setBody(null); setChanged(false); setError(''); setErrorCode('');
  };
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      <Button onClick={() => { const next = { ...selected }; const all = rows.every(row => !!selected[entryKey(row)]); for (const row of rows) { if (all) delete next[entryKey(row)]; else next[entryKey(row)] = row; } setSelected(next); }}>{t('b17.selectVisible')}</Button>
      <Button disabled={!Object.keys(selected).length} onClick={() => setSelected({})}>{t('b17.clearSelection')}</Button>
      <p>{t('b17.selected', { n: Object.keys(selected).length })}</p>
    </div>
    <div className="flex flex-wrap gap-2">{['classify', 'group', 'review', 'open', 'hide', 'unhide', 'ungroup'].filter(action => !isGameCatalog(catalog) || !['group', 'ungroup'].includes(action)).map(action => <Button key={action} disabled={busy || !Object.keys(selected).length || Object.keys(selected).length > 100} onClick={() => { setWork(null); begin(action); }}>{t(`b17.action.${action}`)}</Button>)}
      {!isGameCatalog(catalog) && <Button disabled={busy} onClick={() => void startQueue()}>{t('b17.variantsCheck')}</Button>}
    </div>
    {Object.keys(selected).length > 100 && <Notice tone="warn">{t('b17.selectionLimit')}</Notice>}
    {error && !body && <Notice tone="bad">{error}</Notice>}
    {saved && !body && <Notice tone="ok">{t('a2.edit.catalogSaved')}</Notice>}
    {!!Object.keys(timings).length && <details><summary>{t('b20.timing.title')}</summary><dl>{Object.entries(timings).map(([name, ms]) => <div key={name}><dt>{t(`b20.timing.${name}`)}</dt><dd>{t('b20.timing.value', { ms: Math.round(ms) })}</dd></div>)}</dl></details>}
    <Dialog open={!!body} onOpenChange={open => !open && guard.requestClose()} title={t(work ? 'b17.variantsCheck' : 'b17.bulk')} footer={<>
      {work && <><Button disabled={busy} title={t('a2.care.skipHelp')} onClick={() => guard.requestClose(() => next({ ...work, groups: work.groups.slice(1) }))}>{t('b17.skip')}</Button>
        <Button disabled={busy} title={t('a2.care.independentHelp')} onClick={() => edit({ action: 'standalone_review' })}>{t('b17.keepIndependent')}</Button></>}
      <Button disabled={saving} onClick={() => guard.requestClose()}>{t('common.cancel')}</Button>
      <Button disabled={busy || !body || !groupReady || !selectionCurrent || body.identities.length > 100} onClick={() => void check()}>{t('alpha10.catalog.preview')}</Button>
      <Button variant="primary" disabled={busy || !!error || !groupReady || !selectionCurrent || !body || !impact || impact.body !== JSON.stringify(body)} onClick={() => void save()}>{t('alpha10.catalog.save')}</Button>
    </>}>
      <EffectStrip effect="catalog" />
      {saved && <Notice tone="ok">{t('a2.edit.catalogSaved')}</Notice>}
      <div className="space-y-4">
        <p>{t('b17.careDescription')}</p><Notice tone="warn">{t('b17.tcWins')}</Notice>
        {work && <p>{t('b17.groupsRemaining', { n: work.groups.length })}</p>}
        {work && <div className="space-y-1 text-sm text-muted"><p>{t('a2.care.skipHelp')}</p><p>{t('a2.care.independentHelp')}</p></div>}
        {body && <p>{t(`b17.action.${body.action}`)}</p>}
        {!selectionCurrent && <Notice tone="warn">{t('a2.care.selectionChanged')} <Button disabled={busy} onClick={() => void reloadSelection()}>{t('a2.care.reload')}</Button></Notice>}
        <ul>{members.map(row => <li key={row.identity}>{titleLabel(row)}</li>)}</ul>
        {body?.action === 'group' && <><Field label={t('alpha10.catalog.master')}>{p => <Select {...p} disabled={busy} value={body.new_master_name !== undefined ? '__new_master__' : body.master ?? ''} onChange={event => edit(event.target.value === '__new_master__' ? { master: undefined, new_master_name: '', classification: catalog.classification_schema.values[0] } : { master: event.target.value, new_master_name: undefined, classification: undefined })}>{members.map(row => <option key={row.identity} value={row.identity}>{titleLabel(row)}{row.master_key ? ' · ' + t('a2.care.promoteWithFamily') : ''}</option>)}<option value="__new_master__">{t('b22.catalog.newMaster')}</option></Select>}</Field>{body.new_master_name !== undefined && <><Field label={t('b22.catalog.masterName')}>{p => <Input {...p} disabled={saving} maxLength={1024} value={body.new_master_name} onChange={event => edit({ new_master_name: event.target.value })} />}</Field><p className="text-sm text-muted">{t('b22.catalog.newMasterHelp')}</p></>}</>}
        {body?.action === 'classify' && named && <>
          <Field label={t('alpha10.catalog.class')}>{props => <Select {...props} value={body.class_key ?? ''} onChange={event => edit({ class_key: event.target.value, class_value: undefined })}>
            {dimensions.map(item => <option key={item.class_key} value={item.class_key}>{item.display_name}</option>)}
          </Select>}</Field>
          {selectedClass && <ClassField definition={selectedClass} value={body.class_value} disabled={busy} onChange={value => edit({ class_value: value })} />}
        </>}
        {body?.action === 'group' && named && body.new_master_name !== undefined && <ClassFields definitions={dimensions} values={body.class_values ?? {}} onChange={values => edit({ class_values: values })} disabled={busy} />}
        {body && !named && ['classify', 'group', 'ungroup'].includes(body.action) && <Field label={t(body.action === 'group' ? 'b22.catalog.masterClass' : 'alpha10.catalog.class')}>{p => <Select {...p} disabled={busy} value={body.classification ?? ''} onChange={event => edit({ classification: event.target.value || undefined })}>
          {body.action === 'group' && body.new_master_name === undefined && <option value="">{t('a2.care.keepMasterClass')}</option>}
          {catalog.classification_schema.values.map(value => <option key={value} value={value}>{classLabel(value)}</option>)}</Select>}</Field>}
        {busy && <Spinner />}{error && <Notice tone="bad">
          <p>{error}</p>
          {errorCode === 'catalog_confirm_class_change' ? <Button disabled={busy} onClick={() => edit({ confirm_class_change: true })}>{t('a2.care.confirmClass')}</Button>
            : ['catalog_content_conflict', 'active_revision_conflict', 'catalog_page_invalid'].includes(errorCode) ? <Button disabled={busy} onClick={() => void reloadSelection()}>{t('a2.care.reload')}</Button>
              : body?.action === 'group' ? <Button disabled={busy} onClick={() => edit({ action: 'standalone_review' })}>{t('a2.care.reviewIndividually')}</Button>
                : <Button disabled={busy} onClick={() => void check()}>{t('alpha10.catalog.preview')}</Button>}
        </Notice>}
        {impact && impact.body === JSON.stringify(body) && <>
          <p>{t('alpha10.catalog.impact', { keys: impact.value.affected_keys, contracts: impact.value.affected_contracts })}</p>
          {impact.value.changes.length === 0 && <p>{t('a2.care.noChanges')}</p>}
          <ul>{impact.value.changes.map((row, index) => <li key={index}>{titleLabel(row)} · {row.classification ? classLabel(row.classification) : row.master_key ? titleLabel({ key: row.master_key }) : ''}</li>)}</ul>
          <ul>{impact.value.context_effects.map((effect, index) => <li key={index}>{effect.contract_id} · {effect.storage} · {effect.slot}</li>)}</ul>
        </>}
      </div>
    </Dialog>
    {guard.confirmation}
  </div>;
}

export function CatalogDetail({ catalog, entry, onClose, onOpen }: { catalog: Catalog; entry: Entry | null; onClose: () => void; onOpen: (row: Entry) => void }) {
  const [after, setAfter] = useState('');
  const data = useQuery({ queryKey: ['catalog-details', catalog.catalog_id, catalog.version, entry?.identity, after], enabled: !!entry,
    queryFn: () => api<{ entry: Entry; master: Entry | null; variants: Entry[]; next_cursor: string | null; contexts: (NonNullable<Entry['tc_metadata']>['contexts'][number] & { catalog_id: string | null; effective_classification: string | null })[] }>(`catalogs/care/detail?${new URLSearchParams({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: entry!.identity, after })}`) });
  const value = data.data?.entry; const meta = value?.tc_metadata;
  const displayClass = (name?: string | null) => name ? catalog.classification_schema.legend?.[name]?.display_name ?? name : t('a2.care.noStoredClass');
  const open = (row: Entry) => { setAfter(''); onOpen(row); };
  return <Dialog open={!!entry} onOpenChange={shown => { if (!shown) { setAfter(''); onClose(); } }} title={t('b17.details')}>
    <p>{t('b17.detailDescription')}</p>
    {data.isPending ? <Spinner /> : data.isError ? <Notice tone="bad">{describeError(data.error)}</Notice> : value && <div className="space-y-4">
      <h3>{titleLabel(value)}</h3><CatalogMediaFacts metadata={value.catalog_metadata} />{value.display_metadata?.artist && <p>{t('b22.stash.performers')}: {value.display_metadata.artist}</p>}
      <details><summary>{t('alpha10.technical')}</summary><p>{catalog.catalog_id} · {catalog.version}</p><p>{value.signal_type === 'app' ? value.key : value.identity} · {value.scope} · {value.media_type} · {value.signal_type}</p></details>
      <dl className="grid gap-2 sm:grid-cols-[max-content_1fr]">
        <dt>{t('b26.catalog.role')}</dt><dd>{t(value.master_identity || value.master_key ? 'b22.catalog.variantRole' : value.variant_count ? 'alpha10.catalog.master' : 'alpha10.catalog.independent')}</dd>
         {catalog.class_definitions?.length ? catalog.class_definitions.map(definition => {
           const origin = value.class_origins?.[definition.class_key];
           return <div className="contents" key={definition.class_key}><dt>{definition.display_name}</dt><dd>
             {typedClassLabel(definition, value.effective_class_values?.[definition.class_key])}
             {origin && <span className="text-muted"> · {t(origin === 'inherited' ? 'a2.care.classFromMaster' : origin === 'explicit' ? 'a2.care.classFromRecord' : 'a2.care.classFromDefault')}</span>}
           </dd></div>;
         }) : <>
           <dt>{t('a2.care.storedClass')}</dt><dd>{displayClass(value.classification)}</dd>
           <dt>{t('a2.care.defaultClass')}</dt><dd>{displayClass(catalog.unassigned_value)}</dd>
           <dt>{t('b17.effectiveClass')}</dt><dd>{displayClass(value.effective_classification)} <span className="text-muted">· {t(value.master_identity || value.master_key ? 'a2.care.classFromMaster' : value.classification ? 'a2.care.classFromRecord' : 'a2.care.classFromDefault')}</span></dd>
         </>}
        <dt>{t('b26.catalog.status')}</dt><dd>{t(`b17.status.${value.review_status ?? 'open'}`)}</dd>
      </dl>
      {data.data.master && <Button onClick={() => open(data.data!.master!)}>{t('alpha10.catalog.master')}: {titleLabel(data.data.master)}</Button>}
      {meta && <dl><dt>{t('b17.artist')}</dt><dd>{meta.artist ?? '—'}</dd><dt>{t('b17.title')}</dt><dd>{meta.title ?? '—'}</dd><dt>{t('b17.sourceClass')}</dt><dd>{meta.source_class}</dd></dl>}
      <p>{t('b17.totalSightings', { n: value.seen_count ?? 0, first: formatTime(value.first_seen), last: formatTime(value.last_seen) })}</p>
      <h3>{t('b17.contexts')}</h3>
      <ul>{data.data.contexts?.map((ctx, index) => <li key={index}><p>{contextLabel(ctx.context)} · {ctx.source_app ? contextLabel(ctx.source_app) : ''} · {ctx.catalog_id ? catalogLabel({ catalog_id: ctx.catalog_id, version: 1 }) : t('alpha10.catalog.unassigned')} · {t('b17.override')}: {ctx.enum_override ?? '—'}</p><p>{t('b17.effectiveClass')}: {ctx.effective_classification ?? t('alpha10.catalog.unassigned')}</p><p>{t('b17.totalSightings', { n: ctx.telemetry?.seen_count ?? 0, first: formatTime(ctx.telemetry?.first_seen), last: formatTime(ctx.telemetry?.last_seen) })}</p></li>)}</ul>
      {value.local_sightings && <p>{t('b17.localSightings', { n: value.local_sightings.seen_count })}</p>}
      <h3>{t('b17.variants')}</h3><ul>{data.data.variants.map(row => <li key={row.identity}><Button onClick={() => open(row)}>{titleLabel(row)}</Button></li>)}</ul>
      <Button disabled={!data.data.next_cursor} onClick={() => setAfter(data.data?.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button>
    </div>}
  </Dialog>;
}
