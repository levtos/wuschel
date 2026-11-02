import { Link } from 'react-router';
import { countSentence } from '@/lib/valueText';
import { draftState } from '@/features/registry/authoring';
import { useActiveRevision } from '@/lib/queries';
import { t as uiText } from '@/i18n';
import { CheckCircle2, CircleAlert, Loader2, MoreHorizontal, Redo2, Save, Undo2 } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog, Dialog } from '@/components/ui/dialog';
import { Notice, Spinner } from '@/components/ui/layout';
import { formatTime } from '@/lib/format';
import { canonicalJson } from '@/lib/json';
import { fieldLabel } from '@/i18n/codes';
import { locateIssue } from './locate';
import { describeError } from '@/lib/api';
import { useDrafts } from '@/lib/queries';
import { changedObjectCount } from '@/lib/diff';
import { ActivateDialog } from '@/features/registry/ActivationDialogs';
import { IssueList } from '@/features/registry/IssueList';
import type { Draft } from '@/lib/types';
import { CreateDraftDialog } from './CreateDraftDialog';
import { useWorkingDocument } from './WorkingDocument';
import { freshnessChoiceLabel } from '@/features/sources/freshness';

export const draftLabel = (draft: Pick<Draft, 'draft_id' | 'display_name'>): string => draft.display_name || uiText('alpha7.draft.unnamed');

