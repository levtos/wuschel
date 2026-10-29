import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { codeLabel } from '@/i18n/codes';
import { api, describeError } from '@/lib/api';
import { useActiveRevision } from '@/lib/queries';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';

interface Target { catalog_id: string; definition_version: number; scope: string; profile: string }
interface Mapping { mappings: Record<string, Target>; standalone: string[] }
interface Preview {
  plan_id: string; plan_digest: string; at: string; counts: Record<string, number>;
  changed_targets: number; affected_contracts: string[]; context_effects: { contract_id?: string; storage?: string; slot?: string }[];
  can_apply: boolean; mapping: Mapping; mapping_version: number;
  contexts: { key: string; context: string; source_app: string }[];
  errors: { row: number; context?: string; key?: string; standalone_key?: string; reasons: string[] }[];
}
interface Difference { cursor: string; catalog_id: string; before: Record<string, unknown> | null; after: Record<string, unknown> }
export interface SyncCatalog { catalog_id: string; version: number }

export function TitleSync({ catalogs }: { catalogs: SyncCatalog[] }) {
  const active = useActiveRevision(); const cache = useQueryClient();
  const [open, setOpen] = useState(false); const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null); const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const [diff, setDiff] = useState<{ items: Difference[]; next_cursor: string | null } | null>(null);
  const requestId = useRef(crypto.randomUUID()); const sequence = useRef(0);
  const expected = useRef(0);
  const read = async (edit = false) => {
    const serial = ++sequence.current; setBusy(true); setError(''); setMessage(''); setOpen(true);
    const revision = active.data?.revision ?? 0; expected.current = revision;
    try {
      const result = await api<Preview>('title-sync/preview', { method: 'POST', body: {
        expected_active_revision: revision,
        ...(edit && mapping ? { mapping, expected_mapping_version: preview?.mapping_version ?? 0 } : {}),
      } });
      if (serial !== sequence.current) return;
      setPreview(result); setMapping(result.mapping); setDirty(false); setDiff(null); requestId.current = crypto.randomUUID();
    } catch (failure) { if (serial === sequence.current) { setError(describeError(failure)); setPreview(null); } }
    finally { if (serial === sequence.current) setBusy(false); }
  };
  const apply = async () => {
    if (!preview || dirty || !preview.can_apply || expected.current !== active.data?.revision) return;
    setBusy(true); setError('');
    try {
      const result = await api<{ changed: number }>('title-sync/apply', { method: 'POST', body: {
        plan_id: preview.plan_id, plan_digest: preview.plan_digest, request_id: requestId.current,
      } });
      setMessage(t(result.changed ? 'b17.synced' : 'b17.syncUnchanged')); setPreview(null);
      await cache.invalidateQueries({ queryKey: ['catalog-entries'] });
      await cache.invalidateQueries({ queryKey: ['catalog-history'] });
    } catch (failure) {
      // Same transport ID is retained: the server returns its committed receipt after a lost response.
      setError(describeError(failure)); void cache.invalidateQueries({ queryKey: ['registry', 'active'] });
    } finally { setBusy(false); }
  };
  const update = (key: string, patch: Partial<Target>) => {
    const value = mapping ?? { mappings: {}, standalone: [] };
    setMapping({ ...value, mappings: { ...value.mappings, [key]: { catalog_id: '', definition_version: 1, scope: 'default', profile: 'music', ...value.mappings[key], ...patch } } }); setDirty(true);
  };
  const differences = async (after = '') => {
    if (!preview) return; setBusy(true); setError('');
    try { setDiff(await api(`title-sync/diff?${new URLSearchParams({ plan_id: preview.plan_id, after })}`)); }
    catch (failure) { setError(describeError(failure)); } finally { setBusy(false); }
  };
  return <>
    <Button variant="primary" disabled={busy || !catalogs.length || active.data === undefined} onClick={() => void read()}>{t('b17.sync')}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) { setOpen(value); ++sequence.current; } }} title={t('b17.sync')} footer={<>
      <Button disabled={busy} onClick={() => void read(dirty)}>{t(dirty ? 'b17.checkMapping' : 'b17.syncAgain')}</Button>
      <Button variant="primary" disabled={busy || dirty || !preview?.can_apply || expected.current !== active.data?.revision} onClick={() => void apply()}>{t('b17.syncApply')}</Button>
    </>}>
      <div className="space-y-4">
        <p>{t('b17.syncDescription')}</p>
        {busy && <p role="status">{t('b17.loading')}</p>}
        {error && <Notice tone="bad">{error}</Notice>}{message && <Notice tone="ok">{message}</Notice>}
        {preview && <>
          <p>{t('b17.sourceAt', { at: new Date(preview.at).toLocaleString('de-DE') })}</p>
          <dl>{['new', 'changed', 'unchanged', 'unmappable'].map(key => <div key={key}><dt>{t(`b17.diff.${key}`)}</dt><dd>{preview.counts[key] ?? 0}</dd></div>)}</dl>
          <Notice tone="warn">{t('b17.tcWins')}</Notice>
          <p>{t('b17.effects', { contracts: preview.affected_contracts.length, contexts: preview.context_effects.length })}</p>
          <ul>{preview.context_effects.map((effect, index) => <li key={index}>{effect.contract_id} · {effect.storage} · {effect.slot}</li>)}</ul>
          <ul>{preview.errors.map((finding, index) => <li key={index}>
            {finding.key ?? finding.row} · {finding.context} · {finding.reasons.map(codeLabel).join(', ')}
            {finding.standalone_key && finding.reasons.includes('title_sync_variant_override') && <Button onClick={() => {
              if (!mapping) return; setMapping({ ...mapping, standalone: [...mapping.standalone, finding.standalone_key!] }); setDirty(true);
            }}>{t('b17.standalone')}</Button>}
          </li>)}</ul>
          <details open={!preview.can_apply || dirty}><summary>{t('b17.mapping')}</summary>
            <p>{t('b17.mappingHint')}</p>
            {preview.contexts.map(context => { const target = mapping?.mappings[context.key]; return <fieldset key={context.key} className="space-y-2 border border-line p-3">
              <legend>{context.context || t('b15.withoutContext')} · {context.source_app}</legend>
              <Field label={t('b15.device')}>{p => <Select {...p} value={target?.catalog_id ?? ''} onChange={e => update(context.key, { catalog_id: e.target.value, definition_version: catalogs.find(c => c.catalog_id === e.target.value)?.version ?? 1 })}>
                <option value="">{t('b4.choose')}</option>{catalogs.map(c => <option key={c.catalog_id} value={c.catalog_id}>{c.catalog_id}</option>)}
              </Select>}</Field>
              <Field label={t('b15.profile')}>{p => <Select {...p} value={target?.profile ?? 'music'} onChange={e => update(context.key, { profile: e.target.value })}>{[['music', 'b15.music'], ['game', 'b15.games'], ['private', 'b15.private']].map(([profile, label]) => <option key={profile} value={profile}>{t(label!)}</option>)}</Select>}</Field>
              <Field label={t('alpha10.catalog.field.scope')}>{p => <Input {...p} value={target?.scope ?? 'default'} onChange={e => update(context.key, { scope: e.target.value })} />}</Field>
            </fieldset>; })}
          </details>
          <Button disabled={busy} onClick={() => void differences()}>{t('b17.showDiff')}</Button>
           {diff && <><ul>{diff.items.map(item => <li key={item.cursor} className="space-y-2 border-b border-line py-3"><p>{item.catalog_id} · {String(item.after.display_name ?? item.after.key)}</p><dl>{['classification', 'master_key', 'reviewed_at', 'hidden_at', 'tc_metadata'].map(field => {
             const display = (row: Record<string, unknown> | null) => { const value = row?.[field]; if (field !== 'tc_metadata') return value == null ? '—' : String(value); const meta = value as { artist?: string; title?: string; telemetry?: { seen_count: number; first_seen: string; last_seen: string } } | undefined; return meta ? [meta.artist, meta.title, meta.telemetry?.seen_count, meta.telemetry?.first_seen, meta.telemetry?.last_seen].filter(value => value != null).join(' · ') : '—'; };
             return <div key={field}><dt>{t(`b17.field.${field}`)}</dt><dd>{display(item.before)} → {display(item.after)}</dd></div>;
           })}</dl></li>)}</ul>
            <Button disabled={busy || !diff.next_cursor} onClick={() => void differences(diff.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button></>}
        </>}
      </div>
    </Dialog>
  </>;
}
