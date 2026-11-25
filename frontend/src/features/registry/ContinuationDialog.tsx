import { t as uiText } from '@/i18n';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Notice } from '@/components/ui/layout';
import { api, describeError } from '@/lib/api';
import type { Draft, Registry } from '@/lib/types';
import { freshnessChoiceLabel } from '@/features/sources/freshness';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { CONTINUE } from './authoring';
import { fieldLabel, codeLabel } from '@/i18n/codes';

interface Conflict { path: string; kind: string; base: unknown; draft: unknown; current: unknown; digests: string[]; presence?: Record<string, { present: boolean; known: boolean }> }
interface Session { config?: Registry; session_id: string; session_version: number; status: string; conflicts: Conflict[]; decisions: Record<string, { digests: string[]; choice: string }>; expected_active_revision: number; result?: Draft }
function readable(value: unknown, config: Registry = {}): string {
  const freshness = freshnessChoiceLabel(config, value);
  if (freshness) return freshness;
  if (value === true) return uiText('beta3.cde9e58a9a4e');
  if (value === false) return uiText('beta3.90ebc1bde6f3');
  if (value === null) return uiText('beta3.6a4c4c7e8f60');
  if (typeof value === 'object') return Object.entries(value ?? {}).map(([key, item]) => `${fieldLabel(key)}: ${readable(item, config)}`).join(' · ');
  return String(value);
}

export function ContinuationDialog({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const [session, setSession] = useState<Session | null>(null);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const doc = useWorkingDocument();
  const navigate = useNavigate();
  const client = useQueryClient();
  useEffect(() => {
    let cancelled = false;
    const key = `wuschel.continuation.${draft.draft_id}`;
    const saved = sessionStorage.getItem(key) ?? draft.continuation_session_id;
    const load = saved ? api<Session>(`registry/continuations/${saved}`) : api<Session>(`registry/drafts/${encodeURIComponent(draft.draft_id)}/continuations`, { method: 'POST', body: { draft_version: draft.draft_version, manual_comparison: draft.base_status === 'needs_review' } });
    load.then(value => { if (!cancelled) { if (value.status === 'completed' && value.result) { sessionStorage.removeItem(key); doc.open(value.result, { reload: true }); onClose(); navigate(`/changes/drafts/${value.result.draft_id}`); } else { setSession(value); sessionStorage.setItem(key, value.session_id); } } }).catch(failure => !cancelled && setError(describeError(failure)));
    return () => { cancelled = true; };
  }, [draft.draft_id, draft.draft_version, draft.base_status]);
  const run = async () => {
    if (!session) return;
    setBusy(true); setError('');
    try {
      if (session.conflicts.length) {
        const decisions = Object.fromEntries(session.conflicts.map(c => [c.path, { digests: c.digests, choice: choices[c.path] }]));
        const next = await api<Session>(`registry/continuations/${session.session_id}/decisions`, { method: 'PUT', interactive: true, body: { session_version: session.session_version, decisions } });
        setSession(next); setChoices({});
      } else {
        const next = await api<Draft | Session>(`registry/continuations/${session.session_id}/finish`, { method: 'POST', interactive: true, body: { session_version: session.session_version, request_id: session.session_id } });
        if (!('session_id' in next)) { sessionStorage.removeItem(`wuschel.continuation.${draft.draft_id}`); doc.open(next, { reload: true }); await client.invalidateQueries({ queryKey: ['registry'] }); onClose(); navigate(`/changes/drafts/${next.draft_id}`); requestAnimationFrame(() => { const title = document.querySelector<HTMLElement>('h1'); if (title) { title.tabIndex = -1; title.focus(); } }); }
        else { setSession(next); setChoices({}); }
      }
    } catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  const cancel = async () => {
    if (!session) { onClose(); return; }
    setBusy(true); setError('');
    try { await api(`registry/continuations/${session.session_id}/cancel`, { method: 'POST', body: { session_version: session.session_version } }); sessionStorage.removeItem(`wuschel.continuation.${draft.draft_id}`); onClose(); }
    catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  const restart = async () => {
    setBusy(true); setError('');
    try { const next = await api<Session>(`registry/drafts/${encodeURIComponent(draft.draft_id)}/continuations`, { method: 'POST', body: { draft_version: draft.draft_version, manual_comparison: draft.base_status === 'needs_review' } }); setSession(next); setChoices({}); sessionStorage.setItem(`wuschel.continuation.${draft.draft_id}`, next.session_id); }
    catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  return <Dialog open onOpenChange={open => !open && onClose()} size="lg" title={uiText('beta3.6d69805a5b5b')} description={uiText('beta3.b6fa012b95d5')} footer={<><Button disabled={busy} onClick={onClose}>{uiText('beta3.61e4f2b44f07')}</Button><Button disabled={busy} onClick={() => void cancel()}>{uiText('beta3.f7ff1178af20')}</Button>{session && ['halted', 'cancelled'].includes(session.status) ? <Button variant="primary" disabled={busy} onClick={() => void restart()}>{uiText('b3.newComparison')}</Button> : <Button variant="primary" disabled={busy || !session || session.status !== 'open' || session.conflicts.some(c => !choices[c.path])} onClick={() => void run()}>{session?.conflicts.length ? uiText('beta3.94ef93714dd0') : uiText('beta3.2f092699c61d')}</Button>}</>}>
    <p>{CONTINUE}</p><p>{uiText('beta3.b7bd3fab0daa')}</p>
    {error && <Notice tone="bad" title={uiText('beta3.630c538a1b8f')}>{error}</Notice>}
    {session?.status === 'halted' && <Notice tone="warn" title={uiText('beta3.1b03130f98c3')}>{uiText('beta3.d34be521e102')}</Notice>}
    {session?.conflicts.length === 0 && <Notice tone="ok" title={uiText('beta3.c01243975e7b')}>{uiText('beta3.a8295234e6c8')}</Notice>}
    {session?.conflicts.map(conflict => { const parts = conflict.path.split('/'); const value = (side: 'base' | 'draft' | 'current') => conflict.presence?.[side]?.known === false ? uiText('b3.baseUnproven') : conflict.presence?.[side]?.present === false ? uiText('beta3.5728b7b1e370') : ['mode', 'source_type', 'kind', 'target'].includes(parts.at(-1) ?? '') ? codeLabel(conflict[side]) : readable(conflict[side], session.config); return <fieldset key={conflict.path} className="my-4 min-w-0 rounded border border-line p-3"><legend className="break-words">{parts.at(-1) === 'freshness_selection' ? uiText('b4.title') : fieldLabel(parts.at(-1) ?? '')} · {parts[2]}</legend><p>{uiText('beta3.e5c95de28a9b')}{value('base')}</p><label className="flex min-h-11 items-center gap-2"><input type="radio" name={conflict.path} checked={choices[conflict.path] === 'draft'} onChange={() => setChoices({ ...choices, [conflict.path]: 'draft' })} />{uiText('beta3.023bc0649c13')}{value('draft')}</label><label className="flex min-h-11 items-center gap-2"><input type="radio" name={conflict.path} checked={choices[conflict.path] === 'current'} onChange={() => setChoices({ ...choices, [conflict.path]: 'current' })} />{uiText('beta3.fe0b33670e6b')}{value('current')}</label><details><summary>{uiText('alpha10.technical')}</summary><code>{conflict.path}</code></details></fieldset>; })}
  </Dialog>;
}
