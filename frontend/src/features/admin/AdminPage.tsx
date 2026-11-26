import { codeLabel } from '@/i18n/codes';
import { t as uiText } from '@/i18n';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router';
import { NAVIGATION } from '@/lib/navigation';
import { Copy, Eye, EyeOff, KeyRound } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { ConditionList } from '@/components/status-parts';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/controls';
import { ConfirmDialog } from '@/components/ui/dialog';
import { KeyValue, Notice, PageHeader, Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { HistoryTable, itemTime } from '@/features/diagnostics/HistoryTab';
import { api, describeError } from '@/lib/api';
import { formatTime, shortId } from '@/lib/format';
import { useLive } from '@/lib/live-context';
import { useActiveRevision, useDiagnostics, useHistory, useInfo, rotateToken } from '@/lib/queries';
import { useOpenSetup } from '@/lib/setup-context';
import { classifySystem, connectionLabel, REASON_LABELS } from '@/lib/status';
import { BRIDGE_LABELS } from '@/setup/SetupWizard';
import { setupRequest } from '@/setup/setup';
import { programVersion } from '@/lib/programVersion';
import type { HaEntityPage, TokenRotation } from '@/lib/types';
import { EventFeed } from '@/features/overview/EventFeed';
import { ContextProducersPanel } from './ContextProducersPanel';

function InstallationPanel() {
  const info = useInfo();
  const active = useActiveRevision();
  const openSetup = useOpenSetup();
  return (
    <Panel>
      <PanelHeader title={uiText("features.admin.AdminPage.installation_einrichtung_342651")} aside={<Button size="sm" onClick={openSetup}>{uiText("features.admin.AdminPage.einrichtungsassistent_offnen_3e1fc4")}</Button>} />
      <div className="p-4">
        {info.isPending ? <Spinner /> : info.data ? (
          <div className="space-y-4">
            <KeyValue items={[
              { label: uiText('alpha9.system.programVersion'), value: programVersion(info.data.version) },
              { label: uiText('features.admin.AdminPage.bezeichnung_97d1e6'), value: info.data.installation_label || '–' },
              { label: uiText('features.admin.AdminPage.aktive_revision_90097f'), value: active.data ? active.data.revision : uiText('features.admin.AdminPage.keine_db028e') },
            ]} />
            <details><summary className="cursor-pointer text-sm text-muted">{uiText('alpha7.contract.advanced')}</summary><div className="mt-3"><KeyValue items={[
              { label: uiText('features.admin.AdminPage.installations_id_fc5797'), value: <code className="break-all text-xs">{info.data.installation_id}</code> },
              { label: uiText('features.admin.AdminPage.protokolle_b4026e'), value: uiText('features.admin.AdminPage.api_v0_bridge_v1_schema_v2_feaecd', { v0: info.data.api_protocol, v1: info.data.bridge_protocol, v2: info.data.config_schema_version }) },
              { label: uiText('features.admin.AdminPage.epoche_8a2707'), value: <code>{shortId(info.data.epoch_id, 16)}</code> },
              { label: uiText('features.admin.AdminPage.serverzeit_utc_123303'), value: formatTime(info.data.server_time) },
            ]} /></div></details>
          </div>
        ) : <Notice tone="bad" title={uiText("features.admin.AdminPage.installationsdaten_nicht_lesbar_5882af")} />}
        <p className="mt-3 text-xs text-muted">{uiText("features.admin.AdminPage.der_assistent_andert_nichts_bevor_sie_ausdruckli_89e424")}</p>
      </div>
    </Panel>
  );
}

function ReadinessPanel() {
  const live = useLive(); const diagnostics = useDiagnostics(); const active = useActiveRevision(); const info = useInfo();
  const service = live.service ?? diagnostics.data?.service ?? null;
  const conditions = classifySystem({ service, connection: live.connection, hasActiveRegistry: active.data === undefined ? undefined : active.data !== null, apiFailure: diagnostics.isError ? 'unavailable' : null });
  const d = diagnostics.data;
  return <Panel id="readiness-details" tabIndex={-1}>
    <PanelHeader title={uiText('features.admin.AdminPage.readiness_komponenten_565aea')} description={uiText('features.admin.AdminPage.bereitschaft_kann_der_dienst_auswerten_ist_etwas_9bcf04')} aside={service && <Badge tone={service.ready ? 'ok' : 'warn'}>{service.ready ? uiText('features.admin.AdminPage.bereit_7f2ffe') : uiText('features.admin.AdminPage.nicht_bereit_05674c')}</Badge>} />
    <div className="space-y-5 p-4">
      <KeyValue items={[{ label: uiText('alpha9.system.programVersion'), value: info.data ? programVersion(info.data.version) : '–' }]} />
      <ConditionList conditions={conditions} />
      <details><summary className="cursor-pointer text-sm text-muted">{uiText('alpha7.contract.advanced')}</summary><div className="mt-4 space-y-4">
        {service && <KeyValue items={[
          { label: uiText('features.admin.AdminPage.persistenz_774960'), value: connectionLabel(service.persistence) },
          { label: uiText('features.admin.AdminPage.home_assistant_2a366a'), value: connectionLabel(service.ha) },
          { label: uiText('features.admin.AdminPage.bridge_3892e1'), value: connectionLabel(service.bridge) },
          { label: uiText('features.admin.AdminPage.mqtt_51b197'), value: connectionLabel(service.mqtt) },
          { label: uiText('features.admin.AdminPage.veroffentlichung_cb618e'), value: service.publication_confirmed ? uiText('features.admin.AdminPage.bestatigt_26bfc3') : uiText('features.admin.AdminPage.unbestatigt_40dee0') },
          { label: uiText('features.admin.AdminPage.offene_grunde_da9e40'), value: service.reasons.length ? service.reasons.map(reason => REASON_LABELS[reason] ?? codeLabel(reason)).join(', ') : uiText('features.admin.AdminPage.keine_db028e') },
        ]} />}
        <KeyValue items={[
          { label: uiText('alpha9.system.bridgeVersionLabel'), value: d?.bridge_version ? uiText('alpha9.system.bridgeVersion', { version: d.bridge_version }) : '–' },
          { label: uiText('features.admin.AdminPage.queue_3b2fe0'), value: d?.queue_size ?? '–' },
          { label: uiText('features.admin.AdminPage.commit_dauer_72559c'), value: d?.commit_duration_s != null ? uiText('features.admin.AdminPage.v0_s_30054e', { v0: d.commit_duration_s.toFixed(3) }) : '–' },
          { label: uiText('features.admin.AdminPage.ingest_uberlaufe_91547b'), value: d?.ingest_overflow_count ?? '–' },
          { label: uiText('features.admin.AdminPage.refresh_uberlaufe_e9c45a'), value: d?.refresh_overflow_count ?? '–' },
        ]} />
        {d && d.dependencies.length > 0 && <div><p className="mb-1 text-xs font-medium text-muted">{uiText('features.admin.AdminPage.dependencies_2e41b1')}</p><ul className="flex flex-wrap gap-1.5">{d.dependencies.map((dep, index) => <li key={index}><Badge>{String(dep.dependency_id)} · {String(dep.kind)}</Badge></li>)}</ul></div>}
      </div></details>
    </div>
  </Panel>;
}

function useDiscovery() { return useMutation({ mutationFn: () => api<HaEntityPage>('ha/entities?limit=1') }); }

function BridgePanel({ discovery }: { discovery: ReturnType<typeof useDiscovery> }) {
  const live = useLive();
  const diagnostics = useDiagnostics(false);
  const bridge = useQuery({ queryKey: ['setup', 'bridge'], queryFn: () => setupRequest<{ state: string }>('bridge'), retry: false });

  const connection = codeLabel(live.connection);
  return (
    <Panel>
      <PanelHeader title={uiText("features.admin.AdminPage.bridge_verbindungsdiagnose_9a2dd6")} />
      <div className="flex flex-col gap-4 p-4">
        <KeyValue items={[
          { label: uiText("features.admin.AdminPage.bridge_zustand_einrichtung_72ab84"), value: bridge.isPending ? uiText("features.admin.AdminPage.wird_gepruft_3242b4") : bridge.isError ? uiText("features.admin.AdminPage.nicht_lesbar_0bfbf9") : BRIDGE_LABELS[bridge.data.state] ?? BRIDGE_LABELS.unknown },
          { label: uiText("features.admin.AdminPage.bridge_version_12d174"), value: String(diagnostics.data?.bridge_version ?? '–') },
          { label: uiText("features.admin.AdminPage.bridge_abonnements_4ce4ee"), value: String(diagnostics.data?.bridge_subscriptions ?? '–') },
          { label: uiText("features.admin.AdminPage.live_websocket_74b350"), value: <Badge tone={live.connection === 'open' ? 'ok' : live.connection === 'mismatch' ? 'bad' : 'warn'}>{connection}</Badge> },
          { label: uiText("features.admin.AdminPage.letztes_ereignis_b141fd"), value: live.lastEventAt ? new Date(live.lastEventAt).toLocaleTimeString('de-DE') : '–' },
          { label: uiText("features.admin.AdminPage.resyncs_634ca6"), value: uiText("features.admin.AdminPage.v0_460783", { v0: (live.resyncCount) }) },
        ]} />
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" disabled={discovery.isPending} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => discovery.mutate()}>{uiText("features.admin.AdminPage.ha_erkennung_testen_nur_lesend_34b8be")}</Button>
          {discovery.isSuccess && <Badge tone="ok">{uiText("features.admin.AdminPage.erreichbar_fb6263")}</Badge>}
          {discovery.isError && <span className="text-xs text-warn">{describeError(discovery.error)}</span>}
        </div>
      </div>
    </Panel>
  );
}

function TokensPanel() {
  const roles = useQuery({ queryKey: ['tokens'], queryFn: () => api<{ roles: string[]; tokens_visible: boolean }>('tokens') });
  const [rotating, setRotating] = useState(false);
  const [rotateError, setRotateError] = useState<unknown>(null);
  const [pending, setPending] = useState<'admin' | 'consumer' | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [result, setResult] = useState<TokenRotation | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);

  // The new credential lives in component state only: never in storage, URLs or the query cache.
  useEffect(() => () => setResult(null), []);
  useEffect(() => setAcknowledged(false), [pending]);

  const run = () => {
    const role = pending;
    if (!role) return;
    setPending(null);
    setRotating(true);
    setRotateError(null);
    // Never retried automatically; the response lives only in this component's state.
    rotateToken(role)
      .then((r) => {
        setResult(r);
        setRevealed(false);
        setCopied(false);
      })
      .catch((error: unknown) => setRotateError(error))
      .finally(() => setRotating(false));
  };
  return (
    <Panel id="access-list" tabIndex={-1}>
      <PanelHeader title={uiText("features.admin.AdminPage.rollen_tokens_9fcf59")} description={uiText("features.admin.AdminPage.tokens_werden_nie_angezeigt_oder_gespeichert_au__7dbaa2")} />
      <div className="flex flex-col gap-4 p-4">
        {roles.isPending ? <Spinner /> : (
          <ul className="flex flex-col gap-2">
            {(roles.data?.roles ?? []).map((role) => (
              <li key={role} className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-md)] border border-line px-3 py-2">
                <span className="flex items-center gap-2 text-sm"><KeyRound size={14} className="text-fg" /> <strong className="font-medium">{codeLabel(role)}</strong></span>
                <Button size="sm" variant="danger" disabled={rotating} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => setPending(role as 'admin' | 'consumer')}>{uiText("features.admin.AdminPage.token_rotieren_ce44a8")}</Button>
              </li>
            ))}
          </ul>
        )}
        {rotateError !== null && (
          <Notice tone="bad" title={uiText("features.admin.AdminPage.rotation_nicht_bestatigt_7a441b")}>
            {describeError(rotateError)} {uiText("features.admin.AdminPage.das_ergebnis_ist_unbestimmt_falls_die_antwort_ve_f7ba98")}<strong>{uiText("features.admin.AdminPage.nicht_automatisch_wiederholen_008a18")}</strong>{uiText("features.admin.AdminPage.zugriff_prufen_und_ggf_uber_die_vertrauenswurdig_5d01cc")}</Notice>
        )}
        {result && (
          <Notice tone={result.audit_status === 'complete' ? 'ok' : 'warn'} title={uiText("features.admin.AdminPage.v0_token_rotiert_376829", { v0: (result.role) })}>
            <p>{uiText("features.admin.AdminPage.neues_token_wird_nur_jetzt_angezeigt_und_nicht_i_f57447")}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input readOnly aria-label={uiText("features.admin.AdminPage.neues_token_f72672")} type={revealed ? 'text' : 'password'} value={result.token} className="h-9 min-w-72 flex-1 rounded-[var(--radius-sm)] border border-line-strong bg-sunken px-2.5 font-mono text-xs" />
              <Button size="sm" onClick={() => setRevealed((v) => !v)}>{revealed ? <EyeOff size={13} /> : <Eye size={13} />} {revealed ? uiText("features.admin.AdminPage.verbergen_a710cb") : uiText("features.admin.AdminPage.anzeigen_ccb27f")}</Button>
              <Button size="sm" onClick={async () => { await navigator.clipboard?.writeText(result.token); setCopied(true); }}><Copy size={13} /> {copied ? uiText("features.admin.AdminPage.kopiert_c2756e") : uiText("features.admin.AdminPage.kopieren_472050")}</Button>
            </div>
            <p className="mt-2 text-xs">{uiText("features.admin.AdminPage.rotations_id_0f1c11")}<code>{result.rotation_id}</code> {uiText("features.admin.AdminPage.audit_2e2f9c")}{result.audit_status === 'complete' ? uiText("features.admin.AdminPage.vollstandig_c1bea4") : uiText("features.admin.AdminPage.abschluss_unbestatigt_token_trotzdem_sichern_90957f")}</p>
            <div className="mt-2"><Button size="sm" variant="primary" onClick={() => { setResult(null); setRevealed(false); setCopied(false); }}>{uiText("features.admin.AdminPage.ich_habe_das_token_gesichert_ausblenden_d2c4e8")}</Button></div>
          </Notice>
        )}
      </div>
      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title={uiText("features.admin.AdminPage.v0_token_rotieren_b5584c", { v0: (codeLabel(pending)) })}
        description={
          <>
            {uiText("features.admin.AdminPage.das_bisherige_token_wird_sofort_ungultig_verbund_973d41")}{pending === 'admin' && uiText("features.admin.AdminPage.beim_admin_token_betrifft_das_alle_administrativ_5abdbf")}
            {uiText("features.admin.AdminPage.es_gibt_keine_automatische_wiederholung_das_neue_ddf90c")}</>
        }
        confirmLabel={uiText("features.admin.AdminPage.jetzt_rotieren_622965")}
        tone="danger"
        confirmDisabled={!acknowledged}
        onConfirm={run}
      >
        <Checkbox checked={acknowledged} onCheckedChange={setAcknowledged} label={uiText("features.admin.AdminPage.ich_habe_verstanden_dass_bestehende_clients_dana_70d245")} />
      </ConfirmDialog>
    </Panel>
  );
}

