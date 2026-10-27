import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Field, FileInput, Input, Select } from '@/components/ui/form';
import { Dialog } from '@/components/ui/dialog';
import { Notice, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { downloadText } from '@/features/authoring/importExport';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { api, apiText, describeError } from '@/lib/api';
import { useActiveExport, useActiveRevision } from '@/lib/queries';
import type { Draft } from '@/lib/types';
import type { Registry } from '@/lib/types';
import { FreshnessAssignment, type FreshnessChoices } from '@/features/sources/FreshnessAssignment';
import { asArray } from '@/features/authoring/model';
import { isObject } from '@/lib/json';
import { currentDraft } from './authoring';
import { importFreshnessSources } from '@/features/sources/freshness';

interface Preview { config: Registry; display_name_changes?: { id: string; before: string; after: string }[]; freshness_changed_sources?: string[]; catalog_entries?: number; editorial_entries?: number; plan_digest: string; added: number; reused: number; conflicts: string[]; unresolved: string[]; editorial_versions: Record<string, number>; editorial_changed: Record<string, boolean>; package: { config: Registry; editorial: { object: { kind: string; id: string }; category: string; room: { name: string } }[] } }
type Resolution = 'keep' | 'file' | { new_id: string };

export default function ImportExportPage() {
  const active = useActiveRevision(); const exported = useActiveExport(); const doc = useWorkingDocument(); const cache = useQueryClient(); const navigate = useNavigate();
  const [fileText, setFileText] = useState(''); const [filename, setFilename] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null); const [resolutions, setResolutions] = useState<Record<string, Resolution>>({});
  const [editorialChoices, setEditorialChoices] = useState<Record<string, string>>({}); const [error, setError] = useState('');
  const [busy, setBusy] = useState(false); const [backingUp, setBackingUp] = useState(false); const [scope, setScope] = useState('active');
  const [requestId, setRequestId] = useState(''); const [stale, setStale] = useState(false);
  const [freshnessChoices, setFreshnessChoices] = useState<FreshnessChoices>({});
  const [checking, setChecking] = useState(false);
  const [checkedKey, setCheckedKey] = useState('');
  const requestSequence = useRef(0);
  const isContract = (() => { try { return JSON.parse(fileText).scope === 'contract'; } catch { return false; } })();
  const targetUnavailable = isContract && (!doc.draft || !currentDraft(doc.draft, active.data?.revision ?? 0) || doc.dirty);
  const body = () => ({ package_text: fileText, resolutions, editorial_choices: editorialChoices, freshness_choices: freshnessChoices, ...(isContract && doc.draft ? { target_draft_id: doc.draft.draft_id, target_draft_version: doc.draft.draft_version } : {}) });
  const bodyText = JSON.stringify(body());
  const previewKey = JSON.stringify([bodyText, active.data?.revision, targetUnavailable]);
  const previewOutdated = stale || (!!preview && checkedKey !== previewKey);
  const latestKey = useRef(previewKey); latestKey.current = previewKey;
  const check = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setChecking(true); setError('');
    try {
      const result = await api<Preview>('portable/preview', { method: 'POST', body: JSON.parse(bodyText) });
      if (sequence !== requestSequence.current) return;
      if (latestKey.current !== previewKey) {
        // Keep a stale first result visible so the existing recheck effect runs.
        // Its old key and stale flag still prohibit staging until acknowledgement.
        setPreview(result); setCheckedKey(previewKey); setStale(true); return;
      }
      setPreview(result); setCheckedKey(previewKey); setRequestId(crypto.randomUUID()); setStale(false);
    } catch (failure) {
      if (sequence === requestSequence.current && latestKey.current === previewKey) setError(describeError(failure));
    } finally { if (sequence === requestSequence.current) setChecking(false); }
  }, [bodyText, previewKey]);
  useEffect(() => {
    if (!preview || !previewOutdated || targetUnavailable) return;
    const timer = window.setTimeout(() => void check(), 300);
    return () => window.clearTimeout(timer);
  }, [check, previewOutdated, targetUnavailable, !!preview]);
  useEffect(() => () => { requestSequence.current++; }, []);
  const assignmentSources = preview ? importFreshnessSources(preview.package.config, isContract ? doc.draft?.config ?? {} : exported.data ?? {}) : [];
  const unchangedCount = preview ? asArray(preview.package.config.sources).filter(isObject).filter(source => source.origin !== 'manual').length - assignmentSources.length : 0;
  const editorialRows = preview?.package.editorial.filter(row => preview.editorial_changed[`${row.object.kind}:${row.object.id}`]) ?? [];
  const unchangedEditorial = preview ? preview.package.editorial.length - editorialRows.length : 0;
  const firstConflictIndex = preview?.conflicts.indexOf(preview.unresolved[0] ?? '') ?? -1;
  const blockedReason = busy ? t('b12.staging') : checking || previewOutdated ? t('b10.previewUpdating') : error || (targetUnavailable ? t('alpha10.transfer.targetSave') : '');
  const save = async () => { if (!preview || previewOutdated || checking || error || targetUnavailable || preview.unresolved.length) return; setBusy(true); setError(''); try {
    const draft = await api<Draft>('portable/stage', { method: 'POST', body: { ...body(), plan_digest: preview.plan_digest, request_id: requestId } });
    await cache.invalidateQueries({ queryKey: ['registry', 'drafts'] }); if (draft.draft_id === doc.draft?.draft_id) doc.open(draft, { reload: true }); else doc.requestOpen(draft); navigate(`/changes/drafts/${encodeURIComponent(draft.draft_id)}`);
  } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  const backup = async () => { setBusy(true); setError(''); try {
    const data = await apiText('portable/export', { method: 'POST', body: { expected_active_revision: active.data?.revision ?? 0,
      ...(scope === 'draft' && doc.draft ? { draft_id: doc.draft.draft_id, draft_version: doc.draft.draft_version } : {}) } });
    downloadText(`wuschel-${new Date().toISOString().slice(0, 10)}.json`, data); setBackingUp(false);
  } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  return <>
    <PageHeader title={t('alpha10.transfer.title')} description={t('alpha10.transfer.description')} actions={<Button variant="primary" disabled={!active.data && !doc.draft} onClick={() => { setScope(active.data ? 'active' : 'draft'); setBackingUp(true); }}>{t('alpha9.actions.backup')}</Button>} />
    <Notice tone="info">{t('alpha10.transfer.history')}</Notice>
    <Panel className="mt-5"><PanelHeader title={t('alpha10.transfer.read')} /><div className="space-y-4 p-5">
      <Field label={t('alpha7.import.file')}>{p => <FileInput {...p} accept="application/json,.json" disabled={busy} onChange={async e => { const file = e.target.files?.[0]; if (!file) return; requestSequence.current++; setChecking(false); setStale(false); setRequestId(''); setPreview(null); setError(''); setResolutions({}); setEditorialChoices({}); setFreshnessChoices({}); setFilename(file.name); if (file.size > 50 * 1024 * 1024) { setError(t('alpha10.transfer.tooLarge')); setFileText(''); return; } setFileText(await file.text()); }} />}</Field>
      <p className="text-sm text-muted">{filename || t('alpha10.transfer.choose')}</p>
      <Button disabled={!fileText || busy || checking || targetUnavailable} onClick={() => void check()}>{t('alpha10.transfer.preview')}</Button>
      {isContract && <Notice tone="info">{doc.draft ? t('alpha10.transfer.target', { name: doc.displayName || doc.draft.draft_id }) : t('alpha10.transfer.targetRequired')}{!doc.draft && <Button onClick={() => void doc.ensureDraft().catch(failure => setError(describeError(failure)))}>{t('alpha10.transfer.createTarget')}</Button>}{doc.dirty && <p>{t('alpha10.transfer.targetSave')}</p>}</Notice>}
      {!preview && error && <Notice tone="bad">{error}</Notice>}
      {preview && <section className="space-y-4" aria-live="polite">
        {preview.conflicts.length > 0 && <section aria-label={t('b12.conflicts', { count: preview.conflicts.length })} className="space-y-2">
          <h2>{t('b12.conflicts', { count: preview.conflicts.length })}</h2>
          {preview.conflicts.map((key, index) => <div key={key} id={`import-conflict-${index}`} tabIndex={-1} className="scroll-mt-4 rounded-lg border border-line p-3"><p className="break-all text-sm">{key}</p><Field label={t('alpha10.transfer.collision')}>{p => <Select {...p} value={resolutions[key] === 'keep' ? 'keep' : resolutions[key] === 'file' ? 'file' : typeof resolutions[key] === 'object' ? 'new' : ''} onChange={e => { setResolutions(previous => { const next = { ...previous }; if (!e.target.value) delete next[key]; else next[key] = e.target.value === 'keep' ? 'keep' : e.target.value === 'file' ? 'file' : { new_id: '' }; return next; }); setError(''); setStale(true); }}><option value="">{t('alpha10.transfer.decide')}</option><option value="keep">{t('alpha10.transfer.keep')}</option>{!isContract && <option value="file">{t('b12.useFile')}</option>}{!key.startsWith('catalog-entry:') && <option value="new">{t('alpha10.transfer.new')}</option>}</Select>}</Field>{typeof resolutions[key] === 'object' && <Field label={t('alpha10.transfer.newId')}>{p => <Input {...p} value={(resolutions[key] as { new_id: string }).new_id} onChange={e => { setResolutions(previous => ({ ...previous, [key]: { new_id: e.target.value } })); setError(''); setStale(true); }} />}</Field>}</div>)}
        </section>}
        <h2>{t('b4.assignImport')}</h2>
        {unchangedCount > 0 && <p>{t('b10.unchangedSources', { count: unchangedCount })}</p>}
        {assignmentSources.length > 0 && <><p>{t('b10.optionalKind')}</p><FreshnessAssignment config={preview.package.config} target={preview.config} sourceIds={assignmentSources.map(source => String(source.source_id))} choices={freshnessChoices} onChange={next => { setFreshnessChoices(next); setError(''); setStale(true); }} /></>}
        {!previewOutdated && preview.freshness_changed_sources && <p>{t(preview.freshness_changed_sources.length === 1 ? 'b4.fileChangeOne' : 'b4.fileChanges', { count: preview.freshness_changed_sources.length })}</p>}
        <p>{t('alpha10.transfer.counts', { added: preview.added, reused: preview.reused, conflicts: preview.conflicts.length })}</p>
        <p>{t('alpha10.transfer.contents', { n: preview.catalog_entries ?? 0, metadata: preview.editorial_entries ?? 0 })}</p>
        {!!preview.display_name_changes?.length && <section aria-label={t('b14.import.names')}><h2>{t('b14.import.names')}</h2><ul>{preview.display_name_changes.map(change => <li key={change.id}>{t('b14.import.rename', { before: change.before || change.id, after: change.after || change.id })}</li>)}</ul></section>}
        <Notice tone="warn">{t('alpha10.transfer.addresses')}</Notice>
        <h2>{t('alpha10.transfer.metadata')}</h2>
        {unchangedEditorial > 0 && <p>{t('b12.unchangedEditorial', { count: unchangedEditorial })}</p>}
        {editorialRows.map(row => { const key = `${row.object.kind}:${row.object.id}`; return <div key={key} className="rounded-lg border border-line p-3"><p>{t(row.object.kind === 'contract' ? 'b12.contract' : 'b12.template', { id: row.object.id })} · {row.category} · {row.room.name}</p>{preview.editorial_versions[key] ? <Field label={t('alpha10.transfer.metadata')}>{p => <Select {...p} value={editorialChoices[key] ?? 'keep'} onChange={e => { setEditorialChoices(previous => ({ ...previous, [key]: e.target.value })); setError(''); setStale(true); }}><option value="keep">{t('alpha10.transfer.keepMetadata')}</option><option value="file">{t('alpha10.transfer.fileMetadata')}</option></Select>}</Field> : <p>{t('alpha10.transfer.newMetadata')}</p>}</div>; })}
        {(preview.package as { legacy?: boolean }).legacy && <Notice tone="info">{t('alpha10.transfer.legacy')}</Notice>}<p className="text-sm text-muted">{t('alpha10.transfer.draftHelp')}</p>
        <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-3 border-t border-line bg-panel p-3">
          <Button disabled={busy || checking || targetUnavailable} onClick={() => void check()}>{t(checking ? 'b12.checking' : 'b12.recheck')}</Button>
          <Button aria-describedby="import-stage-reason" disabled={busy || checking || previewOutdated || !!error || targetUnavailable || preview.unresolved.length > 0} onClick={() => void save()}>{t('alpha10.transfer.stage')}</Button>
          <div id="import-stage-reason" className="text-sm">
            {error ? <Notice tone="bad">{error}<p>{t('b10.previewFailed')}</p></Notice> : blockedReason ? <p role="status">{blockedReason}</p> : preview.unresolved.length > 0 && <a className="underline" href={`#import-conflict-${firstConflictIndex}`} onClick={event => { event.preventDefault(); const first = document.getElementById(`import-conflict-${firstConflictIndex}`); first?.scrollIntoView({ block: 'center' }); first?.focus(); }}>{t('b12.unresolved', { count: preview.unresolved.length })}</a>}
          </div>
        </div>
      </section>}
    </div></Panel>
    <Dialog open={backingUp} onOpenChange={setBackingUp} title={t('alpha9.actions.backup')} footer={<Button variant="primary" disabled={busy || (scope === 'draft' && !!doc.dirty)} onClick={() => void backup()}>{t('alpha9.export.saveFile')}</Button>}>
      <Field label={t('alpha9.export.scope')}>{p => <Select {...p} value={scope} onChange={e => setScope(e.target.value)}><option value="active" disabled={!active.data}>{t('alpha7.export.active')}</option><option value="draft" disabled={!doc.draft}>{t('alpha7.export.draft')}</option></Select>}</Field>
      <p className="mt-3 text-sm">{t('alpha10.transfer.savedOnly')}</p><p className="mt-3 text-sm">{t('alpha10.transfer.history')}</p>
    </Dialog>
  </>;
}
