import { currentDraft, draftState } from './authoring';
import { ConfigurationContent } from './ConfigurationContent';
import { t as uiText } from '@/i18n';
import { Copy, Download, FilePlus2, FolderOpen, GitCompare, History, Pencil, RotateCcw, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible } from '@/components/ui/controls';
import { ConfirmDialog, Dialog } from '@/components/ui/dialog';
import { Empty, Notice, PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { CreateDraftDialog } from '@/features/authoring/CreateDraftDialog';
import { draftLabel } from '@/features/authoring/DraftBar';
import { downloadDraft, downloadText, exportRegistry } from '@/features/authoring/importExport';
import { changedPaths } from '@/features/authoring/locate';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { describeError } from '@/lib/api';
import { formatTime } from '@/lib/format';
import { canonicalJson } from '@/lib/json';
import { asObject } from '@/features/authoring/model';
import { useLive } from '@/lib/live-context';
import { useActiveRevision, useDraftMutations, useDrafts, useHistory, useRevisions } from '@/lib/queries';
import type { Draft, RevisionRow } from '@/lib/types';
import { RollbackDialog } from './ActivationDialogs';

function DraftsTab() {
  const drafts = useDrafts();
  const doc = useWorkingDocument();
  const { remove } = useDraftMutations();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [copyOf, setCopyOf] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<Draft | null>(null);
  const [opening, setOpening] = useState<Draft | null>(null);
  // After the switch dialog confirms, follow into the draft's page.
  useEffect(() => {
    if (opening && doc.draft?.draft_id === opening.draft_id) {
      setOpening(null);
      navigate(`/changes/drafts/${encodeURIComponent(opening.draft_id)}`);
    } else if (opening && doc.switchRequest === null && doc.draft?.draft_id !== opening.draft_id) setOpening(null);
  }, [opening, doc.draft?.draft_id, doc.switchRequest, navigate]);
  const [error, setError] = useState('');
  const active = useActiveRevision();
  const [completed, setCompleted] = useState(false);
  const [staleOnly, setStaleOnly] = useState(false);
  const all = drafts.data ?? [];
  const openCount = all.filter(d => (d.lifecycle ?? 'open') === 'open').length;
  const list = all.filter(d => ((d.lifecycle ?? 'open') !== 'open') === completed).filter(d => !staleOnly || !currentDraft(d, active.data?.revision ?? 0));

  const open = (draft: Draft) => {
    if (doc.requestOpen(draft) !== 'pending') navigate(`/changes/drafts/${encodeURIComponent(draft.draft_id)}`);
    else setOpening(draft);
  };

  return (
    <>
    <PageHeader title={uiText('alpha7.nav.drafts')} description={uiText('alpha9.pages.drafts')} actions={<Button variant="primary" disabled={openCount >= 100} disabledReason={uiText('alpha7.import.limit')} onClick={() => setCreating(true)}><FilePlus2 size={15} />{uiText('features.registry.RegistryPage.neuer_draft_610fb2')}</Button>} />
    <div className="flex flex-wrap gap-2"><Button aria-pressed={!completed} onClick={() => setCompleted(false)}>{uiText('beta3.6bdbd1fb5da3')}</Button><Button aria-pressed={completed} onClick={() => setCompleted(true)}>{uiText('beta3.467e01ba533e')}</Button>{!completed && <Button aria-pressed={staleOnly} onClick={() => setStaleOnly(!staleOnly)}>{staleOnly ? uiText('beta3.fe1a5c4bbaa7') : uiText('beta3.d9075b29ee27')}</Button>}</div>
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">{openCount} {uiText("features.registry.RegistryPage.von_100_drafts_drafts_sind_niemals_live_sie_durf_c3fc1f")}</p>
      </div>
      {error && <Notice tone="bad" title={uiText("features.registry.RegistryPage.aktion_fehlgeschlagen_3a20cc")}>{error}</Notice>}
      <Panel>
        {drafts.isPending ? (
          <Spinner />
        ) : list.length === 0 ? (
          <Empty title={uiText("features.registry.RegistryPage.noch_keine_drafts_a1dbb9")}>
            {completed ? uiText('beta3.a32d6f4f66ce') : uiText('beta3.0616d1ac83b2')}</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {list.map((draft) => (
              <li key={draft.draft_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 font-medium">
                    <Link className="truncate hover:text-fg" to={`/changes/drafts/${encodeURIComponent(draft.draft_id)}`}>{draftLabel(draft)}</Link>
                    {doc.draft?.draft_id === draft.draft_id && <Badge tone="accent">{doc.dirty ? uiText("features.registry.RegistryPage.geoffnet_ungespeichert_62ba6c") : uiText("features.registry.RegistryPage.geoffnet_ec5e95")}</Badge>}
                  </p>
                  <Collapsible title={uiText('alpha7.draft.advanced')}><p className="mono break-all text-xs text-muted">{draft.draft_id}</p></Collapsible>
                  <p className="text-sm">{draftState(draft, active.data?.revision ?? 0)}</p><p className="text-xs text-muted">{uiText("features.registry.RegistryPage.v_4c9448")}{draft.draft_version} {uiText("features.registry.RegistryPage.basis_revision_690542")}{draft.base_revision ?? '–'} {uiText("features.registry.RegistryPage.erstellt_555e13")}{formatTime(draft.created_at)} {uiText("features.registry.RegistryPage.geandert_4147a3")}{formatTime(draft.updated_at)}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" onClick={() => open(draft)}><Pencil size={13} /> {completed ? uiText('beta3.8e31363947a6') : currentDraft(draft, active.data?.revision ?? 0) ? uiText('beta3.84e45ee73411') : uiText('beta3.98040289d15a')}</Button>
                  <Button size="sm" onClick={() => setCopyOf(draft)}><Copy size={13} /> {uiText("features.registry.RegistryPage.duplizieren_9d400c")}</Button>
                  <Button size="sm" disabled={doc.draft?.draft_id === draft.draft_id && doc.dirty} onClick={() => void downloadDraft(draft, active.data?.revision ?? 0, draftLabel(draft)).catch(failure => setError(describeError(failure)))}><Download size={13} /> {uiText("features.registry.RegistryPage.export_366489")}</Button>
                  <Button size="sm" variant="danger" disabled={completed} onClick={() => setDeleting(draft)}><Trash2 size={13} /> {uiText("features.registry.RegistryPage.loschen_6c2d35")}</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <CreateDraftDialog open={creating} onOpenChange={setCreating} onCreated={(d) => navigate(`/changes/drafts/${encodeURIComponent(d.draft_id)}`)} />
      {copyOf && <CreateDraftDialog open onOpenChange={(o) => !o && setCopyOf(null)} copyOf={{ config: copyOf.config, name: draftLabel(copyOf) }} />}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={uiText("features.registry.RegistryPage.draft_loschen_4ca056")}
        description={<>„{deleting ? draftLabel(deleting) : ''}{uiText("features.registry.RegistryPage.wird_endgultig_geloscht_version_v_5065b7")}{deleting?.draft_version}{uiText("features.registry.RegistryPage.die_aktive_registry_bleibt_unberuhrt_cf60a6")}</>}
        confirmLabel={uiText("features.registry.RegistryPage.endgultig_loschen_6c1fd3")}
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => {
          const target = deleting;
          if (!target) return;
          setError('');
          remove.mutate(target, {
            onSuccess: () => {
              if (doc.draft?.draft_id === target.draft_id) doc.close();
              setDeleting(null);
            },
            onError: (e) => {
              setError(describeError(e));
              setDeleting(null);
            },
          });
        }}
      />
    </div></>
  );
}

function RevisionsTab() {
  const revisions = useRevisions();
  const active = useActiveRevision();
  const activations = useHistory('activations', { limit: 200 });
  const audit = useHistory('diagnostics', { limit: 200 });
  const live = useLive();
  const [viewing, setViewing] = useState<RevisionRow | null>(null);
  const [rolling, setRolling] = useState<RevisionRow | null>(null);
  const [copyOf, setCopyOf] = useState<RevisionRow | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [comparing, setComparing] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const navigate = useNavigate();
  const revisionQuery = useLocation().search;
  useEffect(() => { const number = Number(new URLSearchParams(revisionQuery).get('revision')); const row = revisions.data?.find(item => item.revision === number); if (row) setViewing(row); else if (!revisionQuery) setViewing(null); }, [revisionQuery, revisions.data]);
  const operations = useMemo(() => new Map((activations.data?.pages.flatMap((p) => p.items) ?? []).map((a) => [Number(a.revision), String(a.operation)])), [activations.data]);
  const rows = [...(revisions.data ?? [])].sort((a, b) => b.revision - a.revision);
  const activeNumber = active.data?.revision ?? 0;
  const activeKnown = active.isSuccess && (live.registryRevision === null || live.registryRevision === activeNumber) && rows.every((row) => row.revision <= activeNumber);
  const actors = new Map((audit.data?.pages.flatMap((page) => page.items) ?? []).filter((event) => event.operation === 'activate' || event.operation === 'rollback').map((event) => [Number(asObject(event.details).revision ?? event.revision), asObject(event.actor)]));
  const pair = selected.length === 2 ? selected.map((n) => rows.find((r) => r.revision === n)!).sort((a, b) => a.revision - b.revision) : null;

  const header = <PageHeader title={uiText('alpha7.nav.versions')} description={uiText('alpha9.pages.versions')} actions={<Button variant="primary" disabled={revisions.isPending || rows.length < 2} disabledReason={uiText('alpha9.versions.needTwo')} onClick={() => pair ? setComparing(true) : setSelecting(true)}><GitCompare size={15} />{uiText('alpha9.actions.compare')}</Button>} />;
  if (revisions.isPending) return <>{header}<Spinner /></>;
  return (
    <>{header}
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">{uiText("features.registry.RegistryPage.revisionen_sind_unveranderlich_aktivierung_und_r_45199a")}</p>
        <div className="space-y-1"><Button disabled={selected.length !== 2} onClick={() => setComparing(true)}><GitCompare size={14} /> {uiText("features.registry.RegistryPage.zwei_revisionen_vergleichen_ebd968")}{selected.length}/2)</Button>{selected.length !== 2 && <p className="text-xs text-muted">{uiText('alpha7.versions.selectTwo')}</p>}</div>
      </div>
      {!activeKnown && rows.length > 0 && <p role="status" className="text-sm text-muted">{uiText(active.isError ? 'alpha7.versions.activeFailed' : 'alpha7.versions.activePending')}</p>}
      <Panel>
        {rows.length === 0 ? (
          <Empty title={uiText("features.registry.RegistryPage.noch_keine_revision_bf7c3f")}>{uiText("features.registry.RegistryPage.es_wurde_noch_nie_eine_registry_aktiviert_66d0b4")}</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((row) => {
              const isActive = activeKnown && activeNumber === row.revision;
              const op = operations.get(row.revision);
              const actor = row.actor ?? actors.get(row.revision);
              const actorLabel = actor?.kind === 'internal' ? uiText('alpha7.versions.actorSystem') : actor?.role === 'admin' && (actor.kind === 'token' || actor.kind === 'ingress') ? uiText(actor.kind === 'token' ? 'alpha7.versions.actorToken' : 'alpha7.versions.actorApp') : uiText('alpha7.versions.actorUnknown');
              return (
                <li key={row.revision} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                  <input type="checkbox" aria-label={uiText("features.registry.RegistryPage.revision_v0_zum_vergleich_wahlen_d77c8e", { v0: (row.revision) })} className="size-4 accent-[var(--color-fg)]" checked={selected.includes(row.revision)} onChange={(e) => setSelected((s) => (e.target.checked ? [...s, row.revision].slice(-2) : s.filter((n) => n !== row.revision)))} />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 font-medium">
                      {uiText("features.registry.RegistryPage.revision_e330d8")}{row.revision}
                      {isActive && <Badge tone="info">{uiText("features.registry.RegistryPage.aktiv_3139fc")}</Badge>}
                      {op && <Badge>{op === 'rollback' ? uiText("features.registry.RegistryPage.rollback_c591f5") : uiText("features.registry.RegistryPage.aktivierung_90ba1a")}</Badge>}
                    </p>
                    <p className="text-xs text-muted">{formatTime(row.created_at)} · {actorLabel}</p>
                    {row.note && <p className="mt-1 text-sm">{row.note}</p>}
                    <Collapsible title={uiText('alpha7.versions.advanced')}><p className="mb-2 text-sm text-muted">{uiText('alpha10.checksumHelp')}</p><p className="mono break-all text-xs text-muted">{row.checksum}</p></Collapsible>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button size="sm" onClick={() => setViewing(row)}><History size={13} /> {uiText("features.registry.RegistryPage.ansehen_8e3136")}</Button>
                    <Button size="sm" onClick={() => downloadText(`registry-rev-${row.revision}.json`, exportRegistry(row.config))}><Download size={13} /> {uiText('b20.export.raw')}</Button>
                    <Button size="sm" onClick={() => setCopyOf(row)}><FolderOpen size={13} /> {uiText("features.registry.RegistryPage.als_draft_051157")}</Button>
                    {activeKnown && row.revision < activeNumber && <Button size="sm" variant="danger" onClick={() => setRolling(row)}><RotateCcw size={13} /> {uiText("features.registry.RegistryPage.zuruckrollen_1c5e6f")}</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
      {viewing && (
        <Dialog open onOpenChange={(o) => { if (!o) { setViewing(null); navigate('/changes/versions', { replace: true }); } }} size="xl" title={uiText("features.registry.RegistryPage.revision_v0_5db9ad", { v0: (viewing.revision) })}>
          <Collapsible title={uiText('alpha7.versions.advanced')}><p className="mb-2 text-sm text-muted">{uiText('alpha10.checksumHelp')}</p><p className="mono break-all text-xs">{viewing.checksum}</p></Collapsible>
          <ConfigurationContent config={viewing.config} />
          <Collapsible title={uiText('alpha10.technical')}><pre className="max-h-[60vh] overflow-auto rounded-[var(--radius-md)] bg-sunken p-3 text-xs">{canonicalJson(viewing.config)}</pre></Collapsible>
        </Dialog>
      )}
      {rolling && <RollbackDialog key={rolling.revision} revision={rolling} open onOpenChange={(o) => !o && setRolling(null)} />}
      {copyOf && <CreateDraftDialog open onOpenChange={(o) => !o && setCopyOf(null)} copyOf={{ config: copyOf.config, name: uiText('alpha7.versions.name', { n: copyOf.revision }) }} onCreated={(d) => navigate(`/changes/drafts/${encodeURIComponent(d.draft_id)}`)} />}
      {comparing && pair && (
        <Dialog open onOpenChange={(o) => !o && setComparing(false)} size="lg" title={uiText("features.registry.RegistryPage.revision_v0_v1_1a2b5a", { v0: (pair[0]!.revision), v1: (pair[1]!.revision) })} description={uiText("features.registry.RegistryPage.strukturelle_differenz_der_konfigurationsdokumen_aee6e2")}>
          {(() => {
            const paths = changedPaths(pair[0]!.config, pair[1]!.config);
            return paths.length === 0 ? (
              <Notice tone="info" title={uiText("features.registry.RegistryPage.inhaltlich_identisch_8109af")} />
            ) : (
              <ul className="mono max-h-[55vh] overflow-auto text-xs">
                {paths.map((p) => <li key={p} className="border-b border-line/60 py-1">{p}</li>)}
              </ul>
            );
          })()}
        </Dialog>
      )}
      <Dialog open={selecting} onOpenChange={setSelecting} title={uiText('alpha9.actions.compare')} description={uiText('alpha7.versions.selectTwo')} footer={<Button variant="primary" disabled={selected.length !== 2} onClick={() => { setSelecting(false); setComparing(true); }}>{uiText('alpha9.actions.compare')}</Button>}>
        <ul className="space-y-3">{rows.map(row => <li key={row.revision}><label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={selected.includes(row.revision)} onChange={event => setSelected(current => event.target.checked ? [...current, row.revision].slice(-2) : current.filter(number => number !== row.revision))} />{uiText('alpha7.versions.name', { n: row.revision })}</label></li>)}</ul>
      </Dialog>
    </div></>
  );
}

export default function RegistryPage() {
  const versions = useLocation().pathname === '/changes/versions';
  return (
    <>
      {versions ? <RevisionsTab /> : <DraftsTab />}
    </>
  );
}