function AuditPanel() {
  const events = useHistory('diagnostics', { limit: 100 });
  const items = useMemo(() => events.data?.pages.flatMap((p) => p.items) ?? [], [events.data]);
  // A rotation intent without a completion record is an indeterminate outcome, not proof of success.
  const completed = useMemo(() => new Set(items.filter((i) => i.operation === 'token_rotated').map((i) => String(i.rotation_id))), [items]);
  return (
    <Panel>
      <PanelHeader title={uiText("features.admin.AdminPage.audit_verlauf_843625")} description={uiText("features.admin.AdminPage.administrative_operationen_mit_rolle_vertrauensg_2a53f4")} />
      <div className="space-y-5 p-4">
        <EventFeed limit={50} logLink={false} includeActivations />
        <HistoryTable
          kind="diagnostics"
          filters={{ limit: 100 }}
          columns={[
            { header: uiText("features.admin.AdminPage.operation_0f044f"), render: (i) => <span className="flex items-center gap-2"><code>{codeLabel(i.operation ?? i.kind)}</code>{i.operation === 'token_rotation_requested' && !completed.has(String(i.rotation_id)) && <Badge tone="warn">{uiText("features.admin.AdminPage.ausgang_unbestimmt_ef46c4")}</Badge>}</span> },
            { header: uiText("features.admin.AdminPage.akteur_30711f"), render: (i) => { const a = i.actor as { kind?: string; role?: string } | undefined; return a ? `${codeLabel(a.role)} · ${codeLabel(a.kind)}` : '–'; } },
            { header: uiText("features.admin.AdminPage.objekt_fe41f9"), render: (i) => <code>{String(i.draft_id ?? i.rotation_id ?? (i.revision !== undefined ? `Rev. ${String(i.revision)}` : '–'))}</code> },
            { header: uiText("features.admin.AdminPage.revision_2e516d"), render: (i) => String(i.registry_revision ?? '–') },
          ]}
        />
        {items.some((i) => itemTime(i) === null) && <p className="mt-2 text-xs text-faint">{uiText("features.admin.AdminPage.eintrage_ohne_zeitstempel_stammen_aus_alteren_la_516fef")}</p>}
      </div>
    </Panel>
  );
}