/** Choose (or create) the working draft. Opening another draft with unsaved edits asks first. */
export function DraftPicker({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const doc = useWorkingDocument();
  const allDrafts = useDrafts();
  const drafts = { ...allDrafts, data: allDrafts.data?.filter(d => (d.lifecycle ?? 'open') === 'open') };
  const [creating, setCreating] = useState(false);
  const pick = (draft: Draft) => {
    // Re-selecting the open draft is a no-op; another draft asks first if edits would be lost.
    doc.requestOpen(draft);
    onOpenChange(false);
  };
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange} title={uiText("features.authoring.DraftBar.arbeits_draft_wahlen_d0f546")} description={uiText("features.authoring.DraftBar.authoring_bereiche_bearbeiten_immer_genau_einen__3bfed3")} footer={<Button variant="primary" disabled={(drafts.data?.length ?? 0) >= 100} disabledReason={uiText('alpha7.import.limit')} onClick={() => setCreating(true)}>{uiText("features.authoring.DraftBar.neuer_draft_1d7ef0")}</Button>}>
        {drafts.isPending ? (
          <Spinner />
        ) : (drafts.data ?? []).length === 0 ? (
          <p className="py-6 text-center text-sm text-muted">{uiText("features.authoring.DraftBar.noch_keine_drafts_vorhanden_8f793b")}</p>
        ) : (
          <ul className="divide-y divide-line rounded-[var(--radius-md)] border border-line">
            {drafts.data!.map((draft) => (
              <li key={draft.draft_id}>
                <button type="button" className="flex w-full cursor-pointer items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-raised" onClick={() => pick(draft)}>
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{draftLabel(draft)}</span>
                    <span className="block text-xs text-muted">{uiText("features.authoring.DraftBar.v_4c9448")}{draft.draft_version} {uiText("features.authoring.DraftBar.basis_revision_cb2fb5")}{draft.base_revision ?? '–'} · {formatTime(draft.updated_at)}</span>
                  </span>
                  {doc.draft?.draft_id === draft.draft_id && <Badge tone="accent">{doc.dirty ? uiText("features.authoring.DraftBar.geoffnet_ungespeichert_62ba6c") : uiText("features.authoring.DraftBar.geoffnet_ec5e95")}</Badge>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Dialog>
      <CreateDraftDialog open={creating} onOpenChange={setCreating} onCreated={() => onOpenChange(false)} />
    </>
  );
}

/** Asks before unsaved changes of the open draft are dropped by opening another one. */
export function SwitchDialog() {
  const doc = useWorkingDocument();
  return <Dialog open={doc.switchRequest !== null} onOpenChange={open => !open && doc.cancelSwitch()} title={uiText('beta3.7872db271b81')} description={<>{uiText('beta3.9e20711b60e7')} {doc.switchRequest && draftLabel(doc.switchRequest)}</>} footer={<><Button onClick={doc.cancelSwitch}>{uiText('beta3.5b9c81fe9755')}</Button><Button onClick={doc.confirmSwitch}>{uiText('beta3.fc07c17bc2e1')}</Button><Button variant="primary" disabled={doc.saving} onClick={() => void (async () => { if (await doc.save() === 'saved') doc.confirmSwitch(); })()}>{uiText('beta3.2c515692b9be')}</Button></>} />;
}

/** Version conflict (OCC): never overwrites silently; the user picks explicitly. */
export function ConflictDialog() {
  const doc = useWorkingDocument();
  const navigate = useNavigate();
  const location = useLocation();
  const conflict = doc.conflict;
  const [busy, setBusy] = useState(false);
  const [choices, setChoices] = useState<Record<string, string>>({});
  useEffect(() => setChoices({}), [conflict]);
  if (!conflict) return null;
  const same = canonicalJson(conflict.server.config) === canonicalJson(doc.config);
  const valueText = (path: string, value: unknown) => path.endsWith('/freshness_selection') ? freshnessChoiceLabel(conflict.server.config, value) ?? JSON.stringify(value) : JSON.stringify(value);
  const run = async (choice: Parameters<typeof doc.resolveConflict>[0]) => {
    setBusy(true);
    try {
      const decisions = Object.fromEntries((conflict.comparison?.conflicts ?? []).map(c => [c.path, { digests: c.digests, choice: choices[c.path] ?? '' }]));
      const createdId = await doc.resolveConflict(choice, decisions);
      if (createdId && location.pathname.startsWith('/changes/drafts/')) navigate(`/changes/drafts/${encodeURIComponent(createdId)}`, { replace: true });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && void run('cancel')}
      title={uiText("features.authoring.DraftBar.versionskonflikt_beim_speichern_b52e2a")}
      description={uiText("features.authoring.DraftBar.der_draft_wurde_seit_dem_laden_verandert_server__3341e6", { v0: (conflict.server.draft_version), v1: (doc.draft?.draft_version) })}
      footer={
        <>
          <Button disabled={busy} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => run('cancel')}>{uiText("features.authoring.DraftBar.abbrechen_f7ff11")}</Button>
          <Button disabled={busy} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => run('take-server')}>{uiText("features.authoring.DraftBar.server_stand_ubernehmen_2f2a5e")}</Button>
          <Button variant="primary" disabled={busy || !conflict.comparison || conflict.comparison.conflicts.some(c => !choices[c.path])} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => void run('merge')}>
            {uiText('b3.mergeInputs')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        {same ? <Notice tone="info">{uiText("features.authoring.DraftBar.beide_stande_sind_inhaltlich_gleich_ein_ubernehm_d8a22e")}</Notice> : <Notice tone="warn" title={uiText("features.authoring.DraftBar.die_stande_unterscheiden_sich_0b65e1")}>{uiText("features.authoring.DraftBar.server_stand_ubernehmen_verwirft_ihre_lokalen_an_59393c")}</Notice>}
        <p>{uiText('b3.mergeExplanation')}</p>
        {conflict.comparison?.conflicts.map(row => <fieldset className="min-w-0 rounded border border-line p-3" key={row.path}><legend>{row.path.endsWith('/freshness_selection') ? uiText('b4.title') : fieldLabel(row.path.split('/').at(-1) ?? '')}</legend><p>{uiText('b3.lastRead')}: {valueText(row.path, row.base)}</p>{(['draft', 'current'] as const).map(side => <label className="flex min-h-11 items-center gap-2 break-all" key={side}><input type="radio" name={row.path} checked={choices[row.path] === side} onChange={() => setChoices({ ...choices, [row.path]: side })} />{uiText(side === 'draft' ? 'b3.localInput' : 'b3.savedInput')}: {valueText(row.path, row[side])}</label>)}</fieldset>)}
      </div>
    </Dialog>
  );
}

export function ValidationChip() {
  const doc = useWorkingDocument();
  const { validation, autoValidate } = doc;
  const [show, setShow] = useState(false);
  const count = validation.issues.length;
  let content;
  if (validation.state === 'checking') content = <Badge tone="neutral"><Loader2 size={11} className="animate-spin" />{uiText('alpha7.draft.checking')}</Badge>;
  else if (validation.state === 'failed') content = <Badge tone="bad">{uiText('alpha7.draft.checkFailed')}</Badge>;
  else if (validation.state === 'idle') content = <Badge tone="neutral">{autoValidate ? uiText("features.authoring.DraftBar.wird_gepruft_90f584") : uiText("features.authoring.DraftBar.ungepruft_08112f")}</Badge>;
  else if (count) content = <Badge tone="bad"><CircleAlert size={11} />{uiText('alpha7.draft.errors', { count })}{validation.stale ? uiText('alpha7.draft.recheck') : ''}</Badge>;
  else content = <Badge tone={validation.stale ? 'neutral' : 'ok'}><CheckCircle2 size={11} />{validation.stale ? uiText('alpha7.draft.changed') : uiText('alpha7.draft.noErrors')}</Badge>;
  return <>
    {count || validation.state === 'failed' ? <button type="button" aria-haspopup="dialog" className="cursor-pointer rounded-full" onClick={() => setShow(true)}>{content}</button> : <span role="status" aria-live="polite">{content}</span>}
    <Dialog open={show} onOpenChange={setShow} title={uiText('alpha7.draft.checkResult')} footer={<Button onClick={() => setShow(false)}>{uiText('alpha7.draft.close')}</Button>}>
      {validation.error && <p role="alert" className="mb-3 text-sm text-bad">{validation.error}</p>}
      {validation.stale && <p className="mb-3 text-sm">{uiText('alpha7.draft.changed')}</p>}
      <IssueList issues={validation.issues} config={doc.config} />
    </Dialog>
  </>;
}

export function ReviewDraftButton({ children, size = 'sm', variant = 'primary', checkFirst = false, onReview, steadyLabel }: { children?: React.ReactNode; size?: 'sm' | 'md'; variant?: 'primary' | 'secondary' | 'ghost'; checkFirst?: boolean; onReview?: (draft: Draft) => void; steadyLabel?: string }) {
  const doc = useWorkingDocument();
  const navigate = useNavigate();
  const [target, setTarget] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const invalid = !doc.dirty && !doc.validation.stale && doc.validation.state === 'done' && doc.validation.issues.length > 0;
  const checking = !doc.dirty && doc.validation.state === 'checking';
  const requiresCheck = checkFirst || doc.dirty;
  const label = checking ? uiText('b3.checking') : invalid ? uiText('b3.fixErrors') : !doc.dirty && doc.validation.state === 'failed' ? uiText('b3.checkAgain') : requiresCheck ? uiText('beta3.f539371f8846') : children ?? uiText('alpha7.draft.apply');
  const review = async () => {
    setBusy(true); setError('');
    try { const saved = await doc.prepareActivation(); if (onReview) onReview(saved); else setTarget(saved); }
    catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  return <>
    <Button size={size} variant={variant} disabled={!doc.draft || busy || doc.saving || checking} disabledReason={uiText('alpha7.copy.validationRunning')} onClick={() => { if (invalid) { const issue = doc.validation.issues[0]!; const where = locateIssue(issue.path, doc.config); navigate(`${where.to}${where.to.includes('?') ? '&' : '?'}focus=${encodeURIComponent(issue.path)}`); } else if (requiresCheck) void (async () => { if (!doc.dirty || await doc.save() === 'saved') await doc.validateNow(); })(); else void review(); }}>{steadyLabel ?? (busy ? uiText('alpha7.draft.preparingReview') : label)}</Button>
    {!doc.draft && <span className="text-xs text-muted">{uiText('alpha7.draft.noDraft')}</span>}
    {error && <span role="alert" className="text-xs text-bad">{error}</span>}
    {target && <ActivateDialog draft={target} open onOpenChange={open => !open && setTarget(null)} />}
  </>;
}

export function DraftBar() {
  const doc = useWorkingDocument();
  const active = useActiveRevision();
  const navigate = useNavigate();
  const closeDraft = () => { doc.close(); if (detailPage) navigate('/changes/drafts'); };
  const detailPage = useLocation().pathname.startsWith('/changes/drafts/');
  const saveStatusId = useId();
  const [picker, setPicker] = useState(false);
  const [closing, setClosing] = useState(false);
  const diff = doc.validation.state === 'done' && !doc.validation.stale ? doc.validation.result?.diff : undefined;
  const changes = diff ? changedObjectCount(diff) : null;
  const invalid = doc.validation.state === 'done' && !doc.validation.stale && doc.validation.issues.length > 0;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && doc.draft && doc.editing) {
        event.preventDefault();
        if (doc.dirty && !doc.saving) void doc.save();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [doc]);

  return (
    <div className="flex flex-wrap items-center gap-2" aria-label={uiText("features.authoring.DraftBar.arbeits_draft_00218f")}>
      {doc.starting && <span role="status" className="text-xs text-muted">{uiText('alpha7.draft.preparing')}</span>}
      {doc.draft && doc.editing ? (
        <>
          <span className="min-w-0 text-xs text-muted"><span title={draftLabel({ draft_id: doc.draft.draft_id, display_name: doc.displayName })} className="block max-w-40 truncate">{draftLabel({ draft_id: doc.draft.draft_id, display_name: doc.displayName })}</span><span id={saveStatusId} role="status" className="block">{uiText(doc.saving ? 'alpha7.draft.saving' : doc.dirty ? 'alpha7.draft.notSaved' : 'alpha7.draft.saved')}</span></span>
          <span aria-label={uiText('alpha7.draft.changeCount')} className="text-sm font-medium">{invalid ? <>–<span className="block text-xs font-normal text-muted">{uiText('post1.draft.countAfterErrors')}</span></> : changes === null ? '…' : countSentence(changes, 'changes')}</span>
          <ValidationChip />
          <ReviewDraftButton steadyLabel={uiText('b22.object.applyChanges')} />
          {!detailPage && <Link className="min-h-11 underline" to={`/changes/drafts/${doc.draft.draft_id}`}>{uiText('beta3.8afb05d4c53f')}</Link>}
          <span role="status">{draftState(doc.draft, active.data?.revision)}</span>
          <details className="relative" onClick={event => { if ((event.target as HTMLElement).closest('button')) event.currentTarget.open = false; }}><summary aria-label={uiText('alpha7.draft.more')} className="grid size-8 cursor-pointer list-none place-items-center rounded-lg hover:bg-raised"><MoreHorizontal size={16} /></summary><div className="absolute right-0 top-9 z-30 flex min-w-48 flex-col items-start gap-1 rounded-lg border border-line bg-panel p-2 shadow-xl">
            <Button size="sm" variant="ghost" disabled={!doc.dirty || doc.saving} aria-describedby={saveStatusId} onClick={() => void doc.save()} title={uiText('features.authoring.DraftBar.strg_s_bda7e5')}><Save size={13} />{uiText('features.authoring.DraftBar.speichern_f6b2ff')}</Button>
            {doc.canUndo && <Button size="sm" variant="ghost" onClick={doc.undo}><Undo2 size={13} />{uiText('features.authoring.DraftBar.ruckgangig_dd7bd2')}</Button>}
            {doc.canRedo && <Button size="sm" variant="ghost" onClick={doc.redo}><Redo2 size={13} />{uiText('features.authoring.DraftBar.wiederholen_e0ef00')}</Button>}
            <Button size="sm" variant="ghost" onClick={() => setPicker(true)}>{uiText('alpha7.draft.switch')}</Button>
            <Button size="sm" variant="ghost" onClick={() => doc.dirty ? setClosing(true) : closeDraft()}>{uiText('features.authoring.DraftBar.draft_schlie_en_e8650e')}</Button>
          </div></details>
        </>
      ) : doc.dirty ? <span className="text-xs text-muted">{uiText('alpha7.draft.kept')}</span> : null}
      <DraftPicker open={picker} onOpenChange={setPicker} />
      <ConflictDialog />
      <SwitchDialog />
      <ConfirmDialog open={closing} onOpenChange={setClosing} title={uiText("features.authoring.DraftBar.draft_schlie_en_f5ae17")} description={uiText("features.authoring.DraftBar.es_gibt_ungespeicherte_anderungen_sie_gehen_verl_002e77")} confirmLabel={uiText("features.authoring.DraftBar.verwerfen_und_schlie_en_6dcb36")} tone="danger" onConfirm={() => { setClosing(false); closeDraft(); }} />
      {doc.saveError && <span role="alert" className="text-xs text-bad">{doc.saveError}</span>}
    </div>
  );
}

/** Shown above authoring pages that currently display the (read-only) active registry. */
export function ReadOnlyNotice({ what }: { what: string }) {
  void what;
  return null;
}
