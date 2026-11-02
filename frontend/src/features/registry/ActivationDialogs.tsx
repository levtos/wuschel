import { Link } from 'react-router';
import { uuid } from '@/lib/uuid';
import { useOptionalWorkingDocument } from '@/features/authoring/WorkingDocument';
import { ActivationPreview, activateWithReceipt, CONTINUE } from './authoring';
import { validationLabel } from '@/i18n/codes';
import { t as uiText } from '@/i18n';
import { useQueryClient } from '@tanstack/react-query';
import { CheckCircle2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/controls';
import { Dialog } from '@/components/ui/dialog';
import { Collapsible } from '@/components/ui/controls';
import { Field, Input } from '@/components/ui/form';
import { Notice, Spinner } from '@/components/ui/layout';
import { api, ApiError, describeError, type Issue } from '@/lib/api';
import type { JsonObject } from '@/lib/json';
import { useLive } from '@/lib/live-context';
import { keys, useActivation, validateConfig } from '@/lib/queries';
import type { Draft, RevisionRow, ValidationResult } from '@/lib/types';
import { DiffView } from './DiffView';
import { IssueList } from './IssueList';

type Check =
  | { state: 'checking' }
  | { state: 'ok'; result: ValidationResult }
  | { state: 'invalid'; issues: Issue[] }
  | { state: 'stale'; message: string; reason: 'draft' | 'base' }
  | { state: 'error'; message: string };

class StaleSubject extends Error { constructor(message: string, public reason: 'draft' | 'base' = 'draft') { super(message); } }

/**
 * Checks exactly one subject (draft id + saved version, or a revision). A result and
 * any confirmation belong to that subject and to the active revision they were computed
 * against: when either changes, the check restarts and the old result is discarded.
 * `run` is read through a ref, so it always validates the *current* subject without
 * making the restart depend on callback identity (no re-check loops).
 */
function useCheck(open: boolean, subject: string, run: () => Promise<ValidationResult>) {
  const client = useQueryClient();
  const live = useLive();
  const runRef = useRef(run);
  runRef.current = run;
  const ticket = useRef(0);
  const [state, setState] = useState<{ subject: string; check: Check; expected: number }>({ subject, check: { state: 'checking' }, expected: 0 });

  const start = useCallback(async () => {
    const mine = ++ticket.current;
    const runCandidate = runRef.current;
    setState({ subject, check: { state: 'checking' }, expected: 0 });
    let outcome: Check;
    let expected = 0;
    try {
      const active = await client.fetchQuery({ queryKey: keys.active, queryFn: () => api<RevisionRow | null>('registry/active'), staleTime: 0 });
      if (mine !== ticket.current) return;
      expected = active?.revision ?? 0;
      const result = await runCandidate();
      const after = await api<RevisionRow | null>('registry/active');
      if ((after?.revision ?? 0) !== expected) throw new StaleSubject(uiText("features.registry.ActivationDialogs.die_aktive_revision_hat_sich_wahrend_der_prufung_38126e"), 'base');
      outcome = { state: 'ok', result };
    } catch (error) {
      if (error instanceof StaleSubject) outcome = { state: 'stale', message: error.message, reason: error.reason };
      else if (error instanceof ApiError && error.kind === 'validation') outcome = { state: 'invalid', issues: error.issues };
      else outcome = { state: 'error', message: describeError(error) };
    }
    if (mine === ticket.current) setState({ subject, check: outcome, expected });
  }, [client, subject]);

  useEffect(() => {
    if (open) void start();
    else ticket.current += 1; // a closed dialog keeps no result
  }, [open, start]);

  // A result computed for another subject is never shown or confirmable.
  const current = state.subject === subject;
  const base: Check = current ? state.check : { state: 'checking' };
  const expected = current ? state.expected : 0;
  // The active revision moved while the dialog was open: the diff no longer describes the change.
  const moved = open && current && base.state === 'ok' && live.registryRevision !== null && live.registryRevision !== expected;
  const check: Check = moved ? { state: 'stale', reason: 'base', message: uiText("features.registry.ActivationDialogs.die_aktive_revision_ist_inzwischen_v0_gepruft_ge_481253", { v0: (live.registryRevision), v1: (expected) }) } : base;
  return { check, expected, currentRevision: live.registryRevision ?? expected, restart: start, key: `${subject}@${expected}` };
}

function Outcome({ error, onRecheck, draftId, close }: { error: unknown; onRecheck: () => void; draftId?: string; close?: () => void }) {
  if (!error) return null;
  if (error instanceof ApiError && error.kind === 'conflict') {
    if (draftId && ['draft_base_conflict', 'active_revision_conflict', 'draft_base_unknown', 'import_plan_conflict', 'catalog_content_conflict', 'editorial_version_conflict'].includes(error.code)) return <Notice tone="warn" title={uiText('b3.baseTitle')}><p>{describeError(error)}</p><p>{uiText('b3.noChange')}</p><Link to={`/changes/drafts/${draftId}?continue=1`} onClick={close}><Button variant="primary">{CONTINUE}</Button></Link><div><Link to={`/changes/drafts/${draftId}`} onClick={close}>{uiText('b3.draftView')}</Link> · <Link to="/changes/drafts" onClick={close}>{uiText('b3.allDrafts')}</Link></div></Notice>;
    return (
      <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.konflikt_nichts_wurde_aktiviert_8709dc")}>
        {describeError(error)}
        <div className="mt-2"><Button size="sm" onClick={onRecheck}>{uiText("features.registry.ActivationDialogs.stand_neu_laden_und_erneut_prufen_346ba0")}</Button></div>
      </Notice>
    );
  }
  if (error instanceof ApiError && error.kind === 'validation') {
    return <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.der_server_hat_die_aktivierung_abgelehnt_9260c9")}><IssueList issues={error.issues} config={null} /></Notice>;
  }
  return <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.aktion_fehlgeschlagen_3a20cc")}>{describeError(error)}</Notice>;
}

function StaleNotice({ message, onRecheck }: { message: string; onRecheck: () => void }) {
  return (
    <Notice tone="warn" title={uiText("features.registry.ActivationDialogs.prufergebnis_veraltet_ed4995")}>
      {message}
      <div className="mt-2"><Button size="sm" onClick={onRecheck}>{uiText("features.registry.ActivationDialogs.erneut_prufen_7da626")}</Button></div>
    </Notice>
  );
}

export function ActivateDialog({ draft, open, onOpenChange }: { draft: Draft; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { activate } = useActivation();
  const doc = useOptionalWorkingDocument();
  const [preview, setPreview] = useState<ActivationPreview | null>(null);
  const [retentionConfirmed, setRetentionConfirmed] = useState(false);
  const requestId = useRef(uuid());
  const [uncertain, setUncertain] = useState(false);
  const subject = `draft:${draft.draft_id}@${draft.draft_version}`;
  const { check, expected, currentRevision, restart, key } = useCheck(open, subject, async () => {
    // The saved draft on the server must still be the very version the user is about to activate.
    const saved = await api<Draft>(`registry/drafts/${encodeURIComponent(draft.draft_id)}`);
    if (saved.draft_version !== draft.draft_version) {
      throw new StaleSubject(uiText("features.registry.ActivationDialogs.der_draft_wurde_zwischenzeitlich_auf_v_v0_geande_f36298", { v0: (saved.draft_version), v1: (draft.draft_version) }));
    }
    const result = await api<ValidationResult>(`registry/drafts/${encodeURIComponent(draft.draft_id)}/validate`, { method: 'POST', body: {} });
    setPreview(await api<ActivationPreview>(`registry/drafts/${encodeURIComponent(draft.draft_id)}/activation-preview`, { method: 'POST', body: {} }));
    const after = await api<Draft>(`registry/drafts/${encodeURIComponent(draft.draft_id)}`);
    if (after.draft_version !== saved.draft_version) throw new StaleSubject(uiText("features.registry.ActivationDialogs.der_draft_hat_sich_wahrend_der_prufung_geandert__832d36"));
    return result;
  });
  const [confirmedFor, setConfirmedFor] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [done, setDone] = useState<{ subject: string; revision: RevisionRow } | null>(null);
  // Confirmation is valid only for the exact (draft, version, active revision) that was checked.
  const confirmed = confirmedFor === key;
  const result = done?.subject === subject ? done.revision : null;
  useEffect(() => {
    setConfirmedFor(null);
    setPreview(null); setRetentionConfirmed(false); requestId.current = uuid();
    setNote('');
    setDone(null);
    activate.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, subject]);
  const ready = check.state === 'ok' && check.result.valid && !activate.error && preview !== null;
  useEffect(() => { setConfirmedFor(null); setRetentionConfirmed(false); }, [preview?.comparison_digest]);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={uiText("features.registry.ActivationDialogs.registry_aktivieren_8c0eff")}
      description={uiText("features.registry.ActivationDialogs.gespeicherter_draft_v0_v_v1_wird_als_neue_revisi_1cd2fe", { v0: (draft.display_name || uiText('alpha7.draft.unnamed')), v1: (draft.draft_version) })}
      footer={
        result ? (
          <Link to={`/changes/versions?revision=${result.revision}`}><Button variant="primary" onClick={() => { doc?.close(); onOpenChange(false); }}>{uiText('beta3.838cb6772119')}</Button></Link>
        ) : (
          <>
            <div className="w-full space-y-2">
              {ready && <Checkbox checked={confirmed} onCheckedChange={(on) => setConfirmedFor(on ? key : null)} label={uiText('alpha9.configuration.confirm', { revision: expected + 1 })} />}
              {!confirmed && <p className="text-xs text-muted">{uiText("features.registry.ActivationDialogs.zum_ubernehmen_zuerst_die_auswirkungen_prufen_un_5ce543")}</p>}
            </div>
            <Button onClick={() => onOpenChange(false)}>{uiText("features.registry.ActivationDialogs.abbrechen_f7ff11")}</Button>
            <Button
              variant="primary"
              disabled={!ready || !confirmed || !!preview?.retention.shortening.length && !retentionConfirmed || activate.isPending || uncertain}
              onClick={() => { if (preview) activate.mutate({ draft, expectedActiveRevision: expected, note, run: () => activateWithReceipt(draft, preview, requestId.current, note, preview.no_effect, () => setUncertain(true)) }, { onSuccess: revision => { setUncertain(false); setDone({ subject, revision }); } }); }}
            >
              {uncertain ? uiText('beta3.3f9ccef22d20') : preview?.no_effect ? uiText('beta3.6ca558c11114') : uiText("features.registry.ActivationDialogs.jetzt_aktivieren_957b0a")}</Button>
          </>
        )
      }
    >
      {result ? (
        <Notice tone="ok" title={uiText("features.registry.ActivationDialogs.revision_v0_ist_aktiv_ffe993", { v0: (result.revision) })}>
          {uiText('beta3.2ba3f51e3f35', { v0: (result.revision) })}
          <Collapsible title={uiText('alpha7.versions.advanced')}><p className="mono break-all text-xs">{result.checksum}</p></Collapsible>
        </Notice>
      ) : (
        <div className="flex flex-col gap-4">
          <Notice tone="warn" title={uiText("features.registry.ActivationDialogs.aktivierung_andert_den_aktiven_zustand_5e5312")}>
            {uiText(currentRevision ? 'alpha9.configuration.activateFrom' : 'alpha9.configuration.activateFirst', { revision: currentRevision })}</Notice>
          {check.state === 'checking' && <Spinner label={uiText("features.registry.ActivationDialogs.server_pruft_den_gespeicherten_draft_afe932")} />}
          {check.state === 'error' && <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.prufung_nicht_moglich_9c9bd1")}>{check.message}</Notice>}
          {check.state === 'stale' && (check.reason === 'base' ? <Notice tone="warn" title={uiText('b3.baseTitle')}><p>{check.message}</p><Link to={`/changes/drafts/${draft.draft_id}?continue=1`} onClick={() => onOpenChange(false)}>{CONTINUE}</Link></Notice> : <StaleNotice message={check.message} onRecheck={() => void restart()} />)}
          {check.state === 'invalid' && (
            <>
              <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.der_gespeicherte_draft_ist_nicht_gultig_99bc6f")}>{uiText("features.registry.ActivationDialogs.aktivieren_ist_erst_nach_korrektur_und_erneutem__0d3dee")}</Notice>
              <IssueList issues={check.issues} config={draft.config as JsonObject} />
            </>
          )}
          {check.state === 'ok' && check.result.diff && <DiffView diff={check.result.diff} against={uiText("features.registry.ActivationDialogs.aktive_revision_v0_d363db", { v0: (expected || '–') })} />}
          {preview?.no_effect && <Notice tone="info">{uiText('beta3.32d735e08ded')}</Notice>}
          {preview?.retention.changed && <Notice tone={preview.retention.shortening.length ? 'warn' : 'info'} title={uiText('beta3.5c8fb977482e')}>
            {(['raw_days', 'rollup_days'] as const).filter(key => preview.retention.before?.[key] !== preview.retention.after?.[key]).map(key => <p key={key}>{key === 'raw_days' ? uiText('beta3.3a9f00a1ce0d') : uiText('beta3.7037f98da0fe')}: {preview.retention.before?.[key] ?? uiText('beta3.f711fa65f056')} → {preview.retention.after?.[key] ?? uiText('beta3.06c6cbc2bdef')} {uiText('beta3.6770c319ff7b')}</p>)}
            {preview.retention.shortening.length ? uiText('beta3.d9d1a2d04aaa') : ['raw_days', 'rollup_days'].every(key => preview.retention.before?.[key] === preview.retention.after?.[key]) ? uiText('b3.retentionOperation') : uiText('beta3.186ef596ae3b')}
          </Notice>}
          {!!preview?.retention.shortening.length && <Checkbox checked={retentionConfirmed} onCheckedChange={setRetentionConfirmed} label={uiText('beta3.525da4f813eb')} />}
          {uncertain && <Notice tone="info">{uiText('beta3.3f9ccef22d20')}</Notice>}
          {check.state === 'error' && <Link to={`/changes/drafts/${draft.draft_id}`} onClick={() => onOpenChange(false)}>{CONTINUE}</Link>}
          <Field label={uiText('alpha7.versions.note')} hint={uiText('alpha7.versions.noteHelp')}>{(props) => <Input {...props} maxLength={256} value={note} disabled={activate.isPending} onChange={(e) => { setNote(e.target.value); setConfirmedFor(null); }} />}</Field>
          {check.state === 'ok' && check.result.warnings?.map((warning) => <Notice key={warning.path} tone="warn" title={validationLabel(warning)}>{warning.object_id}</Notice>)}
          {!uncertain && <Outcome error={activate.error} draftId={draft.draft_id} close={() => onOpenChange(false)} onRecheck={() => { activate.reset(); setConfirmedFor(null); void restart(); }} />}
        </div>
      )}
    </Dialog>
  );
}

export function RollbackDialog({ revision, open, onOpenChange }: { revision: RevisionRow; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { rollback } = useActivation();
  const subject = `revision:${revision.revision}:${revision.checksum}`;
  const { check, expected, currentRevision, restart, key } = useCheck(open, subject, () => validateConfig(revision.config));
  const [confirmedFor, setConfirmedFor] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [done, setDone] = useState<{ subject: string; revision: RevisionRow } | null>(null);
  const confirmed = confirmedFor === key;
  const result = done?.subject === subject ? done.revision : null;
  useEffect(() => {
    setConfirmedFor(null);
    setNote('');
    setDone(null);
    rollback.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, subject]);
  const older = revision.revision < expected;
  const ready = check.state === 'ok' && check.result.valid && older && !rollback.error;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={uiText("features.registry.ActivationDialogs.auf_revision_v0_zuruckrollen_1c608b", { v0: (revision.revision) })}
      description={uiText("features.registry.ActivationDialogs.der_historische_stand_wird_als_neue_revision_akt_62c809")}
      footer={
        result ? (
          <Button variant="primary" onClick={() => onOpenChange(false)}>{uiText("features.registry.ActivationDialogs.schlie_en_b808f6")}</Button>
        ) : (
          <>
            <div className="w-full space-y-2">
              {ready && <Checkbox checked={confirmed} onCheckedChange={(on) => setConfirmedFor(on ? key : null)} label={uiText('alpha9.configuration.rollbackConfirm', { before: revision.revision, after: expected + 1 })} />}
              {!confirmed && <p className="text-xs text-muted">{uiText("features.registry.ActivationDialogs.zum_zurucksetzen_zuerst_die_auswirkungen_prufen__6e723c")}</p>}
            </div>
            <Button onClick={() => onOpenChange(false)}>{uiText("features.registry.ActivationDialogs.abbrechen_f7ff11")}</Button>
            <Button
              variant="danger"
              disabled={!ready || !confirmed || rollback.isPending}
              onClick={() => rollback.mutate({ revision: revision.revision, expectedActiveRevision: expected, note }, { onSuccess: (r) => setDone({ subject, revision: r }) })}
            >
              {uiText("features.registry.ActivationDialogs.zuruckrollen_1c5e6f")}</Button>
          </>
        )
      }
    >
      {result ? (
        <Notice tone="ok" title={<span className="flex items-center gap-2"><CheckCircle2 size={15} /> {uiText('alpha9.configuration.rollbackDone', { revision: result.revision })}</span>}>
          {uiText('alpha9.configuration.rollbackContent', { before: revision.revision })}</Notice>
      ) : (
        <div className="flex flex-col gap-4">
          <Notice tone="warn" title={uiText("features.registry.ActivationDialogs.rollback_andert_den_aktiven_zustand_975420")}>{uiText('alpha9.configuration.rollbackFrom', { revision: currentRevision })}</Notice>
          {check.state === 'checking' && <Spinner label={uiText("features.registry.ActivationDialogs.server_analysiert_den_historischen_stand_7fcf03")} />}
          {check.state === 'error' && <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.analyse_nicht_moglich_e876eb")}>{check.message}</Notice>}
          {check.state === 'stale' && <StaleNotice message={check.message} onRecheck={() => { setConfirmedFor(null); void restart(); }} />}
          {check.state === 'invalid' && (
            <>
              <Notice tone="bad" title={uiText("features.registry.ActivationDialogs.dieser_historische_stand_ist_unter_der_aktuellen_e1e6a7")}>{uiText("features.registry.ActivationDialogs.er_kann_nicht_zuruckgerollt_werden_a731a6")}</Notice>
              <IssueList issues={check.issues} config={revision.config as JsonObject} />
            </>
          )}
          {check.state === 'ok' && check.result.diff && <DiffView diff={check.result.diff} against={uiText("features.registry.ActivationDialogs.aktive_revision_v0_d363db", { v0: (expected || '–') })} />}
          {check.state === 'ok' && !older && <p role="status" className="text-sm text-muted">{uiText('alpha7.versions.olderOnly')}</p>}
          <Field label={uiText('alpha7.versions.note')} hint={uiText('alpha7.versions.noteHelp')}>{(props) => <Input {...props} maxLength={256} value={note} disabled={rollback.isPending} onChange={(e) => { setNote(e.target.value); setConfirmedFor(null); }} />}</Field>
          <Outcome error={rollback.error} onRecheck={() => { rollback.reset(); setConfirmedFor(null); void restart(); }} />
        </div>
      )}
    </Dialog>
  );
}