export default function AdminPage() {
  const location = useLocation();
  const section = location.pathname.split('/')[2] ?? 'system';
  const title = NAVIGATION.find(module => module.id === 'settings')?.children.find(item => item.to === location.pathname)?.label ?? uiText('alpha7.nav.system');
  const live = useLive();
  const openSetup = useOpenSetup();
  const queryClient = useQueryClient();
  const discovery = useDiscovery();
  const focus = (id: string) => { const target = document.getElementById(id); target?.scrollIntoView?.({ block: 'start' }); target?.focus(); };
  const action = section === 'installation' ? openSetup : section === 'connections' ? () => discovery.mutate() : section === 'keys' ? () => focus('access-list') : section === 'log' ? () => { void queryClient.invalidateQueries({ queryKey: ['history'] }); void queryClient.invalidateQueries({ queryKey: ['events'] }); } : () => focus('readiness-details');
  const actionKey = section === 'installation' ? 'openSetup' : section === 'connections' ? 'checkConnections' : section === 'keys' ? 'chooseAccess' : 'reload';
  return (
    <>
      <PageHeader title={title} description={uiText(section === 'system' ? 'alpha9.system.description' : `alpha9.pages.${section}`)} actions={<Button variant="primary" disabled={section === 'connections' && discovery.isPending} disabledReason={uiText('alpha7.copy.inProgress')} onClick={action}>{uiText(section === 'system' ? 'alpha9.readiness.open' : `alpha9.actions.${actionKey}`)}</Button>} />
      {section === 'system' && <ReadinessPanel />}
      {section === 'installation' && <InstallationPanel />}
      {section === 'connections' && <div className="space-y-5">{live.service?.mqtt_mode === 'disabled' && live.service.mqtt_external_settings_present === true && <Notice tone="warn">{uiText('alpha10.mqtt.disabledHint')}</Notice>}<BridgePanel discovery={discovery} /><Panel><PanelHeader title={uiText('alpha7.settings.databaseMqtt')} /><div className="p-5"><KeyValue items={[
        { label: uiText('alpha7.settings.database'), value: connectionLabel(live.service?.persistence ?? 'unknown') },
        { label: uiText('alpha7.settings.mqtt'), value: connectionLabel(live.service?.mqtt ?? 'unknown') },
      ]} /></div></Panel><ContextProducersPanel /></div>}
      {section === 'keys' && <TokensPanel />}
      {section === 'log' && <AuditPanel />}
    </>
  );
}
