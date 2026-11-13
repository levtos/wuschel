import { t as uiText } from '@/i18n';
import { connectionLabel } from '@/lib/status';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ContractLiveState } from '@/components/ContractLiveState';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { QualityBadge } from '@/components/status-parts';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Tabs } from '@/components/ui/controls';
import { Field, Select } from '@/components/ui/form';
import { Empty, KeyValue, Notice, PageHeader, Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { formatTime, shortId } from '@/lib/format';
import { useLive } from '@/lib/live-context';
import { useDiagnostics, useHistory } from '@/lib/queries';
import { CommandsPanel } from './CommandsPanel';
import { ContractDiagnosis } from './ContractDiagnosis';
import { EvidenceTab } from './EvidenceLookup';
import { HistoryTab, HistoryTable } from './HistoryTab';
import { MachineRestartPanel } from '@/features/contracts/MachineRestartPanel';

function ContractPicker({ value, onChange, allowNone }: { value: string; onChange: (id: string) => void; allowNone?: boolean }) {
  const live = useLive();
  const ids = Object.keys(live.contracts ?? {}).sort();
  return (
    <Field label={uiText("features.diagnostics.DiagnosticsPage.contract_a6f5b0")} className="max-w-md">
      {(p) => (
        <Select {...p} value={value} onChange={(e) => onChange(e.target.value)}>
          {allowNone && <option value="">{uiText("features.diagnostics.DiagnosticsPage.wahlen_602246")}</option>}
          {value && !ids.includes(value) && <option value={value}>{value}</option>}
          {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        </Select>
      )}
    </Field>
  );
}

function StateTab({ contractId, onContract }: { contractId: string; onContract: (id: string) => void }) {
  const live = useLive();
  const envelope = live.contracts?.[contractId] ?? null;
  const ids = Object.keys(live.contracts ?? {});
  if (live.contracts === null) return live.connection === 'open' || live.connection === 'connecting' ? <Spinner label={uiText("features.diagnostics.DiagnosticsPage.snapshot_wird_erwartet_8f2bea")} /> : <Notice tone="warn" title={uiText("features.diagnostics.DiagnosticsPage.noch_kein_live_zustand_d31d85")}>{uiText("features.diagnostics.DiagnosticsPage.es_wurde_noch_kein_snapshot_empfangen_ec5820")}{connectionLabel(live.connection)}).</Notice>;
  if (ids.length === 0) return <Empty title={uiText("features.diagnostics.DiagnosticsPage.keine_aktiven_contracts_ec85c7")}>{uiText("features.diagnostics.DiagnosticsPage.die_aktive_registry_enthalt_keine_aktivierten_co_ee9568")}</Empty>;
  return (
    <div className="flex flex-col gap-4">
      <ContractPicker value={contractId} onChange={onContract} allowNone />
      {envelope ? <ContractLiveState envelope={envelope} stale={live.stale} /> : <p className="text-sm text-muted">{uiText("features.diagnostics.DiagnosticsPage.contract_wahlen_um_felder_quality_reasons_und_ev_31617c")}</p>}
    </div>
  );
}

function PublicationsTab() {
  const live = useLive();
  const diagnostics = useDiagnostics(10_000);
  const publications = useHistory('contracts', { limit: 50 });
  const gaps = useHistory('gaps', { limit: 50 });
  const epochs = useMemo(() => {
    const seen = new Map<string, { first: string; last: string; maxSeq: number }>();
    for (const item of publications.data?.pages.flatMap((p) => p.items) ?? []) {
      const epoch = typeof item.epoch_id === 'string' ? item.epoch_id : null;
      const at = typeof item.published_at === 'string' ? item.published_at : typeof item.at === 'string' ? item.at : '';
      if (!epoch) continue;
      const prev = seen.get(epoch);
      const seq = Number(item.publication_seq ?? 0);
      seen.set(epoch, { first: prev && prev.first < at ? prev.first : at, last: prev && prev.last > at ? prev.last : at, maxSeq: Math.max(prev?.maxSeq ?? 0, seq) });
    }
    return [...seen.entries()];
  }, [publications.data]);
  return (
    <PanelLayout view="diagnostics.publications"><LayoutPanel id="current" title={uiText("features.diagnostics.DiagnosticsPage.aktuelle_veroffentlichung_38f9d7")}><Panel>
        <PanelHeader title={uiText("features.diagnostics.DiagnosticsPage.aktuelle_veroffentlichung_38f9d7")} />
        <div className="p-4">
          <KeyValue
            items={[
              { label: uiText("features.diagnostics.DiagnosticsPage.epoche_8a2707"), value: <code>{shortId(live.epochId ?? diagnostics.data?.epoch_id ?? '–', 16)}</code> },
              { label: uiText("features.diagnostics.DiagnosticsPage.publication_sequenz_48deb8"), value: live.publicationSeq ?? diagnostics.data?.publication_seq ?? '–' },
              { label: uiText("features.diagnostics.DiagnosticsPage.registry_revision_70bc45"), value: live.registryRevision ?? '–' },
              { label: uiText("features.diagnostics.DiagnosticsPage.bestatigt_61794f"), value: live.publicationConfirmed === null ? '–' : live.publicationConfirmed ? uiText("features.diagnostics.DiagnosticsPage.ja_3702fc") : <Badge tone="warn">{uiText("features.diagnostics.DiagnosticsPage.nein_4d03db")}</Badge> },
              { label: uiText("features.diagnostics.DiagnosticsPage.neu_synchronisiert_073e42"), value: uiText("features.diagnostics.DiagnosticsPage.v0_460783", { v0: (live.resyncCount) }) },
            ]}
          />
        </div>
      </Panel>
      </LayoutPanel><LayoutPanel id="epochs" title={uiText("features.diagnostics.DiagnosticsPage.epochen_570ce9")}><Panel>
        <PanelHeader title={uiText("features.diagnostics.DiagnosticsPage.epochen_570ce9")} description={uiText("features.diagnostics.DiagnosticsPage.aus_den_geladenen_historienzeilen_abgeleitet_ein_8d83d0")} />
        <div className="p-2">
          {epochs.length === 0 ? <p className="p-3 text-sm text-muted">{uiText("features.diagnostics.DiagnosticsPage.keine_epochen_in_den_geladenen_zeilen_2208ad")}</p> : (
            <ul className="divide-y divide-line">
              {epochs.map(([id, e]) => <li key={id} className="flex flex-wrap items-center gap-3 px-2 py-2 text-sm"><code className="text-xs">{shortId(id, 16)}</code><span className="text-xs text-muted">{formatTime(e.first)} – {formatTime(e.last)}</span><Badge>{uiText("features.diagnostics.DiagnosticsPage.max_seq_384fa8")}{e.maxSeq}</Badge></li>)}
            </ul>
          )}
        </div>
      </Panel>
      </LayoutPanel><LayoutPanel id="gaps" title={uiText("features.diagnostics.DiagnosticsPage.lucken_3bdb09")}><Panel>
        <PanelHeader title={uiText("features.diagnostics.DiagnosticsPage.lucken_history_gaps_6d2e41")} description={uiText("features.diagnostics.DiagnosticsPage.unterbrechungen_der_beobachtungs_kontinuitat_bet_cb4453")} />
        <div className="p-4"><HistoryTable kind="gaps" filters={{ limit: 50 }} /></div>
        {gaps.isSuccess && diagnostics.data && diagnostics.data.gaps.length > 0 && <p className="border-t border-line px-4 py-2 text-xs text-muted">{uiText("features.diagnostics.DiagnosticsPage.aktuelle_diagnose_meldet_fa6c61")}{diagnostics.data.gaps.length} {uiText("features.diagnostics.DiagnosticsPage.lucke_n_im_laufzeitspeicher_c38399")}</p>}
      </Panel>
      </LayoutPanel><LayoutPanel id="publications" title={uiText("features.diagnostics.DiagnosticsPage.veroffentlichungen_2f573d")}><Panel>
        <PanelHeader title={uiText("features.diagnostics.DiagnosticsPage.veroffentlichungen_2f573d")} description={uiText("features.diagnostics.DiagnosticsPage.persistierte_contract_zustande_mit_epoche_und_se_30b8f1")} />
        <div className="p-4">
          <HistoryTable
            kind="contracts"
            filters={{ limit: 50 }}
            columns={[
              { header: uiText("features.diagnostics.DiagnosticsPage.contract_a6f5b0"), render: (i) => <code>{String(i.contract_id ?? '–')}</code> },
              { header: uiText("features.diagnostics.DiagnosticsPage.epoche_8a2707"), render: (i) => <code>{shortId(String(i.epoch_id ?? '–'), 10)}</code> },
              { header: uiText("features.diagnostics.DiagnosticsPage.seq_43ac26"), render: (i) => String(i.publication_seq ?? '–') },
              { header: uiText("features.diagnostics.DiagnosticsPage.revision_2e516d"), render: (i) => String(i.registry_revision ?? '–') },
              { header: uiText("features.diagnostics.DiagnosticsPage.gesundheit_1c7f2f"), render: (i) => (i.contract_health === 'healthy' || i.contract_health === 'degraded' ? <QualityBadge quality={i.contract_health} /> : '–') },
            ]}
          />
        </div>
      </Panel>
    </LayoutPanel></PanelLayout>
  );
}

export default function DiagnosticsPage() {
  const live = useLive();
  const [choosing, setChoosing] = useState(false); const [chosen, setChosen] = useState('');
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') ?? 'state';
  const contract = params.get('contract') ?? '';
  const set = (next: Record<string, string>) => {
    const merged = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) (v ? merged.set(k, v) : merged.delete(k));
    setParams(merged, { replace: true });
  };
  return (
    <>
      <PageHeader title={uiText("features.diagnostics.DiagnosticsPage.diagnose_historie_63b2ee")} description={uiText("features.diagnostics.DiagnosticsPage.was_der_server_tatsachlich_wei_werte_quality_rea_24aab3")} actions={<Button variant="primary" disabled={!live.contracts || Object.keys(live.contracts).length === 0} disabledReason={uiText('features.diagnostics.DiagnosticsPage.die_aktive_registry_enthalt_keine_aktivierten_co_ee9568')} onClick={() => { setChosen(contract); setChoosing(true); }}>{uiText('alpha9.actions.chooseContract')}</Button>} />
      <Dialog open={choosing} onOpenChange={setChoosing} title={uiText('alpha9.actions.chooseContract')} footer={<Button variant="primary" disabled={!chosen} onClick={() => { set({ contract: chosen }); setChoosing(false); }}>{uiText('alpha9.actions.viewContract')}</Button>}><ContractPicker value={chosen} onChange={setChosen} allowNone /></Dialog>
      <Link to="/settings/diagnostics/system-state" className="mb-4 inline-flex min-h-11 items-center text-sm text-accent underline">{uiText('b26.system.title')}</Link>
      <Tabs
        value={tab}
        onValueChange={(t) => set({ tab: t })}
        items={[
          { value: 'state', label: uiText("features.diagnostics.DiagnosticsPage.live_zustand_39e6cc"), content: <StateTab contractId={contract} onContract={(id) => set({ contract: id })} /> },
          {
            value: 'diagnosis',
            label: uiText("features.diagnostics.DiagnosticsPage.contract_diagnose_314975"),
            content: (
              <div className="flex flex-col gap-4">
                <ContractPicker value={contract} onChange={(id) => set({ contract: id })} allowNone />
                {contract ? <ContractDiagnosis contractId={contract} /> : <p className="text-sm text-muted">{uiText("features.diagnostics.DiagnosticsPage.contract_wahlen_fur_explain_temporal_restore_und_bc22e6")}</p>}
              </div>
            ),
          },
          { value: 'history', label: uiText("features.diagnostics.DiagnosticsPage.historie_4eaa80"), content: <HistoryTab key={contract} initialContract={contract} /> },
          { value: 'publications', label: uiText("features.diagnostics.DiagnosticsPage.publikationen_lucken_44dc70"), content: <PublicationsTab /> },
          { value: 'evidence', label: uiText("features.diagnostics.DiagnosticsPage.evidence_03867a"), content: <EvidenceTab /> },
          { value: 'commands', label: uiText("features.diagnostics.DiagnosticsPage.commands_b269dc"), content: <div className="space-y-4"><CommandsPanel contractId={contract} onContract={(id) => set({ contract: id })} />{contract && <MachineRestartPanel key={contract} contractId={contract} />}</div> },
        ]}
      />
    </>
  );
}
