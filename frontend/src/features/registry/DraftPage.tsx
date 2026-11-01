import { ContinuationDialog } from './ContinuationDialog';
import { ActivateDialog } from './ActivationDialogs';
import { currentDraft, draftState, CONTINUE, recoverActivation } from './authoring';
import { useActiveRevision } from '@/lib/queries';
import { PageLoading } from '@/components/PageLoading';
import { t as uiText } from '@/i18n';
import { useQuery } from '@tanstack/react-query';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { ArrowLeft, CheckCircle2, CircleAlert, Download } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Collapsible } from '@/components/ui/controls';
import { ConfirmDialog } from '@/components/ui/dialog';
import { Empty, KeyValue, Notice, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { Input, Textarea } from '@/components/ui/form';
import { draftLabel, ReviewDraftButton } from '@/features/authoring/DraftBar';
import { downloadDraft, parseRegistryJson } from '@/features/authoring/importExport';
import { asArray } from '@/features/authoring/model';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { api, describeError } from '@/lib/api';
import type { Draft } from '@/lib/types';
import { DiffView } from './DiffView';
import { IssueList } from './IssueList';

const SECTIONS: { key: string; label: string; to: string }[] = [
  { key: 'dependencies', label: uiText("features.registry.DraftPage.dependencies_2e41b1"), to: "/sources/dependencies?tab=dependencies" },
  { key: 'sources', label: uiText("features.registry.DraftPage.sources_caf85b"), to: "/sources/devices" },
  { key: 'bindings', label: uiText("features.registry.DraftPage.bindings_7697ab"), to: "/sources/bindings?tab=bindings" },
  { key: 'catalogs', label: uiText("features.registry.DraftPage.kataloge_daf282"), to: "/sources/catalogs?tab=catalogs" },
  { key: 'definitions', label: uiText("features.registry.DraftPage.definitionen_871a91"), to: "/templates/own" },
  { key: 'contracts', label: uiText("features.registry.DraftPage.contracts_0488ac"), to: "/contracts/list/all" },
];

function JsonExpert() {
  const doc = useWorkingDocument();
  const current = useMemo(() => (doc.config ? JSON.stringify(doc.config, null, 2) : ''), [doc.config]);
  const [text, setText] = useState(current);
  const [error, setError] = useState('');
  useEffect(() => setText(current), [current]);
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted">{uiText("features.registry.DraftPage.optionale_expertenansicht_des_gesamten_dokuments_c2d8aa")}</p>
      <Textarea aria-label={uiText("features.registry.DraftPage.registry_json_dceba9")} rows={18} spellCheck={false} value={text} onChange={(e) => { setText(e.target.value); setError(''); }} />
      {error && <p role="alert" className="text-xs text-bad">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" disabled={text === current} disabledReason={uiText('alpha7.copy.unchanged')} onClick={() => { const parsed = parseRegistryJson(text); if (parsed.ok) { doc.replaceAll(parsed.config); setError(''); } else setError(parsed.error); }}>{uiText('alpha9.draft.applyText')}</Button>
        <Button size="sm" disabled={text === current} disabledReason={uiText('alpha7.copy.unchanged')} onClick={() => { setText(current); setError(''); }}>{uiText("features.registry.DraftPage.zurucksetzen_1ba046")}</Button>
      </div>
    </div>
  );
}

export default function DraftPage() {
  const { draftId = '' } = useParams();
  const doc = useWorkingDocument();
  const query = useLocation().search;
  const server = useQuery({ queryKey: ['registry', 'drafts', draftId], queryFn: () => api<Draft>(`registry/drafts/${encodeURIComponent(draftId)}`), retry: false });
  const [switching, setSwitching] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [activationTarget, setActivationTarget] = useState<Draft | null>(null);
  const [exportError, setExportError] = useState('');
  const active = useActiveRevision();
  useEffect(() => { if (new URLSearchParams(query).get('continue') === '1' && doc.draft?.draft_id === draftId && !doc.dirty) setContinuing(true); }, [query, doc.draft?.draft_id, doc.dirty, draftId]);
  const effect = useQuery({ queryKey: ['draft-effect', draftId, doc.draft?.draft_version, active.data?.revision], queryFn: () => api<import('./authoring').ActivationPreview>(`registry/drafts/${draftId}/activation-preview`, { method: 'POST', body: {} }), enabled: doc.draft?.draft_id === draftId && !doc.dirty && doc.validation.result?.valid === true && !doc.validation.stale && doc.draft.lifecycle !== 'completed' && doc.draft.lifecycle !== 'continued' });
  useEffect(() => { void recoverActivation(draftId).then(result => { if (result) { doc.close(); void server.refetch(); } }).catch(() => undefined); }, [draftId]);
  const isOpen = doc.draft?.draft_id === draftId;
  const autoOpened = useRef<string | null>(null);
  const blocked = !isOpen && doc.dirty;

  useEffect(() => {
    if (isOpen) autoOpened.current = draftId;
    if (!isOpen && !doc.dirty && server.data && autoOpened.current !== draftId) { autoOpened.current = draftId; doc.open(server.data); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, doc.dirty, server.data]);

  if (server.isError && !isOpen) {
    return (
      <>
        <PageHeader title={uiText('alpha7.nav.drafts')} description={uiText('alpha9.pages.draft')} actions={<Link to="/changes/drafts"><Button variant="primary">{uiText('features.registry.DraftPage.zum_geoffneten_draft_855199')}</Button></Link>} />
        <Empty title={uiText("features.registry.DraftPage.draft_v0_nicht_gefunden_ea702b", { v0: (draftId) })}>{uiText("features.registry.DraftPage.er_wurde_geloscht_oder_hat_nie_existiert_0556f2")}</Empty>
      </>
    );
  }
  if (blocked) {
    return (
      <>
        <PageHeader title={uiText('alpha7.nav.drafts')} description={uiText('alpha9.pages.draft')} actions={<Link to={`/changes/drafts/${encodeURIComponent(doc.draft?.draft_id ?? '')}`}><Button variant="primary">{uiText('features.registry.DraftPage.zum_geoffneten_draft_855199')}</Button></Link>} />
        <Notice tone="warn" title={uiText("features.registry.DraftPage.ein_anderer_draft_hat_ungespeicherte_anderungen_261d0b")}>
          {uiText("features.registry.DraftPage.geoffnet_ist_e6c05b")}{doc.draft ? draftLabel(doc.draft) : ''}{uiText("features.registry.DraftPage.speichern_sie_ihn_zuerst_oder_verwerfen_sie_die__baa37d")}<div className="mt-2 flex gap-2">
            <Link to={`/changes/drafts/${encodeURIComponent(doc.draft?.draft_id ?? '')}`}><Button size="sm">{uiText("features.registry.DraftPage.zum_geoffneten_draft_855199")}</Button></Link>
            <Button size="sm" variant="danger" onClick={() => setSwitching(true)}>{uiText("features.registry.DraftPage.verwerfen_und_wechseln_88c2d1")}</Button>
          </div>
        </Notice>
        <ConfirmDialog open={switching} onOpenChange={setSwitching} title={uiText("features.registry.DraftPage.anderungen_verwerfen_d16767")} description={uiText("features.registry.DraftPage.die_ungespeicherten_anderungen_des_geoffneten_dr_9242f0")} confirmLabel={uiText("features.registry.DraftPage.verwerfen_594ca8")} tone="danger" onConfirm={() => { setSwitching(false); if (server.data) { doc.close(); doc.open(server.data); } }} />
      </>
    );
  }
  if (!isOpen || !doc.draft || !doc.config) return <PageLoading title={uiText('alpha7.nav.drafts')} description={uiText('alpha9.pages.draft')} />;

  const { draft, config, validation } = doc;
  const closed = !!server.data?.lifecycle && server.data.lifecycle !== 'open';
  const current = currentDraft(server.data ?? draft, active.data?.revision ?? 0);
  if (closed && !activationTarget) return <><Back /><PageHeader title={draftLabel(draft)} description={uiText('beta3.88e34492c718')} actions={server.data?.completed_revision ? <Link to={`/changes/versions?revision=${server.data.completed_revision}`}><Button variant="primary" onClick={doc.close}>{uiText('beta3.838cb6772119')}</Button></Link> : <Link to={`/changes/drafts/${server.data?.continued_to_draft_id}`}><Button variant="primary">{uiText('beta3.533a922fbb99')}</Button></Link>} /><Notice tone="ok" title={draftState(server.data!, active.data?.revision)}>{server.data?.continued_to_draft_id && <Link to={`/changes/drafts/${server.data.continued_to_draft_id}`}>{uiText('beta3.533a922fbb99')}</Link>}</Notice></>;
  const validNow = !doc.dirty && validation.state === 'done' && validation.result?.valid === true && !validation.stale;

  return (
    <>
      <Back />
      <PageHeader
        title={draftLabel({ draft_id: draft.draft_id, display_name: doc.displayName })}
        description={uiText('alpha9.pages.draft')}
        actions={
          <>
            {current ? <ReviewDraftButton size="md" checkFirst={!validNow} onReview={setActivationTarget}>{validNow && effect.data?.no_effect ? uiText('b3.complete') : undefined}</ReviewDraftButton> : <Button variant="primary" onClick={() => void (async () => { if (!doc.dirty || await doc.save() === 'saved') setContinuing(true); })()}>{draft.base_status === 'needs_review' ? uiText('beta3.c902a9a3c590') : CONTINUE}</Button>}
            <Button disabled={doc.dirty} onClick={() => void downloadDraft(draft, active.data?.revision ?? 0, doc.displayName || draft.draft_id).catch(error => setExportError(describeError(error)))}><Download size={14} /> {uiText("features.registry.DraftPage.export_366489")}</Button>
          </>
        }
      />
      <Notice tone={current ? 'info' : 'warn'} title={draftState(server.data ?? draft, active.data?.revision ?? 0)}>{!current && <><p>{uiText('b3.baseExplanation', { name: doc.displayName, base: draft.base_revision ?? '–', active: active.data?.revision ?? '–' })}</p><p>{uiText('b3.continueHelp')}</p><Link to="/changes/drafts">{uiText('b3.allDrafts')}</Link></>}</Notice>
      {exportError && <Notice tone="bad">{exportError}</Notice>}
      {activationTarget && <ActivateDialog draft={activationTarget} open onOpenChange={open => !open && setActivationTarget(null)} />}
      {continuing && <ContinuationDialog draft={draft} onClose={() => setContinuing(false)} />}
      <fieldset disabled={!current} className="min-w-0">
      <PanelLayout view="registry.draft" defaultOrder={['validation', 'name', 'diff', 'activation', 'editor', 'json']}>
          <LayoutPanel id="validation" title={uiText("features.registry.DraftPage.validierung_d4bf21")} span={2}>
          <div data-pointer="validation" tabIndex={-1}>
          <Panel>
            <PanelHeader title={uiText("features.registry.DraftPage.validierung_d4bf21")} description={uiText("features.registry.DraftPage.der_server_pruft_den_bearbeiteten_auch_ungespeic_ebe4cd")} aside={<Switch checked={doc.autoValidate} onCheckedChange={doc.setAutoValidate} label={uiText("features.registry.DraftPage.automatisch_f0b793")} />} />
            <div className="flex flex-col gap-3 p-4">
              {(validation.state === 'idle' || validation.state === 'checking') && <Notice tone="info" title={uiText('alpha7.draft.checking')} />}
              {validation.state === 'failed' && <Notice tone="warn" title={uiText("features.registry.DraftPage.prufung_nicht_moglich_9c9bd1")}>{validation.error}</Notice>}
              {validation.state === 'done' && !validation.stale && validation.issues.length === 0 && <Notice tone="ok" title={uiText("features.registry.DraftPage.keine_fehler_gefunden_0d9c76")}>{uiText("features.registry.DraftPage.der_stand_ist_strukturell_und_semantisch_gultig_a85b70")}</Notice>}
              {validation.stale && validation.state !== 'checking' && <Notice tone="info" title={uiText('alpha7.draft.changed')} />}
              {validation.state === 'done' && !validation.stale && validation.issues.length > 0 && (
                <>
                  <Notice tone="bad" title={uiText("features.registry.DraftPage.v0_fehler_v1_bffe5e", { v0: (validation.issues.length), v1: (validation.stale ? uiText("features.registry.DraftPage.veraltet_14c672") : '') })}>{uiText("features.registry.DraftPage.jeder_eintrag_verweist_auf_das_betroffene_objekt_8d552f")}</Notice>
                  <IssueList issues={validation.issues} config={config} />
                </>
              )}
            </div>
          </Panel></div>
          </LayoutPanel><LayoutPanel id="diff" title={uiText("features.registry.DraftPage.diff_auswirkungen_b6da5d")} span={2}><Panel>
            <PanelHeader title={uiText("features.registry.DraftPage.diff_auswirkungen_b6da5d")} description={uiText("features.registry.DraftPage.gegenuber_der_aktiven_revision_nur_sichtbar_wenn_b51778")} />
            <div className="p-4">
              {validation.state === 'done' && !validation.stale && validation.result?.diff && validation.result.valid ? <DiffView diff={validation.result.diff} against={uiText("features.registry.DraftPage.die_aktive_revision_ccaa57")} /> : <p className="text-sm text-muted">{validation.issues.length ? uiText("features.registry.DraftPage.erst_nach_korrektur_der_fehler_verfugbar_bd3e1e") : uiText("features.registry.DraftPage.wird_nach_der_validierung_angezeigt_0f660e")}</p>}
            </div>
          </Panel>
          </LayoutPanel><LayoutPanel id="json" title={uiText("features.registry.DraftPage.json_experte_7a839f")} span={2}><Collapsible title={uiText('alpha10.technical')}><p className="mb-2 text-sm text-muted">{uiText('alpha10.checksumHelp')}</p><KeyValue items={[{ label: uiText('alpha7.draft.identifier'), value: draft.draft_id }, { label: uiText("features.registry.DraftPage.fingerprint_version_e46d72"), value: String(config.fingerprint_version ?? 1) }]} /><JsonExpert /></Collapsible></LayoutPanel>
          <LayoutPanel id="name" title={uiText("features.registry.DraftPage.draft_name_026ddb")} span={1}>
          <Panel>
            <PanelHeader title={uiText("features.registry.DraftPage.name_dcd1d5")} />
            <div className="p-4"><Input aria-label={uiText("features.registry.DraftPage.draft_name_026ddb")} maxLength={256} value={doc.displayName} placeholder={uiText("features.registry.DraftPage.name_des_drafts_2ca771")} onChange={(e) => doc.setDisplayName(e.target.value)} /></div>
          </Panel>
          </LayoutPanel><LayoutPanel id="activation" title={uiText("features.registry.DraftPage.aktivierung_90ba1a")} span={1}><Panel>
            <PanelHeader title={uiText("features.registry.DraftPage.aktivierung_90ba1a")} />
            <div className="flex flex-col gap-3 p-4">
              <ul className="space-y-1 text-sm">
                <li className="flex items-center gap-2">{doc.dirty ? <CircleAlert size={14} className="text-warn" /> : <CheckCircle2 size={14} className="text-ok" />} {uiText("features.registry.DraftPage.gespeichert_5e2338")}</li>
                <li className="flex items-center gap-2">{validNow ? <CheckCircle2 size={14} className="text-ok" /> : <CircleAlert size={14} className="text-warn" />} {uiText("features.registry.DraftPage.aktuell_gultig_2034ba")}</li>
                <li>{current ? '✓' : '○'} {uiText('b3.current')}</li>
                <li>○ {uiText('b3.confirmed')} · {uiText('b3.confirmInDialog')}</li>
              </ul>

            </div>
          </Panel>
          </LayoutPanel><LayoutPanel id="editor" title={uiText("features.registry.DraftPage.registry_inhalt_4f888f")} span={1}><Panel>
            <PanelHeader title={uiText("features.registry.DraftPage.inhalt_3cc465")} />
            <ul className="divide-y divide-line">
              {SECTIONS.map((section) => (
                <li key={section.key}>
                  <Link to={section.to} className="flex items-center justify-between px-4 py-2 text-sm hover:bg-raised"><span>{section.label}</span><Badge>{asArray(config[section.key]).length}</Badge></Link>
                </li>
              ))}
            </ul>
          </Panel>
          </LayoutPanel>
      </PanelLayout>
      </fieldset>
    </>
  );
}

function Back() {
  return (
    <Link to="/changes/drafts" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg">
      <ArrowLeft size={13} /> {uiText("features.registry.DraftPage.registry_studio_995ef2")}</Link>
  );
}
