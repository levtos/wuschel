import { useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Notice } from '@/components/ui/layout';
import { invalidDurations } from '@/components/SecondsField';
import { DiffView } from '@/features/registry/DiffView';
import { IssueList } from '@/features/registry/IssueList';
import type { EditorialEntry } from '@/features/editorial/model';
import { ApiError, describeError } from '@/lib/api';
import { deepEqual } from '@/lib/json';
import { useActiveRevision, validateConfig } from '@/lib/queries';
import type { Draft, Registry, ValidationResult } from '@/lib/types';
import { t } from '@/i18n';
import { useEditorGuard } from './EditorGuard';
import { applyEditorChanges, EditorInputError, SavedDraftAccepted } from './editorChanges';
import { IssuesProvider } from './issues';
import { useWorkingDocument } from './WorkingDocument';
import { emptyRegistry } from './model';

export function useEditorBase(override?: Registry) {
  const doc = useWorkingDocument();
  const active = useActiveRevision();
  const [base] = useState(() => override ?? (doc.editing ? doc.config : null) ?? active.data?.config ?? emptyRegistry());
  return base;
}

export function EffectStrip({ effect }: { effect: 'draft' | 'catalog' | 'restart' }) {
  return <p className="mb-3 rounded border border-line px-3 py-2 text-sm text-muted">{t(`a2.edit.effect.${effect}`)}</p>;
}

export interface EditorCandidate {
  config: Registry;
  editorial?: EditorialEntry[];
}

interface DraftEditorProps {
  title: ReactNode;
  description?: ReactNode;
  size?: 'md' | 'lg' | 'xl';
  base: Registry;
  dirty: boolean;
  /** Changes to this value invalidate the previous check. */
  input: unknown;
  plan: () => EditorCandidate;
  ready?: boolean;
  footer?: ReactNode;
  otherEditors?: (leave: (next: () => void) => void) => ReactNode;
  children: ReactNode;
  onClose: () => void;
  onNavigateAway?: () => void;
  onSaved?: (saved: Draft) => void;
  onApply?: (config: Registry) => void;
}

export function DraftEditor({ title, description, size, base, dirty, input, plan, ready = true, footer, otherEditors, children, onClose, onNavigateAway, onSaved, onApply }: DraftEditorProps) {
  const doc = useWorkingDocument();
  const [action, setAction] = useState<'check' | 'save' | null>(null);
  const [error, setError] = useState('');
  const [check, setCheck] = useState<{ input: unknown; config: Registry; result: ValidationResult } | null>(null);
  const busy = action !== null || doc.starting || doc.saving;
  const guard = useEditorGuard(dirty, onClose, action === 'save', onNavigateAway);
  const result = check && deepEqual(check.input, input) ? check : null;

  const run = async (next: 'check' | 'save') => {
    if (invalidDurations.size) { setError(t('b4.time.precision')); return; }
    setAction(next);
    setError('');
    let submitted: Registry | undefined;
    try {
      const candidate = plan();
      submitted = candidate.config;
      if (next === 'check') {
        try {
          const result = await validateConfig(candidate.config, true);
          setCheck({ input, config: candidate.config, result });
        } catch (failure) {
          if (!(failure instanceof ApiError) || failure.kind !== 'validation') throw failure;
          setCheck({ input, config: candidate.config, result: { valid: false, issues: failure.issues } });
        }
      } else if (onApply) {
        onApply(candidate.config);
        guard.finish();
      } else {
        await doc.ensureDraft();
        const saved = await doc.editAndSave(target => {
          submitted = applyEditorChanges(base, candidate.config, target);
          return submitted;
        }, candidate.editorial);
        guard.finish();
        onSaved?.(saved);
      }
    } catch (failure) {
      if (failure instanceof SavedDraftAccepted) guard.finish();
      else if (submitted && failure instanceof ApiError && failure.kind === 'validation' && failure.issues.length) {
        setCheck({ input, config: submitted, result: { valid: false, issues: failure.issues } });
      } else setError(failure instanceof EditorInputError ? failure.message : describeError(failure));
    }
    finally { setAction(null); }
  };

  return <>
    <Dialog open onOpenChange={shown => !shown && guard.requestClose()} title={title} description={description} size={size} footer={<>
      {action !== 'save' && footer}
      {action !== 'save' && otherEditors?.(guard.requestClose)}
      <Button disabled={action === 'save'} onClick={() => guard.requestClose()}>{t('common.cancel')}</Button>
      <Button disabled={busy || !ready} onClick={() => void run('check')}>{t('a2.edit.check')}</Button>
      <Button variant="primary" disabled={busy || !ready || !dirty} onClick={() => void run('save')}>{t(onApply ? 'a2.edit.apply' : 'a2.edit.save')}</Button>
    </>}>
      <EffectStrip effect="draft" />
      <IssuesProvider value={result?.result.issues ?? []} config={result?.config}><fieldset className="min-w-0" disabled={action === 'save'}>{children}</fieldset></IssuesProvider>
      {action === 'check' && <p role="status">{t('alpha7.draft.checking')}</p>}
      {result && <div className="mt-4 space-y-3">
        {result.result.valid && <Notice tone="ok">{t('alpha7.draft.noErrors')}</Notice>}
        <IssueList issues={[...result.result.issues, ...(result.result.warnings ?? [])]} config={result.config} />
        {result.result.diff && <DiffView diff={result.result.diff} config={result.config} />}
      </div>}
      {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
    </Dialog>
    {guard.confirmation}
  </>;
}
