import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router';
import { t } from '@/i18n';
import { api, ApiError, describeError } from '@/lib/api';
import { formatTime } from '@/lib/format';
import { uuid } from '@/lib/uuid';
import { useActiveRevision } from '@/lib/queries';
import { Button } from '@/components/ui/button';
import { CatalogTable, entryKey } from './CatalogTable';
import { CatalogInspection } from './CatalogInspection';
import { Field, Input, Select, Textarea } from '@/components/ui/form';
import { ConfirmDialog, Dialog } from '@/components/ui/dialog';
import { Trash2 } from 'lucide-react';
import { Empty, Notice, PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { TitleSync } from './TitleSync';
import { CatalogCare, CatalogDetail } from './CatalogCare';
import { titleLabel } from './titleLabel';
import { CatalogCleanup } from './CatalogCleanup';
import { CatalogClasses } from './CatalogClasses';
import { CatalogScopeRepair } from './CatalogScopeRepair';
import { CatalogVersions } from './CatalogVersions';
import { catalogLabel, catalogValue, contextLabel } from './catalogLabels';
import { type CatalogMediaMetadata } from './CatalogMediaFacts';
import { useRouteFilters } from '@/lib/route-filters';
import { EffectStrip } from '@/features/authoring/DraftEditor';
import { useEditorGuard } from '@/features/authoring/EditorGuard';
import { asArray, asObject } from '@/features/authoring/model';
import { deepEqual } from '@/lib/json';
import { ClassField, ClassFields, uniqueClasses, type ClassDefinition, type ClassValue } from './ClassFields';

export interface Catalog { class_definitions?: ClassDefinition[]; catalog_id: string; version: number; device_scope?: string | null; display_name?: string; unassigned_value?: string; classification_schema: { values: string[]; legend?: Record<string, { display_name: string; description: string }> } }
export interface Entry { class_values?: Record<string, ClassValue>; effective_class_values?: Record<string, ClassValue>; class_origins?: Record<string, 'default' | 'explicit' | 'inherited'>; catalog_id?: string; definition_version?: number; content_revision?: number; family_seen_count?: number; family_last_seen?: string | null; catalog_metadata?: CatalogMediaMetadata; display_metadata?: { title?: string; artist?: string }; master_identity?: string | null; matches_filter?: boolean; matching_variant_count?: number; master_order?: number; playback_counts?: boolean; tombstone?: boolean; identity: string; key: string; scope: string; media_type: string; signal_type: string; classification: string | null; master_key: string | null; display_name: string; note: string; effective_classification: string; complete: boolean; first_seen?: string; last_seen?: string; seen_count?: number; semantic_digest: string; review_status?: string; candidate?: boolean; variant_count?: number; local_sightings?: { seen_count: number }; tc_metadata?: { artist?: string | null; title?: string | null; source_class: number; contexts: { context: string; source_app: string; enum_override?: number | null; telemetry?: { seen_count: number; first_seen: string; last_seen: string } | null }[] } }
interface Page { app_labels?: Record<string, string>; items: Entry[]; content_revision: number; next_cursor: string | null; contexts?: string[]; context_targets?: Record<string, { catalog_id: string; scope: string }>; filter_values?: Record<string, string[]> }

export default function CatalogPage() {
  const cache = useQueryClient();
  const settings = useLocation().pathname === '/settings/catalog-care';
  const active = useActiveRevision();
  const catalogs = useQuery({ queryKey: ['managed-catalogs', active.data?.revision], staleTime: 30_000, queryFn: () => api<Catalog[]>('catalogs') });
  const route = useRouteFilters();
  const [chosen, setChosen] = route.field('catalog', '');
  const [past, setPast] = useState<number | null>(null); const [pastAfter, setPastAfter] = useState('');
  const [restoringId, setRestoringId] = useState('');
  const [impact, setImpact] = useState<{ affected_keys: number; affected_contracts: number; changes?: Entry[]; request: string } | null>(null);
  const viewCatalog = catalogs.data?.find(c => `${c.catalog_id}@${c.version}` === chosen);
  const [editingCatalog, setEditingCatalog] = useState<Catalog | null>(null); const [detailsCatalog, setDetailsCatalog] = useState<Catalog | null>(null);
  const catalog = editingCatalog ?? viewCatalog;
  const [classFilter, setClassFilter] = route.field('classification', '');
  const [dimensionKey, setDimensionKey] = route.field('class_key', '');
  const [dimensionValue, setDimensionValue] = route.field('class_value', '');
  const dimensions = uniqueClasses(viewCatalog ? [viewCatalog] : catalogs.data ?? []);
  const dimension = dimensions.find(item => item.class_key === dimensionKey);
  const dimensionFilter = (() => { try { return JSON.parse(dimensionValue) as ClassValue; } catch { return undefined; } })();
  const classChoice: string[] = (() => { try { const value: unknown = JSON.parse(classFilter || '[]'); return Array.isArray(value) && value.every(item => typeof item === 'string') ? value : []; } catch { return []; } })();
  const [view, setView] = route.field('view', 'all'); const [query, setQuery] = route.field('q', ''); const [after, setAfter] = route.field('after', '');
  const [sort, setSort] = route.field('sort', 'newest'); const [variantsFlag, setVariantsFlag] = route.field('variants_first', 'false'); const variantsFirst = variantsFlag === 'true'; const setVariantsFirst = (value: boolean) => setVariantsFlag(value ? 'true' : 'false'); const [contextFilter, setContextFilter] = route.field('context', ''); const [structure, setStructure] = route.field('structure', '');
  const [appFilter, setAppFilter] = route.field('app', ''); const [liveFilter, setLiveFilter] = route.field('live_tv', ''); const [senderFilter, setSenderFilter] = route.field('sender', '');
  const [selection, setSelection] = useState<{ scope: string; entries: Record<string, Entry> }>({ scope: '', entries: {} });
  const [details, setDetails] = useState<Entry | null>(null);
  const [editing, setEditing] = useState<Entry | null>(null); const [classification, setClassification] = useState('');
  const [classValues, setClassValues] = useState<Record<string, ClassValue>>({});
  const [master, setMaster] = useState(''); const [mode, setMode] = useState('independent');
  const [note, setNote] = useState(''); const [displayName, setDisplayName] = useState(''); const [removingEntry, setRemovingEntry] = useState(false); const [confirmSave, setConfirmSave] = useState(false); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [classConfirmed, setClassConfirmed] = useState(false);
  const [errorCode, setErrorCode] = useState('');
  const editingDirty = !!editing && (!deepEqual(classValues, editing.class_values ?? {}) || classification !== (editing.classification ?? editing.effective_classification ?? catalog?.classification_schema.values[0] ?? '') || mode !== (editing.master_key ? 'variant' : 'independent') || master !== (editing.master_key ?? '') || note !== editing.note || displayName !== editing.display_name || removingEntry);
  const editGuard = useEditorGuard(editingDirty, () => { setEditing(null); setConfirmSave(false); }, busy);
  const restoreGuard = useEditorGuard(past !== null && impact !== null, () => setPast(null), busy);
  const [expected, setExpected] = useState(0); const [expectedActive, setExpectedActive] = useState(0); const [comparison, setComparison] = useState('');
  const [scopeFilter, setScopeFilter] = route.field('scope', ''); const [mediaFilter, setMediaFilter] = route.field('media_type', ''); const [signalFilter, setSignalFilter] = route.field('signal_type', ''); const [masterQuery, setMasterQuery] = useState(''); const [requestId, setRequestId] = useState('');
  const parameters = new URLSearchParams({ catalog_id: viewCatalog?.catalog_id ?? 'all', definition_version: String(viewCatalog?.version ?? 1), classification: classChoice[1] ?? '', classification_catalog: classChoice[0] ?? '', class_key: dimension?.class_key ?? '', class_value: dimension ? dimensionValue : '', view: view === 'history' ? 'all' : view, q: query, after, scope: scopeFilter, media_type: mediaFilter, signal_type: signalFilter, context: contextFilter, structure, sort, variants_first: String(variantsFirst), grouped: 'true', app: appFilter, live_tv: liveFilter, sender: senderFilter });
  const selectionScope = parameters.toString() + ':' + view + ':' + String(active.data?.revision ?? 0);
  const selected = selection.scope === selectionScope ? selection.entries : {};
  const setSelected = useCallback((change: Record<string, Entry> | ((current: Record<string, Entry>) => Record<string, Entry>)) => {
    setSelection(previous => {
      const current = previous.scope === selectionScope ? previous.entries : {};
      const next = typeof change === 'function' ? change(current) : change;
      return previous.scope === selectionScope && next === previous.entries ? previous : { scope: selectionScope, entries: next };
    });
  }, [selectionScope]);
  const entries = useQuery({ queryKey: ['catalog-entries', viewCatalog?.catalog_id ?? 'all', viewCatalog?.version, classFilter, dimensionKey, dimensionValue, view, query, after, scopeFilter, mediaFilter, signalFilter, contextFilter, structure, sort, variantsFirst, appFilter, liveFilter, senderFilter], enabled: !!catalogs.data?.length && view !== 'history', queryFn: () => api<Page>(`catalogs/care?${parameters}`) });
  const masters = useQuery({ queryKey: ['catalog-masters', catalog?.catalog_id, catalog?.version, editing?.scope, editing?.media_type, editing?.signal_type, masterQuery], enabled: !!catalog && mode === 'variant' && !!editing, queryFn: () => api<Page>(`catalogs/entries?${new URLSearchParams({ catalog_id: catalog!.catalog_id, definition_version: String(catalog!.version), view: 'masters', q: masterQuery, scope: editing!.scope, media_type: editing!.media_type, signal_type: editing!.signal_type })}`) });
  const history = useQuery({ queryKey: ['catalog-history', catalog?.catalog_id, after], enabled: !!catalog && view === 'history', queryFn: () => api<{ revision: number; head_revision: number; active_revision: number; at: string; reason: string; affected: string[] }[]>(`catalogs/history?${parameters}`) });
  const pastContent = useQuery({ queryKey: ['catalog-past', catalog?.catalog_id, past, pastAfter], enabled: !!catalog && past !== null, queryFn: () => api<{ items: Entry[]; total: number; next_cursor: string | null }>(`catalogs/revision?${new URLSearchParams({ catalog_id: catalog!.catalog_id, definition_version: String(catalog!.version), revision: String(past), after: pastAfter })}`) });
  const restoreBody = () => ({ catalog_id: catalog!.catalog_id, definition_version: catalog!.version, restore_revision: past, expected_active_revision: expectedActive, expected_content_revision: expected, request_id: restoringId, reason: t('alpha10.catalog.restoreReason') });
  const restore = async () => { if (!catalog || past === null || !impactCurrent) return; setBusy(true); setError(''); try {
    await api('catalogs/entries', { method: 'POST', interactive: true, body: restoreBody() });
    restoreGuard.finish(); setSaved(true); await cache.invalidateQueries({ queryKey: ['catalog-entries'] }); await cache.invalidateQueries({ queryKey: ['catalog-history'] }); await cache.invalidateQueries({ queryKey: ['catalog-details'] }); await cache.invalidateQueries({ queryKey: ['catalog-masters'] });
  } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  const open = (entry: Entry, owner: Catalog = catalog!) => { setSaved(false); setEditingCatalog(owner); setEditing(entry); setClassValues(entry.class_values ?? {}); setClassification(entry.classification ?? entry.effective_classification ?? owner?.classification_schema.values[0] ?? ''); setMode(entry.master_key ? 'variant' : 'independent'); setMaster(entry.master_key ?? ''); setNote(entry.note); setDisplayName(entry.display_name); setRemovingEntry(false); setConfirmSave(false); setExpected(entry.content_revision ?? entries.data?.content_revision ?? 0); setExpectedActive(active.data?.revision ?? 0); setComparison(''); setMasterQuery(''); setRequestId(uuid()); setError(''); setErrorCode(''); setClassConfirmed(false); };
  useEffect(() => { setRequestId(uuid()); setImpact(null); }, [classification, classValues, classConfirmed, master, mode, note, displayName, removingEntry, editing, past, expected, expectedActive]);
  const body = () => mode === 'promote' ? { catalog_id: catalog!.catalog_id, definition_version: catalog!.version, expected_active_revision: expectedActive, expected_content_revision: expected, request_id: requestId, reason: t('b17.careReason'), action: 'promote', identities: [editing!.identity], ...(catalog?.class_definitions?.length ? (Object.keys(classValues).length ? { class_values: classValues } : {}) : { classification }), ...(classConfirmed ? { confirm_class_change: true } : {}) } : ({ catalog_id: catalog!.catalog_id, definition_version: catalog!.version,
        expected_active_revision: expectedActive, expected_content_revision: expected, request_id: requestId,
        reason: t('alpha10.catalog.reason'), changes: [{ scope: editing!.scope, media_type: editing!.media_type, signal_type: editing!.signal_type, key: editing!.key,
          ...(catalog?.class_definitions?.length ? { class_values: mode === 'variant' ? {} : classValues } : { classification: mode === 'variant' ? null : classification }), master_key: mode === 'variant' ? master : null, note, display_name: displayName, tombstone: removingEntry }] });
  const currentPayload = catalog ? past !== null ? restoreBody() : editing ? body() : null : null;
  const impactCurrent = !!impact && impact.request === JSON.stringify(currentPayload);
  const inspectImpact = async (restoring = false) => { if (!catalog || (!editing && !restoring)) return; const payload = restoring ? restoreBody() : body(); setBusy(true); setError(''); setErrorCode(''); try { const result = await api<{ affected_keys: number; affected_contracts: number; changes?: Entry[] }>(!restoring && mode === 'promote' ? 'catalogs/care/preview' : 'catalogs/preview', { method: 'POST', interactive: true, body: payload }); setImpact({ ...result, request: JSON.stringify(payload) }); } catch (failure) { setError(describeError(failure)); setErrorCode(failure instanceof ApiError ? failure.code : ''); } finally { setBusy(false); } };
  const save = async () => {
    if (!editing || !catalog || !impactCurrent) return;
    setBusy(true); setError('');
    try {
      await api(mode === 'promote' ? 'catalogs/care/write' : 'catalogs/entries', { method: 'POST', interactive: true, body: body() });
      if (removingEntry) setSelected(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => key !== entryKey(editing))));
      await cache.invalidateQueries({ queryKey: ['managed-catalogs'] });
      editGuard.finish(); setSaved(true); await cache.invalidateQueries({ queryKey: ['catalog-entries'] }); await cache.invalidateQueries({ queryKey: ['catalog-history'] }); await cache.invalidateQueries({ queryKey: ['catalog-details'] }); await cache.invalidateQueries({ queryKey: ['catalog-masters'] });
    } catch (failure) { setConfirmSave(false); setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  const label = catalogValue;
  const tableRows = useMemo(() => (entries.data?.items ?? []).map(entry => ({ ...entry, catalog_id: entry.catalog_id ?? viewCatalog?.catalog_id, definition_version: entry.definition_version ?? viewCatalog?.version, content_revision: entry.content_revision ?? entries.data?.content_revision })), [entries.data, viewCatalog?.catalog_id, viewCatalog?.version]);
  useEffect(() => {
    const visible = new Map(tableRows.map(row => [entryKey(row), row]));
    setSelected(current => {
      const next: Record<string, Entry> = {};
      for (const [key, chosen] of Object.entries(current)) {
        const row = visible.get(key);
        if (row) next[key] = row;
        else if (chosen.master_identity && visible.has(entryKey({ ...chosen, identity: chosen.master_identity }))) next[key] = chosen;
      }
      return Object.keys(next).length === Object.keys(current).length && Object.entries(next).every(([key, row]) => current[key] === row) ? current : next;
    });
  }, [tableRows, setSelected]);
  const masterMatches = (masters.data?.items ?? []).filter(row => row.identity !== editing?.identity && row.scope === editing?.scope && row.media_type === editing?.media_type && row.signal_type === editing?.signal_type);
  return <>
    <PageHeader title={t('alpha10.catalog.title')} description={t('b17.catalogDescription')} actions={<TitleSync catalogs={catalogs.data ?? []} />} />
    {saved && <Notice tone="ok">{t('a2.edit.catalogSaved')}</Notice>}
    <div id="title-sync-panel" />
      <p className="mb-4 text-sm text-muted">{t('b20.catalog.countHelp')}</p>
    {catalogs.isPending ? <Spinner /> : catalogs.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : !catalogs.data?.length ? <Empty title={t('alpha10.catalog.noCatalog')}>{t('alpha10.catalog.defineFirst')}</Empty> : <Panel className="space-y-4 p-5">
      <div className="grid gap-4 md:grid-cols-3">
        <Field label={t('alpha10.catalog.catalog')}>{p => <Select {...p} value={chosen} onChange={e => { setChosen(e.target.value); setEditingCatalog(null); setClassFilter(''); setView('all'); setAfter(''); setAppFilter(''); setLiveFilter(''); setSenderFilter(''); setSelected({}); setDetails(null); }}><option value="">{t('b26.catalog.allCatalogs')}</option>{catalogs.data?.map(c => <option key={`${c.catalog_id}@${c.version}`} value={`${c.catalog_id}@${c.version}`}>{catalogLabel(c)}</option>)}</Select>}</Field>
        <Field label={t('alpha10.catalog.view')}>{p => <Select {...p} value={view} onChange={e => { setView(e.target.value); setAfter(''); }}>{['open', 'reviewed', 'hidden'].map(v => <option key={v} value={v}>{t(`b17.status.${v}`)}</option>)}{['unassigned', 'all', 'masters', ...(viewCatalog ? ['history'] : [])].map(v => <option key={v} value={v}>{t(`alpha10.catalog.${v}`)}</option>)}</Select>}</Field>
        <Field label={t('alpha10.catalog.search')}>{p => <Input {...p} value={query} onChange={e => { setQuery(e.target.value); setAfter(''); }} />}</Field>
      </div>
      <details className="space-y-3"><summary>{t('a2.care.advancedFilters')}</summary>
      {viewCatalog?.catalog_id === 'media.appletv_titles' && <div className="grid gap-4 md:grid-cols-3">
        <Field label={t('b22.apple.app')}>{p => <Select {...p} value={appFilter} onChange={event => { setAppFilter(event.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option>{(entries.data?.filter_values?.app ?? []).map(id => <option key={id} value={id}>{entries.data?.app_labels?.[id] || t('b22.apple.unknownApp')}</option>)}</Select>}</Field>
        <Field label={t('b22.apple.kind')}>{p => <Select {...p} value={liveFilter} onChange={event => { setLiveFilter(event.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option><option value="true">{t('b22.apple.live')}</option><option value="false">{t('b22.apple.onDemand')}</option></Select>}</Field>
        <Field label={t('b22.apple.sender')}>{p => <Select {...p} value={senderFilter} onChange={event => { setSenderFilter(event.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option>{(entries.data?.filter_values?.sender ?? []).map(name => <option key={name} value={name}>{name}</option>)}</Select>}</Field>
      </div>}
      <div className="grid gap-4 md:grid-cols-3">{[
        ['scope', scopeFilter, setScopeFilter], ['media_type', mediaFilter, setMediaFilter], ['signal_type', signalFilter, setSignalFilter],
      ].map(([name, value, update]) => <Field key={String(name)} label={t(name === 'scope' ? 'b20.catalog.scope' : `alpha10.catalog.field.${name}`)}>{p => <Select {...p} value={String(value)} onChange={e => { (update as (value: string) => void)(e.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option>{(entries.data?.filter_values?.[String(name)] ?? []).map(option => <option key={option} value={option}>{label(String(name), option)}</option>)}</Select>}</Field>)}</div>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label={t('b17.sort')}>{p => <Select {...p} value={sort} onChange={event => { setSort(event.target.value); setAfter(''); }}>{['newest', 'oldest', 'title', 'classification'].map(value => <option key={value} value={value}>{t(`b17.sort.${value}`)}</option>)}</Select>}</Field>
        <Field label={t('b17.contextDevice')}>{p => <Select {...p} value={contextFilter} onChange={event => { setContextFilter(event.target.value); setAfter(''); }}><option value="">{t('b17.allContexts')}</option>{entries.data?.contexts?.map(value => { const target = entries.data.context_targets?.[value]; return <option key={value} value={value}>{target ? catalogLabel(catalogs.data?.find(c => c.catalog_id === target.catalog_id) ?? { catalog_id: target.catalog_id, version: 1 }) : contextLabel(value)}</option>; })}</Select>}</Field>
        <Field label={t('b17.structure')}>{p => <Select {...p} value={structure} onChange={event => { setStructure(event.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option><option value="masters">{t('alpha10.catalog.masters')}</option><option value="variants">{t('b17.variants')}</option></Select>}</Field>
        <label><input type="checkbox" checked={variantsFirst} onChange={event => { setVariantsFirst(event.target.checked); setAfter(''); }} /> {t('b17.variantsFirst')}</label>
      </div>
      </details>
      {viewCatalog?.catalog_id === 'media.stash' && <p className="text-sm text-muted">{t('a2.care.stashFamily')}</p>}
      <Field label={t('alpha10.catalog.class')}>{p => <Select {...p} value={classFilter} onChange={event => { setClassFilter(event.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option>{(viewCatalog ? [viewCatalog] : catalogs.data ?? []).map(item => <optgroup key={item.catalog_id + "@" + item.version} label={catalogLabel(item)}>{[...item.classification_schema.values, '__unassigned__'].map(value => <option key={value} value={JSON.stringify([`${item.catalog_id}@${item.version}`, value])}>{value === '__unassigned__' ? t('alpha10.catalog.unassigned') : item.classification_schema.legend?.[value]?.display_name ?? value}</option>)}</optgroup>)}</Select>}</Field>
      {view !== 'history' && (viewCatalog ? [viewCatalog] : (catalogs.data ?? []).filter(item => Object.values(selected).some(row => row.catalog_id === item.catalog_id && row.definition_version === item.version))).map(item => <CatalogCare key={selectionScope + "/" + item.catalog_id + "@" + item.version} catalog={item} rows={tableRows.filter(row => row.catalog_id === item.catalog_id && row.definition_version === item.version)} selected={Object.fromEntries(Object.entries(selected).filter(([, row]) => row.catalog_id === item.catalog_id && row.definition_version === item.version))} setSelected={next => setSelected(previous => ({ ...Object.fromEntries(Object.entries(previous).filter(([, row]) => row.catalog_id !== item.catalog_id || row.definition_version !== item.version)), ...next }))} revision={tableRows.find(row => row.catalog_id === item.catalog_id && row.definition_version === item.version)?.content_revision ?? entries.data?.content_revision ?? 0} activeRevision={active.data?.revision ?? 0} parameters={new URLSearchParams({ ...Object.fromEntries(parameters), catalog_id: item.catalog_id, definition_version: String(item.version) }).toString()} />)}
      {!!dimensions.length && <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t('b31.classes.filter')}>{props => <Select {...props} value={dimensionKey} onChange={event => {
          setDimensionKey(event.target.value); setDimensionValue(''); setAfter('');
        }}><option value="">{t('b31.classes.all')}</option>{dimensions.map(item => <option key={item.class_key} value={item.class_key}>{item.display_name}</option>)}</Select>}</Field>
        {dimension && <ClassField definition={dimension} value={dimensionFilter} emptyLabel={t('b31.classes.allValues')} onChange={value => {
          setDimensionValue(value === undefined ? '' : JSON.stringify(value)); setAfter('');
        }} />}
      </div>}
      <details open={settings || undefined} className="space-y-3"><summary>{t('a2.care.maintenance')}</summary>
        <CatalogClasses config={active.data?.config} revision={active.data?.revision ?? 0} catalogs={catalogs.data ?? []} />
        <CatalogInspection catalog={viewCatalog} />
      <CatalogScopeRepair activeRevision={active.data?.revision ?? 0} />
      {viewCatalog && <details><summary>{t('alpha10.technical')}</summary><p>{viewCatalog.catalog_id} · {viewCatalog.version}</p></details>}
      {viewCatalog && <CatalogCleanup key={`cleanup-${viewCatalog.catalog_id}@${viewCatalog.version}`} catalog={viewCatalog} />}
      </details>
      {view === 'history' ? history.isPending ? <Spinner /> : history.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : <><ul className="divide-y divide-line">{history.data?.map(row => <li key={row.revision} className="py-3"><p>{t('alpha10.catalog.revision', { n: row.revision })} · {formatTime(row.at)}</p><p>{row.reason}</p><p className="text-sm text-muted">{t('alpha10.catalog.affected', { n: row.affected.length })}</p><Button onClick={() => { setPast(row.revision); setPastAfter(''); setExpected(row.head_revision); setExpectedActive(row.active_revision); setRestoringId(uuid()); setError(''); }}>{t('alpha10.catalog.historyOpen')}</Button></li>)}</ul><div className="flex gap-3"><Button disabled={!after} onClick={() => setAfter('')}>{t('alpha10.catalog.first')}</Button><Button disabled={history.data?.length !== 100} onClick={() => setAfter(String(history.data!.at(-1)!.revision))}>{t('alpha10.catalog.older')}</Button></div></> : entries.isPending ? <Spinner /> : entries.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : <>
        {entries.data?.items.length === 0 ? <Empty title={t('alpha10.catalog.empty')}>{t('alpha10.catalog.emptyHelp')}</Empty> : <CatalogTable key={selectionScope} catalogs={viewCatalog ? [viewCatalog] : catalogs.data ?? []} rows={tableRows} selected={selected} setSelected={setSelected} onEdit={open} onDetails={(entry, owner) => { setDetailsCatalog(owner); setDetails(entry); }} parameters={parameters.toString()} all={!viewCatalog} expand={view === 'masters' || structure === 'masters'} onClassSort={dimension => { setSort(dimension ? `class:${dimension}` : 'classification'); setAfter(''); }} />}
        <div className="flex gap-3"><Button disabled={!after} onClick={() => setAfter('')}>{t('alpha10.catalog.first')}</Button><Button disabled={!entries.data?.next_cursor} onClick={() => setAfter(entries.data?.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button></div>
      </>}
    </Panel>}
    {detailsCatalog && <CatalogDetail catalog={detailsCatalog} entry={details} onClose={() => setDetails(null)} onOpen={setDetails} />}
    {viewCatalog && asArray(active.data?.config?.catalogs).map(asObject).some(row => row.catalog_id === viewCatalog.catalog_id && row.version === viewCatalog.version && row.kind === 'managed') &&
      <CatalogVersions key={viewCatalog.catalog_id + '@' + viewCatalog.version} id={viewCatalog.catalog_id} version={viewCatalog.version} />}
    {viewCatalog?.classification_schema.legend && <Panel className="mt-4 p-5"><h2>{t('b15.legend')}</h2><Link to={`/sources/catalogs?catalog_id=${encodeURIComponent(viewCatalog.catalog_id)}`}>{t('b17.editLegend')}</Link><p>{t('b17.legendVersioned')}</p><dl>{Object.entries(viewCatalog.classification_schema.legend).map(([key, item]) => <div key={key} className="py-2"><dt className="font-medium">{item.display_name}</dt><dd>{item.description}</dd></div>)}</dl></Panel>}
    <Dialog open={!!editing} onOpenChange={open => !open && editGuard.requestClose()} title={t('alpha10.catalog.edit')} footer={<><Button disabled={busy} onClick={() => editGuard.requestClose()}>{t('common.cancel')}</Button><Button disabled={busy || (mode === 'variant' && !master)} onClick={() => void inspectImpact()}>{t('alpha10.catalog.preview')}</Button><Button variant={removingEntry ? "danger" : "primary"} disabled={busy || !impactCurrent || (mode === 'variant' && !master)} onClick={() => setConfirmSave(true)}>{t(removingEntry ? 'b26.catalog.delete' : 'alpha10.catalog.save')}</Button></>}>
      <EffectStrip effect="catalog" />
      <div className="space-y-4"><p className="font-medium">{editing ? titleLabel(editing) : ''}</p>{impactCurrent && impact && <Notice tone="info">{t('alpha10.catalog.impact', { keys: impact.affected_keys, contracts: impact.affected_contracts })}{impact.changes && <ul aria-label={t('a2.care.changedEntries')}>{impact.changes.map(row => <li key={row.key}>{titleLabel(row)}{row.master_key && ' → ' + titleLabel({ key: row.master_key })}</li>)}</ul>}</Notice>}
        <Field label={t('b22.object.name')}>{p => <Input {...p} maxLength={1024} disabled={mode === 'promote'} value={displayName} onChange={event => setDisplayName(event.target.value)} />}</Field>
        <Button variant="danger" disabled={busy} onClick={() => { setRemovingEntry(!removingEntry); setMode(editing?.master_key ? 'variant' : 'independent'); }}><Trash2 size={16} aria-hidden="true" />{t(removingEntry ? 'b26.catalog.cancelDelete' : 'b26.catalog.delete')}</Button>
        {removingEntry && <Notice tone="warn">{t('b26.catalog.deleteHelp')}</Notice>}
        <Field label={t('alpha10.catalog.assignment')}>{p => <Select {...p} value={mode} disabled={removingEntry} onChange={e => { setMode(e.target.value); if (e.target.value === 'promote') { setDisplayName(editing?.display_name ?? ''); setNote(editing?.note ?? ''); setRemovingEntry(false); } }}><option value="independent">{t('alpha10.catalog.independent')}</option><option value="variant">{t('alpha10.catalog.variant')}</option>{editing?.master_identity && <option value="promote">{t('b26.catalog.promote')}</option>}</Select>}</Field>
        {mode !== 'variant' ? catalog?.class_definitions?.length
          ? <ClassFields definitions={catalog.class_definitions} values={classValues} onChange={values => { setClassValues(values); setClassConfirmed(false); }} disabled={removingEntry} />
          : <Field label={t('alpha10.catalog.class')}>{p => <Select {...p} value={classification} onChange={e => { setClassification(e.target.value); setClassConfirmed(false); }}>{catalog?.classification_schema.values.map(value => <option key={value} value={value}>{catalog.classification_schema.legend?.[value]?.display_name ?? value}</option>)}</Select>}</Field> : <><Field label={t('alpha10.catalog.masterSearch')}>{p => <Input {...p} value={masterQuery} onChange={e => setMasterQuery(e.target.value)} />}</Field><Field label={t('alpha10.catalog.master')}>{p => <Select {...p} value={master} onChange={e => setMaster(e.target.value)}><option value="">{t('alpha10.catalog.chooseMaster')}</option>{master && !masterMatches.some(row => row.key === master) && <option value={master}>{titleLabel({ key: master })}</option>}
          {masterMatches.map(row => <option key={row.identity} value={row.key}>{titleLabel(row)}</option>)}</Select>}</Field><div aria-live="polite">{masters.isPending ? <Spinner /> : masters.isError ? <Notice tone="bad">{describeError(masters.error)}<Button onClick={() => void masters.refetch()}>{t('a2.care.reload')}</Button></Notice> : masterMatches.length ? <ul aria-label={t('a2.care.masterMatches')}>{masterMatches.map(row => <li key={row.identity}><Button variant="ghost" onClick={() => setMaster(row.key)}>{titleLabel(row)}</Button></li>)}</ul> : <p className="text-sm text-muted">{t('a2.care.noMasterMatches')}</p>}</div></>}
        {mode === 'independent' && !catalog?.class_definitions?.length && catalog?.classification_schema.legend?.[classification] && <p>{catalog.classification_schema.legend[classification].description}</p>}
        <Field label={t('alpha10.catalog.note')}>{p => <Textarea {...p} disabled={mode === 'promote'} value={note} maxLength={1024} onChange={e => setNote(e.target.value)} />}</Field>
        <details><summary>{t('alpha10.technical')}</summary><p>{t('alpha10.catalog.revision', { n: expected })}</p><code className="break-all text-xs">{editing?.semantic_digest}</code></details>
        {comparison && <Notice tone="info">{comparison}</Notice>}
        {error && <Notice tone="bad">{error}{errorCode === 'catalog_confirm_class_change' ? <Button onClick={() => { setClassConfirmed(true); setError(''); setErrorCode(''); }}>{t('a2.care.confirmClass')}</Button> : <><p>{t('alpha10.catalog.conflictHelp')}</p><Button onClick={async () => { if (!catalog || !editing) return; try { const current = await api<{ content_revision: number; active_revision: number; effective_classification: string; entry: { master_key?: string } | null }>(`catalogs/entry?${new URLSearchParams({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: editing.identity })}`); setExpected(current.content_revision); setExpectedActive(current.active_revision); setComparison(t('alpha10.catalog.comparison', { classification: current.effective_classification, master: current.entry?.master_key ? titleLabel({ key: current.entry.master_key }) : t('alpha10.catalog.independent') })); setRequestId(uuid()); setError(''); } catch (failure) { setError(describeError(failure)); } }}>{t('alpha10.catalog.reload')}</Button></>}</Notice>}
      </div>
    </Dialog>
    <ConfirmDialog open={!!editing && confirmSave} onOpenChange={open => !open && !busy && setConfirmSave(false)} title={t(removingEntry ? 'b26.catalog.delete' : 'alpha10.catalog.save')} description={<><p>{removingEntry ? t('b26.catalog.deleteConfirm', { name: editing ? titleLabel(editing) : '' }) : mode === 'promote' ? t('b26.catalog.promoteConfirm', { old: editing?.master_key ? titleLabel({ key: editing.master_key }) : '', next: editing ? titleLabel(editing) : '', classification: catalog?.classification_schema.legend?.[classification]?.display_name ?? classification }) : t(displayName !== editing?.display_name ? 'b26.catalog.renameConfirm' : 'b26.catalog.saveConfirm', { old: editing ? titleLabel(editing) : '', next: displayName || (editing ? titleLabel({ ...editing, display_name: '' }) : '') })}</p><p>{t('alpha10.catalog.impact', { keys: impact?.affected_keys ?? 0, contracts: impact?.affected_contracts ?? 0 })}</p><EffectStrip effect="catalog" /></>} tone={removingEntry ? 'danger' : 'primary'} busy={busy} confirmDisabled={!impactCurrent} confirmLabel={t(removingEntry ? 'b26.catalog.delete' : 'alpha10.catalog.save')} onConfirm={() => void save()} />
    <Dialog open={past !== null} onOpenChange={open => !open && restoreGuard.requestClose()} title={t('alpha10.catalog.revision', { n: past ?? 0 })} footer={<><Button disabled={busy} onClick={() => restoreGuard.requestClose()}>{t('common.cancel')}</Button><Button disabled={busy || pastContent.isPending || pastContent.isError} onClick={() => void inspectImpact(true)}>{t('alpha10.catalog.preview')}</Button><Button variant="primary" disabled={busy || !impactCurrent || pastContent.isPending || pastContent.isError} onClick={() => void restore()}>{t('alpha10.catalog.restore')}</Button></>}>
      <EffectStrip effect="catalog" />
      <Notice tone="warn">{t('alpha10.catalog.restoreHelp')}</Notice>{impactCurrent && impact && <Notice tone="info">{t('alpha10.catalog.impact', { keys: impact.affected_keys, contracts: impact.affected_contracts })}</Notice>}
      {pastContent.isPending ? <Spinner /> : pastContent.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : <ul className="divide-y divide-line">{pastContent.data?.items.map(row => <li key={row.identity} className="py-2">{titleLabel(row)} · {row.tombstone ? t('alpha10.catalog.removed') : row.master_key ? titleLabel({ key: row.master_key }) : row.classification}</li>)}</ul>}
      {pastContent.data?.next_cursor && <Button onClick={() => setPastAfter(pastContent.data!.next_cursor!)}>{t('alpha10.catalog.next')}</Button>}
      {error && <Notice tone="bad">{error}</Notice>}
    </Dialog>
    {editGuard.confirmation}
    {restoreGuard.confirmation}
  </>;
}
