import { t as uiText } from '@/i18n';
import { ApiError, api } from '@/lib/api';
import type { Draft, RevisionRow } from '@/lib/types';

export const CONTINUE = uiText('beta3.95163eb65ff9');
export interface ActivationPreview {
  comparison_digest: string;
  expected_active_revision: number;
  no_effect: boolean;
  required_confirmations: string[];
  retention: { before: Record<string, number> | null; after: Record<string, number> | null; changed: boolean; shortening: string[] };
}
export function currentDraft(draft: Draft, active: number | undefined): boolean {
  return (draft.lifecycle ?? 'open') === 'open' && draft.base_status !== 'needs_review' && (active === undefined || draft.base_revision === undefined ? draft.base_current !== false : draft.base_revision === active);
}
export function draftState(draft: Draft, active: number | undefined): string {
  if (draft.lifecycle === 'continued') return uiText('beta3.e5704de26e46');
  if (draft.lifecycle === 'completed') return uiText('beta3.0feb12317e84', { v0: (draft.completed_revision) });
  if (draft.base_status === 'needs_review') return uiText('beta3.c1bcffa28ccd');
  const base = draft.base_revision ? uiText('beta3.9fca6f13850b', { v0: (draft.base_revision) }) : uiText('beta3.57671d86d6b6');
  return currentDraft(draft, active) ? uiText('beta3.1a84056f9b61', { v0: (base) }) : uiText('beta3.7f70b0db8b86', { v0: (base), v1: (active) });
}

/** Keep the exact envelope after an uncertain response; never mint a second command. */
export async function activateWithReceipt(draft: Draft, preview: ActivationPreview, requestId: string, note: string, complete: boolean, onUncertain: () => void): Promise<RevisionRow> {
  const key = `wuschel.activation.${draft.draft_id}`;
  const body = { request_id: requestId, draft_version: draft.draft_version, expected_active_revision: preview.expected_active_revision, comparison_digest: preview.comparison_digest, confirmations: preview.required_confirmations, note };
  const pending = { path: `registry/drafts/${encodeURIComponent(draft.draft_id)}/${complete ? 'complete' : 'activate'}`, body };
  sessionStorage.setItem(key, JSON.stringify(pending));
  try {
    const result = await api<RevisionRow>(pending.path, { method: 'POST', body, interactive: true });
    sessionStorage.removeItem(key);
    return result;
  } catch (error) {
    if (!(error instanceof ApiError) || error.beforeExecution || error.code === 'load_protected' || error.status !== 0 && error.status < 500) { sessionStorage.removeItem(key); throw error; }
    onUncertain();
    const outcome = await api<{ status: string; result?: RevisionRow }>(`registry/activation-requests/${requestId}`);
    if (outcome.status === 'completed' && outcome.result) { sessionStorage.removeItem(key); return outcome.result; }
    throw new Error(uiText('beta3.aa4cd09a5ce7'));
  }
}

export async function recoverActivation(draftId: string): Promise<RevisionRow | null> {
  const key = `wuschel.activation.${draftId}`;
  const raw = sessionStorage.getItem(key);
  if (!raw) return null;
  const pending = JSON.parse(raw) as { path: string; body: { request_id: string } };
  const outcome = await api<{ status: string; result?: RevisionRow }>(`registry/activation-requests/${pending.body.request_id}`);
  if (outcome.status === 'completed' && outcome.result) { sessionStorage.removeItem(key); return outcome.result; }
  // Retry only the identical envelope; the server resolves identity before currentness.
  const result = await api<RevisionRow>(pending.path, { method: 'POST', body: pending.body });
  sessionStorage.removeItem(key);
  return result;
}
