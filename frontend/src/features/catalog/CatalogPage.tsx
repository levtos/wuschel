import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { hasTranslation, t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { useActiveRevision } from '@/lib/queries';
import { Button } from '@/components/ui/button';
import { Field, Input, Select, Textarea } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { Empty, Notice, PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { TitleSync } from './TitleSync';
import { CatalogCare, CatalogDetail, MasterVariants } from './CatalogCare';

export interface Catalog { catalog_id: string; version: number; classification_schema: { values: string[]; legend?: Record<string, { display_name: string; description: string }> } }
export interface Entry { tombstone?: boolean; identity: string; key: string; scope: string; media_type: string; signal_type: string; classification: string | null; master_key: string | null; display_name: string; note: string; effective_classification: string; complete: boolean; first_seen?: string; last_seen?: string; seen_count?: number; semantic_digest: string; review_status?: string; candidate?: boolean; variant_count?: number; local_sightings?: { seen_count: number }; tc_metadata?: { artist?: string | null; title?: string | null; source_class: number; contexts: { context: string; source_app: string; enum_override?: number | null; telemetry?: { seen_count: number; first_seen: string; last_seen: string } | null }[] } }
interface Page { items: Entry[]; content_revision: number; next_cursor: string | null; contexts?: string[] }

export default function CatalogPage() {
  const cache = useQueryClient();
  const active = useActiveRevision();
  const catalogs = useQuery({ queryKey: ['managed-catalogs'], queryFn: () => api<Catalog[]>('catalogs') });
  const [chosen, setChosen] = useState('');
  const [past, setPast] = useState<number | null>(null); const [pastAfter, setPastAfter] = useState('');
  const [restoringId, setRestoringId] = useState('');
  const [impact, setImpact] = useState<{ affected_keys: number; affected_contracts: number; request: string } | null>(null);
  const catalog = catalogs.data?.find(c => `${c.catalog_id}@${c.version}` === chosen) ?? catalogs.data?.[0];
  const [view, setView] = useState('open'); const [query, setQuery] = useState(''); const [after, setAfter] = useState('');
  const [sort, setSort] = useState('newest'); const [variantsFirst, setVariantsFirst] = useState(false); const [contextFilter, setContextFilter] = useState(''); const [structure, setStructure] = useState('');
  const [selected, setSelected] = useState<Record<string, Entry>>({}); const [details, setDetails] = useState<Entry | null>(null);
  const [editing, setEditing] = useState<Entry | null>(null); const [classification, setClassification] = useState('');
  const [master, setMaster] = useState(''); const [mode, setMode] = useState('independent');
  const [note, setNote] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [expected, setExpected] = useState(0); const [expectedActive, setExpectedActive] = useState(0); const [comparison, setComparison] = useState('');
  const [scopeFilter, setScopeFilter] = useState(''); const [mediaFilter, setMediaFilter] = useState(''); const [signalFilter, setSignalFilter] = useState(''); const [masterQuery, setMasterQuery] = useState(''); const [requestId, setRequestId] = useState('');
  const parameters = new URLSearchParams({ catalog_id: catalog?.catalog_id ?? '', definition_version: String(catalog?.version ?? 1), view: view === 'history' ? 'all' : view, q: query, after, scope: scopeFilter, media_type: mediaFilter, signal_type: signalFilter, context: contextFilter, structure, sort, variants_first: String(variantsFirst) });
  const entries = useQuery({ queryKey: ['catalog-entries', catalog?.catalog_id, catalog?.version, view, query, after, scopeFilter, mediaFilter, signalFilter, contextFilter, structure, sort, variantsFirst], enabled: !!catalog && view !== 'history', queryFn: () => api<Page>(`catalogs/care?${parameters}`) });
  const masters = useQuery({ queryKey: ['catalog-masters', catalog?.catalog_id, masterQuery], enabled: !!catalog && !!editing, queryFn: () => api<Page>(`catalogs/entries?${new URLSearchParams({ catalog_id: catalog!.catalog_id, definition_version: String(catalog!.version), view: 'masters', q: masterQuery })}`) });
  const history = useQuery({ queryKey: ['catalog-history', catalog?.catalog_id, after], enabled: !!catalog && view === 'history', queryFn: () => api<{ revision: number; head_revision: number; active_revision: number; at: string; reason: string; affected: string[] }[]>(`catalogs/history?${parameters}`) });
  const pastContent = useQuery({ queryKey: ['catalog-past', catalog?.catalog_id, past, pastAfter], enabled: !!catalog && past !== null, queryFn: () => api<{ items: Entry[]; total: number; next_cursor: string | null }>(`catalogs/revision?${new URLSearchParams({ catalog_id: catalog!.catalog_id, definition_version: String(catalog!.version), revision: String(past), after: pastAfter })}`) });
  const restoreBody = () => ({ catalog_id: catalog!.catalog_id, definition_version: catalog!.version, restore_revision: past, expected_active_revision: expectedActive, expected_content_revision: expected, request_id: restoringId, reason: t('alpha10.catalog.restoreReason') });
  const restore = async () => { if (!catalog || past === null || !impactCurrent) return; setBusy(true); setError(''); try {
    await api('catalogs/entries', { method: 'POST', body: restoreBody() });
    setPast(null); await cache.invalidateQueries({ queryKey: ['catalog-entries'] }); await cache.invalidateQueries({ queryKey: ['catalog-history'] });
  } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  const open = (entry: Entry) => { setEditing(entry); setClassification(entry.classification ?? catalog?.classification_schema.values[0] ?? ''); setMode(entry.master_key ? 'variant' : 'independent'); setMaster(entry.master_key ?? ''); setNote(entry.note); setExpected(entries.data?.content_revision ?? 0); setExpectedActive(active.data?.revision ?? 0); setComparison(''); setMasterQuery(''); setRequestId(crypto.randomUUID()); setError(''); };
  useEffect(() => { setRequestId(crypto.randomUUID()); setImpact(null); }, [classification, master, mode, note, editing, past, expected, expectedActive]);
  const body = () => ({ catalog_id: catalog!.catalog_id, definition_version: catalog!.version,
        expected_active_revision: expectedActive, expected_content_revision: expected, request_id: requestId,
        reason: t('alpha10.catalog.reason'), changes: [{ scope: editing!.scope, media_type: editing!.media_type, signal_type: editing!.signal_type, key: editing!.key,
          classification: mode === 'variant' ? null : classification, master_key: mode === 'variant' ? master : null, note, display_name: editing!.display_name, tombstone: false }] });
  const currentPayload = catalog ? past !== null ? restoreBody() : editing ? body() : null : null;
  const impactCurrent = !!impact && impact.request === JSON.stringify(currentPayload);
  const inspectImpact = async (restoring = false) => { if (!catalog || (!editing && !restoring)) return; const payload = restoring ? restoreBody() : body(); setBusy(true); setError(''); try { const result = await api<{ affected_keys: number; affected_contracts: number }>('catalogs/preview', { method: 'POST', body: payload }); setImpact({ ...result, request: JSON.stringify(payload) }); } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  const save = async () => {
    if (!editing || !catalog || !impactCurrent) return;
    setBusy(true); setError('');
    try {
      await api('catalogs/entries', { method: 'POST', body: body() });
      setEditing(null); await cache.invalidateQueries({ queryKey: ['catalog-entries'] }); await cache.invalidateQueries({ queryKey: ['catalog-history'] });
    } catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  const label = (kind: string, value: string) => hasTranslation(`alpha10.catalog.${kind}.${value}`) ? t(`alpha10.catalog.${kind}.${value}`) : value;
  return <>
    <PageHeader title={t('alpha10.catalog.title')} description={t('b17.catalogDescription')} actions={<TitleSync catalogs={catalogs.data ?? []} />} />
    {catalogs.isPending ? <Spinner /> : catalogs.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : !catalog ? <Empty title={t('alpha10.catalog.noCatalog')}>{t('alpha10.catalog.defineFirst')}</Empty> : <Panel className="space-y-4 p-5">
      <div className="grid gap-4 md:grid-cols-3">
        <Field label={t('alpha10.catalog.catalog')}>{p => <Select {...p} value={`${catalog.catalog_id}@${catalog.version}`} onChange={e => { setChosen(e.target.value); setAfter(''); setSelected({}); setDetails(null); }}>{catalogs.data?.map(c => <option key={`${c.catalog_id}@${c.version}`} value={`${c.catalog_id}@${c.version}`}>{c.catalog_id} · {c.version}</option>)}</Select>}</Field>
        <Field label={t('alpha10.catalog.view')}>{p => <Select {...p} value={view} onChange={e => { setView(e.target.value); setAfter(''); }}>{['open', 'reviewed', 'hidden'].map(v => <option key={v} value={v}>{t(`b17.status.${v}`)}</option>)}{['unassigned', 'all', 'masters', 'history'].map(v => <option key={v} value={v}>{t(`alpha10.catalog.${v}`)}</option>)}</Select>}</Field>
        <Field label={t('alpha10.catalog.search')}>{p => <Input {...p} value={query} onChange={e => { setQuery(e.target.value); setAfter(''); }} />}</Field>
      </div>
      <div className="grid gap-4 md:grid-cols-3">{[
        ['scope', scopeFilter, setScopeFilter], ['media_type', mediaFilter, setMediaFilter], ['signal_type', signalFilter, setSignalFilter],
      ].map(([name, value, update]) => <Field key={String(name)} label={t(`alpha10.catalog.field.${name}`)}>{p => <Input {...p} value={String(value)} onChange={e => { (update as (value: string) => void)(e.target.value); setAfter(''); }} />}</Field>)}</div>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label={t('b17.sort')}>{p => <Select {...p} value={sort} onChange={event => { setSort(event.target.value); setAfter(''); }}>{['newest', 'oldest', 'title'].map(value => <option key={value} value={value}>{t(`b17.sort.${value}`)}</option>)}</Select>}</Field>
        <Field label={t('b17.contextDevice')}>{p => <Select {...p} value={contextFilter} onChange={event => { setContextFilter(event.target.value); setAfter(''); }}><option value="">{t('b17.allContexts')}</option>{entries.data?.contexts?.map(value => <option key={value} value={value}>{value}</option>)}</Select>}</Field>
        <Field label={t('b17.structure')}>{p => <Select {...p} value={structure} onChange={event => { setStructure(event.target.value); setAfter(''); }}><option value="">{t('alpha10.catalog.all')}</option><option value="masters">{t('alpha10.catalog.masters')}</option><option value="variants">{t('b17.variants')}</option></Select>}</Field>
        <label><input type="checkbox" checked={variantsFirst} onChange={event => { setVariantsFirst(event.target.checked); setAfter(''); }} /> {t('b17.variantsFirst')}</label>
      </div>
      {view !== 'history' && <CatalogCare key={`${catalog.catalog_id}@${catalog.version}`} catalog={catalog} rows={entries.data?.items ?? []} selected={selected} setSelected={setSelected} revision={entries.data?.content_revision ?? 0} activeRevision={active.data?.revision ?? 0} parameters={parameters.toString()} />}
      {view === 'history' ? history.isPending ? <Spinner /> : history.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : <><ul className="divide-y divide-line">{history.data?.map(row => <li key={row.revision} className="py-3"><p>{t('alpha10.catalog.revision', { n: row.revision })} · {new Date(row.at).toLocaleString('de-DE')}</p><p>{row.reason}</p><p className="text-sm text-muted">{t('alpha10.catalog.affected', { n: row.affected.length })}</p><Button onClick={() => { setPast(row.revision); setPastAfter(''); setExpected(row.head_revision); setExpectedActive(row.active_revision); setRestoringId(crypto.randomUUID()); setError(''); }}>{t('alpha10.catalog.historyOpen')}</Button></li>)}</ul><div className="flex gap-3"><Button disabled={!after} onClick={() => setAfter('')}>{t('alpha10.catalog.first')}</Button><Button disabled={history.data?.length !== 100} onClick={() => setAfter(String(history.data!.at(-1)!.revision))}>{t('alpha10.catalog.older')}</Button></div></> : entries.isPending ? <Spinner /> : entries.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : <>
        {entries.data?.items.length === 0 ? <Empty title={t('alpha10.catalog.empty')}>{t('alpha10.catalog.emptyHelp')}</Empty> : <ul className="divide-y divide-line">{entries.data?.items.map(entry => <li key={entry.identity} className="flex flex-wrap items-center justify-between gap-4 py-3"><input type="checkbox" aria-label={t('b17.selectTitle', { title: entry.display_name || entry.key })} checked={!!selected[entry.identity]} onChange={event => setSelected(previous => { const next = { ...previous }; if (event.target.checked) next[entry.identity] = entry; else delete next[entry.identity]; return next; })} /><div className="min-w-0 flex-1"><p className="break-words font-medium">{entry.display_name || entry.key}</p>{entry.candidate && <p>{t('b17.candidate')}</p>}<p className="text-sm text-muted">{entry.scope} · {label('media', entry.media_type)} · {label('signal', entry.signal_type)} · {entry.complete ? catalog.classification_schema.legend?.[entry.effective_classification]?.display_name ?? entry.effective_classification : t('alpha10.catalog.unassigned')}</p>{entry.master_key && <p className="text-sm">{t('alpha10.catalog.master')}: {entry.master_key}</p>}{entry.last_seen && <p className="text-xs text-muted">{t('alpha10.catalog.seen', { n: entry.seen_count ?? 0, date: new Date(entry.last_seen).toLocaleString('de-DE') })}</p>}</div>{(entry.variant_count ?? 0) > 0 && <MasterVariants catalog={catalog} entry={entry} onOpen={setDetails} />}<Button onClick={() => setDetails(entry)}>{t('b17.details')}</Button><Button onClick={() => open(entry)}>{t('alpha10.catalog.edit')}</Button></li>)}</ul>}
        <div className="flex gap-3"><Button disabled={!after} onClick={() => setAfter('')}>{t('alpha10.catalog.first')}</Button><Button disabled={!entries.data?.next_cursor} onClick={() => setAfter(entries.data?.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button></div>
      </>}
    </Panel>}
    {catalog && <CatalogDetail catalog={catalog} entry={details} onClose={() => setDetails(null)} onOpen={setDetails} />}
    {catalog?.classification_schema.legend && <Panel className="mt-4 p-5"><h2>{t('b15.legend')}</h2><Link to={`/sources/catalogs?catalog_id=${encodeURIComponent(catalog.catalog_id)}`}>{t('b17.editLegend')}</Link><p>{t('b17.legendVersioned')}</p><dl>{Object.entries(catalog.classification_schema.legend).map(([key, item]) => <div key={key} className="py-2"><dt className="font-medium">{item.display_name}</dt><dd>{item.description}</dd></div>)}</dl></Panel>}
    <Dialog open={!!editing} onOpenChange={open => !open && setEditing(null)} title={t('alpha10.catalog.edit')} footer={<><Button disabled={busy || (mode === 'variant' && !master)} onClick={() => void inspectImpact()}>{t('alpha10.catalog.preview')}</Button><Button variant="primary" disabled={busy || !impactCurrent || (mode === 'variant' && !master)} onClick={() => void save()}>{t('alpha10.catalog.save')}</Button></>}>
      <div className="space-y-4"><p className="font-medium">{editing?.key}</p><Notice tone="warn">{t('alpha10.catalog.immediate')}</Notice>{impactCurrent && impact && <Notice tone="info">{t('alpha10.catalog.impact', { keys: impact.affected_keys, contracts: impact.affected_contracts })}</Notice>}
        <Field label={t('alpha10.catalog.assignment')}>{p => <Select {...p} value={mode} onChange={e => setMode(e.target.value)}><option value="independent">{t('alpha10.catalog.independent')}</option><option value="variant">{t('alpha10.catalog.variant')}</option></Select>}</Field>
        {mode === 'independent' ? <Field label={t('alpha10.catalog.class')}>{p => <Select {...p} value={classification} onChange={e => setClassification(e.target.value)}>{catalog?.classification_schema.values.map(value => <option key={value} value={value}>{catalog.classification_schema.legend?.[value]?.display_name ?? value}</option>)}</Select>}</Field> : <><Field label={t('alpha10.catalog.masterSearch')}>{p => <Input {...p} value={masterQuery} onChange={e => setMasterQuery(e.target.value)} />}</Field><Field label={t('alpha10.catalog.master')}>{p => <Select {...p} value={master} onChange={e => setMaster(e.target.value)}><option value="">{t('alpha10.catalog.chooseMaster')}</option>{masters.data?.items.filter(row => row.identity !== editing?.identity && row.scope === editing?.scope && row.media_type === editing?.media_type && row.signal_type === editing?.signal_type).map(row => <option key={row.identity} value={row.key}>{row.key}</option>)}</Select>}</Field></>}
        {mode === 'independent' && catalog?.classification_schema.legend?.[classification] && <p>{catalog.classification_schema.legend[classification].description}</p>}
        <Field label={t('alpha10.catalog.note')}>{p => <Textarea {...p} value={note} maxLength={1024} onChange={e => setNote(e.target.value)} />}</Field>
        <details><summary>{t('alpha10.technical')}</summary><p>{t('alpha10.catalog.revision', { n: expected })}</p><code className="break-all text-xs">{editing?.semantic_digest}</code></details>
        {comparison && <Notice tone="info">{comparison}</Notice>}
        {error && <Notice tone="bad">{error}<p>{t('alpha10.catalog.conflictHelp')}</p><Button onClick={async () => { if (!catalog || !editing) return; try { const current = await api<{ content_revision: number; active_revision: number; effective_classification: string; entry: { master_key?: string } | null }>(`catalogs/entry?${new URLSearchParams({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: editing.identity })}`); setExpected(current.content_revision); setExpectedActive(current.active_revision); setComparison(t('alpha10.catalog.comparison', { classification: current.effective_classification, master: current.entry?.master_key ?? t('alpha10.catalog.independent') })); setRequestId(crypto.randomUUID()); setError(''); } catch (failure) { setError(describeError(failure)); } }}>{t('alpha10.catalog.reload')}</Button></Notice>}
      </div>
    </Dialog>
    <Dialog open={past !== null} onOpenChange={open => !open && setPast(null)} title={t('alpha10.catalog.revision', { n: past ?? 0 })} footer={<><Button disabled={busy || pastContent.isPending || pastContent.isError} onClick={() => void inspectImpact(true)}>{t('alpha10.catalog.preview')}</Button><Button variant="primary" disabled={busy || !impactCurrent || pastContent.isPending || pastContent.isError} onClick={() => void restore()}>{t('alpha10.catalog.restore')}</Button></>}>
      <Notice tone="warn">{t('alpha10.catalog.restoreHelp')}</Notice>{impactCurrent && impact && <Notice tone="info">{t('alpha10.catalog.impact', { keys: impact.affected_keys, contracts: impact.affected_contracts })}</Notice>}
      {pastContent.isPending ? <Spinner /> : pastContent.isError ? <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice> : <ul className="divide-y divide-line">{pastContent.data?.items.map(row => <li key={row.identity} className="py-2">{row.key} · {row.tombstone ? t('alpha10.catalog.removed') : row.master_key || row.classification}</li>)}</ul>}
      {pastContent.data?.next_cursor && <Button onClick={() => setPastAfter(pastContent.data!.next_cursor!)}>{t('alpha10.catalog.next')}</Button>}
      {error && <Notice tone="bad">{error}</Notice>}
    </Dialog>
  </>;
}
