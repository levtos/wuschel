import { PageLoading } from '@/components/PageLoading';
import { t as uiText } from '@/i18n';
import { ArrowLeft, Stethoscope, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs } from '@/components/ui/controls';
import { ConfirmDialog } from '@/components/ui/dialog';
import { Empty, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { ContractEditor } from '@/editors/RegistryObjects';
import { ReadOnlyNotice } from '@/features/authoring/DraftBar';
import { asArray, asObject, str } from '@/features/authoring/model';
import { refOptions, typeCatalog, typeKey } from '@/features/authoring/catalog';
import { consumersOfContract, resolveRef } from '@/features/authoring/usage';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { isObject, setAt, type JsonObject } from '@/lib/json';
import { useLive } from '@/lib/live-context';
import { useActiveRevision, useCapabilities } from '@/lib/queries';
import { fieldsForContract } from './presentation';
import { ContractSummary } from './ContractSummary';
import { DeviceWizard } from '@/features/sources/DeviceWizard';
import { EditorialPanel } from '@/features/editorial/Editorial';
import { describeError } from '@/lib/api';
import { ContractExport } from '@/features/registry/ContractExport';
import { OpeningHeartbeatSettings } from './OpeningHeartbeatSettings';
import { effectiveRule, ownRule } from '@/features/sources/freshness';
import { ObjectActions } from '@/features/authoring/ObjectActions';
import { WakePlanningDialog } from './WakePlanningDialog';

export default function ContractDetailPage() {
  const { contractId = '' } = useParams();
  const src = useAuthoringSource();
  const doc = useWorkingDocument();
  const live = useLive();
  const active = useActiveRevision();
  const caps = useCapabilities();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [removing, setRemoving] = useState(false);
  const [deviceInput, setDeviceInput] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const [editing, setEditing] = useState(false); const [wakeOpen, setWakeOpen] = useState(false); const requestedEdit = useRef(false);
  const selectedContract = useRef(contractId); selectedContract.current = contractId;
  const previousDraft = useRef(doc.draft?.draft_id);
  useEffect(() => { if (previousDraft.current && previousDraft.current !== doc.draft?.draft_id) { setEditing(false); setWakeOpen(false); } previousDraft.current = doc.draft?.draft_id; }, [doc.draft?.draft_id]);
  const list = asArray(src.config?.contracts).filter(isObject);
  const index = list.findIndex((c) => str(c.contract_id) === contractId);
  const contract = index >= 0 ? list[index]! : null;
  const activeContract = asArray(active.data?.config?.contracts).filter(isObject).find(item => item.contract_id === contractId);
  const draftOnly = src.source === 'draft' && active.data !== undefined && !activeContract;
  const candidate = live.contracts?.[contractId];
  const envelope = activeContract && candidate && candidate.registry_revision === active.data?.revision ? candidate : null;

  const beginEdit = async () => {
    setActionError('');
    try {
      if (editing && src.editable) { if (await doc.save() === 'saved') setEditing(false); return; }
      const current = await doc.ensureDraft();
      if (selectedContract.current !== contractId) return;
      if (!asArray(current.contracts).filter(isObject).some(row => row.contract_id === contractId)) { setActionError(uiText('b22.object.missing')); return; }
      setEditing(true); setParams(previous => { const next = new URLSearchParams(previous); next.set('tab', 'config'); next.delete('edit'); return next; }, { replace: true });
    } catch (failure) { setActionError(describeError(failure)); }
  };
  useEffect(() => {
    if (params.get('edit') !== '1') { requestedEdit.current = false; return; }
    if (contract && !requestedEdit.current) { requestedEdit.current = true; void beginEdit(); }
  }, [params, !!contract]);
  useEffect(() => { setEditing(false); setWakeOpen(false); }, [contractId]);

  if (src.loading && !contract) return <PageLoading title={uiText('alpha7.nav.contracts')} description={uiText('alpha9.pages.contract')} />;

  if (!contract) {
    return (
      <>
        <PageHeader title={uiText('alpha7.nav.contracts')} description={uiText('alpha9.pages.contract')} actions={<Button variant="primary" onClick={() => navigate('/contracts/list/all')}>{uiText('components.RouteError.zur_ubersicht_22bfde')}</Button>} />
        <Empty title={uiText("features.contracts.ContractDetailPage.contract_v0_nicht_gefunden_c09b7a", { v0: (contractId) })}>
          {src.source === 'draft' ? uiText("features.contracts.ContractDetailPage.er_existiert_im_geoffneten_draft_nicht_cb5eb3") : uiText("features.contracts.ContractDetailPage.er_existiert_in_der_aktiven_registry_nicht_25b321")}
          {envelope && uiText("features.contracts.ContractDetailPage.der_server_liefert_dennoch_einen_zustand_dafur_0cfd01")}
        </Empty>
      </>
    );
  }
  const pointer = `/contracts/${index}`;
  const refs = refOptions(src.config, src.types, contractId);
  const typeEntry = src.types.find((t) => t.key === typeKey(str(contract.type_id), Number(contract.type_version)));
  const consumers = consumersOfContract(src.config, contractId);
  const inputs = asArray(contract.inputs).filter(isObject);
  const refreshBindings = asArray(src.config?.bindings).filter(isObject);
  const refreshSources = asArray(src.config?.sources).filter(isObject).map(source => {
    const bindingIndex = refreshBindings.findIndex(binding => binding.source_id === source.source_id);
    const refresh = asObject(refreshBindings[bindingIndex]?.refresh);
    return { id: str(source.source_id), name: str(source.display_name) || str(source.source_id), bindingIndex: bindingIndex >= 0 ? bindingIndex : undefined,
      profile: refresh.profile === 'entity_poll' || refresh.profile === 'companion_location' && refresh.permission_confirmed === true ? str(refresh.profile) : undefined };
  });
  const enabled = contract.enabled !== false;
  const activeTypes = typeCatalog(caps.data, active.data?.config);
  const displayFields = activeContract ? fieldsForContract(activeContract, active.data?.config ?? null, activeTypes) : {};
  const activeType = activeTypes.find(item => item.type_id === activeContract?.type_id && item.type_version === activeContract?.type_version);

  return (
    <>
      <BackLink />
      <PageHeader
        title={<span className="block truncate" title={contractId}>{str(contract.display_name) || contractId}</span>}
        description={uiText('alpha9.pages.contract')}
        actions={
          <>
            <ObjectActions key={contractId} collection="contracts" idKey="contract_id" value={contract} editing={editing && src.editable} onEdit={beginEdit} onSaved={() => setEditing(false)} toggle primary />
            {asObject(typeEntry?.parameterSchema?.properties).wake_settings && <Button disabled={doc.starting || doc.saving} onClick={async () => { setActionError(''); try { const current = await doc.ensureDraft(); if (selectedContract.current !== contractId) return; if (!asArray(current.contracts).filter(isObject).some(row => row.contract_id === contractId)) throw new Error(uiText('b22.object.missing')); setWakeOpen(true); } catch (error) { setActionError(describeError(error)); } }}>{uiText('b22.wake.edit')}</Button>}
            {draftOnly ? <Badge tone="info">{uiText('alpha7.contract.state.draft')}</Badge> : <Badge>{enabled ? uiText("features.contracts.ContractDetailPage.aktiviert_acb27b") : uiText("features.contracts.ContractDetailPage.deaktiviert_10f33e")}</Badge>}
            <ContractExport contractId={contractId} source={src.source === 'draft' ? 'draft' : 'active'} />
            <Button size="sm" onClick={() => navigate(`/settings/diagnostics?contract=${encodeURIComponent(contractId)}`)}><Stethoscope size={14} /> {uiText("features.contracts.ContractDetailPage.diagnose_64dd65")}</Button>
            {src.editable && editing && <Button size="sm" variant="danger" onClick={() => setRemoving(true)}><Trash2 size={14} /> {uiText("features.contracts.ContractDetailPage.entfernen_382837")}</Button>}
          </>
        }
      />
      {wakeOpen && typeEntry?.parameterSchema && <WakePlanningDialog contractId={contractId} parameterSchema={typeEntry.parameterSchema} onClose={() => setWakeOpen(false)} />}
      <ReadOnlyNotice what={uiText("features.contracts.ContractDetailPage.contract_konfiguration_d1e14f")} />
      {actionError && <p role="alert" className="mb-3 text-sm text-bad">{actionError}</p>}
      <EditorialPanel object={{ kind: 'contract', id: contractId }} />
      <Tabs
        value={params.get('tab') ?? 'state'}
        onValueChange={(tab) => setParams({ tab }, { replace: true })}
        items={[
          {
            value: 'state',
            label: uiText("features.contracts.ContractDetailPage.zustand_630d1a"),
            content: draftOnly ? <Empty title={uiText('alpha7.contract.state.draft')}>{uiText('alpha7.contract.draftHelp')}</Empty> : !enabled ? (
              <Empty title={uiText("features.contracts.ContractDetailPage.contract_deaktiviert_f0d824")}>{uiText("features.contracts.ContractDetailPage.deaktivierte_contracts_liefern_keinen_zustand_de_a31528")}</Empty>
            ) : envelope ? (
              <ContractSummary envelope={envelope} stale={live.stale || live.registryRevision !== active.data?.revision} fields={displayFields} config={active.data?.config ?? null} machine={activeType?.machine ?? false} />
            ) : (
              <Empty title={uiText("features.contracts.ContractDetailPage.kein_live_zustand_85efdc")}>
                {live.contracts === null ? uiText("features.contracts.ContractDetailPage.es_wurde_noch_kein_snapshot_empfangen_e56b2e") : uiText("features.contracts.ContractDetailPage.der_aktive_stand_enthalt_diesen_contract_nicht_e_15aef3")}
              </Empty>
            ),
          },
          {
            value: 'config',
            label: uiText("features.contracts.ContractDetailPage.konfiguration_8df29d"),
            content: (
              <Panel className="max-w-4xl p-5">
                <fieldset disabled={!src.editable || !editing} className="min-w-0">
                  <ContractEditor value={contract} pointer={pointer} types={src.types} refs={refs} refreshSources={refreshSources} onChange={(next) => doc.edit((c) => ({ ...setAt(c, pointer, next), ...(next.refresh_plan !== undefined ? { config_schema_version: Math.max(2, Number(c.config_schema_version ?? 1)) } : {}) }))} onAddDevice={setDeviceInput} />
                  <OpeningHeartbeatSettings config={src.config ?? {}} contract={contract} onChange={(sourceIndex, interval) => doc.edit(c => { const source = asObject(asArray(c.sources)[sourceIndex]); return setAt(c, `/sources/${sourceIndex}`, ownRule(source, { ...effectiveRule(c, source), interval_s: interval })); })} />
                </fieldset>
              </Panel>
            ),
          },
          {
            value: 'deps',
            label: uiText("features.contracts.ContractDetailPage.abhangigkeiten_327cb1"),
            content: (
              <PanelLayout view="contract.dependencies"><LayoutPanel id="inputs" title={uiText("features.contracts.ContractDetailPage.eingange_53fb39")} span={2}>
                <Panel>
                  <PanelHeader title={uiText("features.contracts.ContractDetailPage.eingange_hangt_ab_von_24b089")} />
                  <ul className="divide-y divide-line">
                    {inputs.length === 0 && <li className="px-4 py-3 text-sm text-muted">{uiText("features.contracts.ContractDetailPage.keine_eingange_gebunden_b03857")}</li>}
                    {inputs.map((input, i) => {
                      const target = resolveRef(str(input.ref), src.config);
                      return (
                        <li key={i} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
                          <code className="text-muted">{str(input.name)}</code>
                          <span>→</span>
                          {target?.kind === 'contract' ? <Link className="font-mono text-xs text-fg hover:underline" to={`/contracts/view/${encodeURIComponent(target.id)}`}>{str(input.ref)}</Link> : <code className="text-xs">{str(input.ref)}</code>}
                          <Badge tone={target?.kind === 'source' ? 'info' : target ? 'accent' : 'bad'}>{target?.kind === 'source' ? uiText("features.contracts.ContractDetailPage.quelle_037889") : target ? uiText("features.contracts.ContractDetailPage.contract_a6f5b0") : uiText("features.contracts.ContractDetailPage.unbekannt_a418e6")}</Badge>
                        </li>
                      );
                    })}
                  </ul>
                </Panel>
                </LayoutPanel><LayoutPanel id="consumers" title={uiText("features.contracts.ContractDetailPage.verwendet_von_656221")} span={1}><Panel>
                  <PanelHeader title={uiText("features.contracts.ContractDetailPage.verwendet_von_656221")} description={uiText("features.contracts.ContractDetailPage.contracts_die_diesen_contract_als_eingang_nutzen_763bf2")} />
                  <ul className="divide-y divide-line">
                    {consumers.length === 0 && <li className="px-4 py-3 text-sm text-muted">{uiText("features.contracts.ContractDetailPage.kein_anderer_contract_nutzt_ihn_4582b8")}</li>}
                    {consumers.map((id) => (
                      <li key={id} className="px-4 py-2.5 text-sm"><Link className="font-mono text-xs text-fg hover:underline" to={`/contracts/view/${encodeURIComponent(id)}`}>{id}</Link></li>
                    ))}
                  </ul>
                </Panel>
                </LayoutPanel><LayoutPanel id="type" title={uiText("features.contracts.ContractDetailPage.typ_1b0241")}><Panel>
                  <PanelHeader title={uiText("features.contracts.ContractDetailPage.typ_1b0241")} />
                  <div className="p-4 text-sm">
                    {typeEntry?.declarative ? (
                      <>{uiText("features.contracts.ContractDetailPage.deklarative_definition_b0121f")}<Link className="font-mono text-fg hover:underline" to={`/templates/details/${encodeURIComponent(typeEntry.type_id)}/${typeEntry.type_version}`}>{typeEntry.key}</Link></>
                    ) : (
                      <>{uiText("features.contracts.ContractDetailPage.paket_typ_44867a")}<code>{str(contract.type_id)}@{String(contract.type_version)}</code> {uiText("features.contracts.ContractDetailPage.fest_im_code_nicht_uber_die_workbench_anderbar_ee4ae9")}</>
                    )}
                  </div>
                </Panel>
                </LayoutPanel></PanelLayout>
            ),
          },
        ]}
      />
      <DeviceWizard open={deviceInput !== null} onOpenChange={open => !open && setDeviceInput(null)} contract={deviceInput ? { id: contractId, input: deviceInput } : undefined} expectedSchema={typeEntry?.inputs.find(input => input.name === deviceInput)?.schema} />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={uiText("features.contracts.ContractDetailPage.contract_entfernen_f6bc55")}
        description={<>„{contractId}{uiText("features.contracts.ContractDetailPage.wird_aus_dem_draft_entfernt_contracts_die_ihn_al_932341")}{consumers.length > 0 && <> {uiText("features.contracts.ContractDetailPage.betroffen_c20137")}{consumers.join(', ')}.</>}</>}
        confirmLabel={uiText("features.contracts.ContractDetailPage.entfernen_382837")}
        tone="danger"
        onConfirm={() => {
          doc.edit((c) => setAt(c, pointer, undefined));
          setRemoving(false);
          navigate("/contracts/list/all");
        }}
      />
    </>
  );
}

function BackLink() {
  return (
    <Link to="/contracts/list/all" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg">
      <ArrowLeft size={13} /> {uiText("features.contracts.ContractDetailPage.alle_contracts_67b49f")}</Link>
  );
}

export type { JsonObject };
