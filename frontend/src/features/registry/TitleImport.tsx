import { useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { useActiveRevision } from '@/lib/queries';
import type { Draft } from '@/lib/types';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { downloadText } from '@/features/authoring/importExport';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, FileInput, Input, Select } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';

interface Catalog { catalog_id: string; version: number }
interface Mapping { catalog_id: string; definition_version: number; scope: string; profile: string }
interface Preview {
  import_id: string; plan_digest?: string; already_applied: boolean;
  contexts: { key: string; context: string; source_app?: string }[];
  report: { input_rows: number; target_entries: number; additional_forms_created: number; additional_forms_skipped: number; error_count: number; outcomes: Record<string, number>; errors: { row: number; key?: string; reasons: string[] }[] };
  unresolved: string[]; conflicts?: string[];
}

export function TitleImport() {
  const [open, setOpen] = useState(false); const [text, setText] = useState('');
  const [mappings, setMappings] = useState<Record<string, Mapping | 'exclude'>>({});
  const [resolutions, setResolutions] = useState<Record<string, string>>({});
  const [excludedRows, setExcludedRows] = useState<number[]>([]);
  const [rowSearch, setRowSearch] = useState(''); const [rowPage, setRowPage] = useState(0);
  // Local display only: the exact source text still goes to the strict server parser.
  const sourceRows = useMemo<{ row: number; key: string }[]>(() => {
    try { const data = JSON.parse(text); return Array.isArray(data?.entries) ? data.entries.map((entry: unknown, index: number) => ({ row: index + 1, key: entry && typeof entry === 'object' && 'key' in entry && typeof entry.key === 'string' ? entry.key : '' })) : []; }
    catch { return []; }
  }, [text]);
  const filteredRows = sourceRows.filter(row => row.key.toLocaleLowerCase('de').includes(rowSearch.toLocaleLowerCase('de')) || String(row.row).includes(rowSearch));
  const [preview, setPreview] = useState<Preview | null>(null);
  const [checked, setChecked] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const requestId = useRef(crypto.randomUUID()); const sequence = useRef(0);
  const active = useActiveRevision(); const doc = useWorkingDocument(); const navigate = useNavigate(); const cache = useQueryClient();
  const catalogs = useQuery({ queryKey: ['managed-catalogs'], queryFn: () => api<Catalog[]>('catalogs'), enabled: open });
  const body = { source_text: text, mappings, excluded_rows: excludedRows, resolutions, expected_active_revision: active.data?.revision ?? 0 };
  const key = JSON.stringify(body); const latest = useRef(key); latest.current = key;
  const current = !!preview && checked === key;
  const check = async () => {
    const token = ++sequence.current; const submitted = key; setBusy(true); setError('');
    try {
      const result = await api<Preview>('title-import/preview', { method: 'POST', body });
      if (token !== sequence.current || latest.current !== submitted) return;
      setPreview(result); setChecked(submitted); requestId.current = crypto.randomUUID();
    } catch (failure) { if (token === sequence.current) { setError(describeError(failure)); setChecked(''); void cache.invalidateQueries({ queryKey: ['registry', 'active'] }); } }
    finally { if (token === sequence.current) setBusy(false); }
  };
  const save = async () => {
    if (!preview || !current || preview.already_applied || preview.unresolved.length) return;
    setBusy(true); setError('');
    try {
      const result = await api<Draft | Preview>('title-import/stage', { method: 'POST', body: { ...body, plan_digest: preview.plan_digest, request_id: requestId.current } });
      if ('already_applied' in result) { setPreview(result); return; }
      await cache.invalidateQueries({ queryKey: ['registry', 'drafts'] }); doc.requestOpen(result);
      setOpen(false); navigate(`/changes/drafts/${encodeURIComponent(result.draft_id)}`);
    } catch (failure) { setError(describeError(failure)); setChecked(''); void cache.invalidateQueries({ queryKey: ['registry', 'active'] }); }
    finally { setBusy(false); }
  };
  return <>
    <Button onClick={() => setOpen(true)}>{t('b15.import')}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value); }} title={t('b15.import')} footer={<>
      <Button disabled={busy || !text} onClick={() => void check()}>{t('b15.check')}</Button>
      <Button variant="primary" disabled={busy || !current || !preview || preview.already_applied || preview.unresolved.length > 0} onClick={() => void save()}>{t('b15.stage')}</Button>
    </>}>
      <div className="space-y-4">
        <p>{t('b15.description')}</p><Notice tone="info">{t('b15.draftOnly')}</Notice>
        <Field label={t('alpha7.import.file')}>{p => <FileInput {...p} accept=".json,application/json" disabled={busy} onChange={async event => {
          const file = event.target.files?.[0]; if (!file) return;
          const token = ++sequence.current; setPreview(null); setChecked(''); setMappings({}); setExcludedRows([]); setRowSearch(''); setRowPage(0); setResolutions({}); setError(''); setText('');
          if (file.size > 50 * 1024 * 1024) { setError(t('alpha10.transfer.tooLarge')); return; }
          const loaded = await file.text(); if (token === sequence.current) setText(loaded);
        }} />}</Field>
        {catalogs.isError && <Notice tone="bad">{t('alpha10.catalog.loadError')}</Notice>}
        {preview?.contexts.map(context => {
          const mapping = mappings[context.key];
          const update = (patch: Partial<Mapping>) => { if (mapping && mapping !== 'exclude') setMappings(previous => ({ ...previous, [context.key]: { ...mapping, ...patch } })); };
          return <fieldset key={context.key} className="space-y-2 rounded-lg border border-line p-3"><legend>{context.context || t('b15.withoutContext')}</legend>
            {context.source_app && <p>{context.source_app}</p>}
            <Field label={t('b15.device')}>{p => <Select {...p} disabled={busy} value={mapping === 'exclude' ? 'exclude' : mapping?.catalog_id ?? ''} onChange={event => {
              const chosen = catalogs.data?.find(item => item.catalog_id === event.target.value);
              setMappings(previous => { const next = { ...previous }; if (event.target.value === 'exclude') next[context.key] = 'exclude'; else if (chosen) next[context.key] = { catalog_id: chosen.catalog_id, definition_version: chosen.version, scope: chosen.catalog_id.split('.').at(-1) ?? chosen.catalog_id, profile: '' }; else delete next[context.key]; return next; });
            }}><option value="">{t('b4.choose')}</option><option value="exclude">{t('b15.exclude')}</option>{catalogs.data?.map(item => <option key={`${item.catalog_id}:${item.version}`} value={item.catalog_id}>{item.catalog_id}</option>)}</Select>}</Field>
            {mapping && mapping !== 'exclude' && <>
              <Field label={t('b15.profile')}>{p => <Select {...p} disabled={busy} value={mapping.profile} onChange={event => update({ profile: event.target.value })}><option value="">{t('b4.choose')}</option><option value="music">{t('b15.music')}</option><option value="game">{t('b15.games')}</option><option value="private">{t('b15.private')}</option></Select>}</Field>
              <Field label={t('alpha10.catalog.field.scope')}>{p => <Input {...p} disabled={busy} value={mapping.scope} onChange={event => update({ scope: event.target.value })} />}</Field>
            </>}
          </fieldset>;
        })}
        {preview && <><p>{t('b15.counts', { rows: preview.report.input_rows, targets: preview.report.target_entries, hidden: preview.report.outcomes.hidden_skipped ?? 0, errors: preview.report.error_count })}</p>
          <p>{t('b15.forms', { created: preview.report.additional_forms_created, skipped: preview.report.additional_forms_skipped })}</p>
          <details><summary>{t('b15.excludeRows')}</summary>
            <p>{t('b15.exclusions', { count: excludedRows.length })}</p>
            <Field label={t('b15.rowSearch')}>{p => <Input {...p} value={rowSearch} onChange={event => { setRowSearch(event.target.value); setRowPage(0); }} />}</Field>
            <ul>{filteredRows.slice(rowPage * 100, (rowPage + 1) * 100).map(row => <li key={row.row} className="py-2"><p>{t('b15.row', { row: row.row })} · {row.key}</p><Field label={t('b15.excludeRow')}>{p => <input {...p} type="checkbox" disabled={busy} checked={excludedRows.includes(row.row)} onChange={event => setExcludedRows(previous => event.target.checked ? [...previous, row.row].sort((a, b) => a - b) : previous.filter(n => n !== row.row))} />}</Field></li>)}</ul>
            <Button disabled={rowPage === 0} onClick={() => setRowPage(page => page - 1)}>{t('b15.previousRows')}</Button>
            <Button disabled={(rowPage + 1) * 100 >= filteredRows.length} onClick={() => setRowPage(page => page + 1)}>{t('b15.nextRows')}</Button>
          </details>
          <Button onClick={() => downloadText('wuschel-titel-pruefbericht.json', JSON.stringify(preview, null, 2))}>{t('b15.report')}</Button>
          {preview.report.error_count > 0 && <Notice tone="warn">{t('b15.errors')}</Notice>}
          {preview.report.errors.map((finding, index) => <fieldset key={`${finding.row}:${index}`} className="space-y-2 rounded-lg border border-line p-3"><legend>{t('b15.row', { row: finding.row })}</legend>
            {finding.key && <p>{finding.key}</p>}
            <p>{t('b15.rowError')}</p>
          </fieldset>)}
          {preview.report.error_count > 100 && <p>{t('b15.moreErrors')}</p>}
          {preview.already_applied && <Notice tone="info">{t('b15.applied')}</Notice>}
          {preview.conflicts?.map(conflict => <Field key={conflict} label={conflict}>{p => <Select {...p} disabled={busy} value={resolutions[conflict] ?? ''} onChange={event => setResolutions(previous => ({ ...previous, [conflict]: event.target.value }))}><option value="">{t('alpha10.transfer.decide')}</option><option value="keep">{t('alpha10.transfer.keep')}</option><option value="file">{t('b12.useFile')}</option></Select>}</Field>)}
          {!current && <p role="status">{t('b15.recheck')}</p>}
        </>}
        {error && <Notice tone="bad">{error}</Notice>}
      </div>
    </Dialog>
  </>;
}
