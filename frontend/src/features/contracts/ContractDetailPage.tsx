import { PageLoading } from '@/components/PageLoading';
import { t as uiText } from '@/i18n';
import { Stethoscope, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs } from '@/components/ui/controls';
import { ConfirmDialog } from '@/components/ui/dialog';
import { Empty, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { ContractEditor } from '@/editors/RegistryObjects';
import { ReadOnlyNotice } from '@/features/authoring/DraftBar';
import { asArray, asObject, fieldCompatible, str } from '@/features/authoring/model';
import { refOptions, typeCatalog, typeKey } from '@/features/authoring/catalog';
import { consumersOfContract } from '@/features/authoring/usage';
import { contractHref, ObjectReadBack, ObjectReadLink } from '@/features/authoring/ObjectReadLinks';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { DraftEditor, EffectStrip } from '@/features/authoring/DraftEditor';
import { clone, deepEqual, isObject, setAt, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { useLive } from '@/lib/live-context';
import { useActiveRevision, useCapabilities } from '@/lib/queries';
import { contractSources, contractTitle, fieldsForContract } from './presentation';
import { ContractSummary } from './ContractSummary';
import { DeviceWizard } from '@/features/sources/DeviceWizard';
import { EditorialPanel } from '@/features/editorial/Editorial';
import { ContractExport } from '@/features/registry/ContractExport';
import { OpeningHeartbeatSettings } from './OpeningHeartbeatSettings';
import { effectiveRule, ownRule } from '@/features/sources/freshness';
import { ObjectActions } from '@/features/authoring/ObjectActions';
import { WakePlanningDialog } from './WakePlanningDialog';
import { ContractTemplateLink, InputReadLinks } from './ContractReadLinks';
import { fieldLabel } from '@/i18n/codes';
import { ContractParameters } from './ContractParameters';

export default function ContractDetailPage() {
  const { contractId = '' } = useParams();
  const src = useAuthoringSource();
  const doc = useWorkingDocument();
  const live = useLive();
  const active = useActiveRevision();
  const caps = useCapabilities();
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [removing, setRemoving] = useState(false);
  const [deviceInput, setDeviceInput] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ base: Registry; config: Registry } | null>(null);
  const [wakeBase, setWakeBase] = useState<Registry | null>(null);
  const requestedEdit = useRef(false);
  const displayConfig = editor?.base ?? wakeBase ?? src.config;
  const list = asArray(displayConfig?.contracts).filter(isObject);
  const index = list.findIndex((c) => str(c.contract_id) === contractId);
  const contract = index >= 0 ? list[index]! : null;
  const activeContract = asArray(active.data?.config?.contracts).filter(isObject).find(item => item.contract_id === contractId);
  const draftOnly = src.source === 'draft' && active.data !== undefined && !activeContract;
  const candidate = live.contracts?.[contractId];
  const envelope = activeContract && candidate && candidate.registry_revision === active.data?.revision ? candidate : null;

  const beginEdit = () => {
    if (!src.config || !contract) return;
    setEditor({ base: src.config, config: clone(src.config) });
    setParams(previous => { const next = new URLSearchParams(previous); next.set('tab', 'config'); next.delete('edit'); return next; }, { replace: true, state: location.state });
  };
  useEffect(() => { setEditor(null); setWakeBase(null); setDeviceInput(null); }, [contractId]);
  useEffect(() => {
    if (params.get('edit') !== '1') { requestedEdit.current = false; return; }
    if (contract && !requestedEdit.current) { requestedEdit.current = true; void beginEdit(); }
  }, [params, !!contract]);

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
  const apply = (update: (config: Registry) => Registry) => setEditor(current => current && { ...current, config: update(current.config) });
  const editingIndex = asArray(editor?.config.contracts).findIndex(row => isObject(row) && row.contract_id === contractId);
  const editingPointer = `/contracts/${editingIndex}`;
  const editingContract = editingIndex >= 0 ? asObject(asArray(editor?.config.contracts)[editingIndex]) : null;
  const editTypes = typeCatalog(caps.data, editor?.config);
  const refs = editor ? refOptions(editor.config, editTypes, contractId) : [];
  const typeEntry = src.types.find((t) => t.key === typeKey(str(contract.type_id), Number(contract.type_version)));
  const editingType = editTypes.find(t => t.key === typeKey(str(editingContract?.type_id), Number(editingContract?.type_version)));
  const consumers = consumersOfContract(src.config, contractId);
  const inputs = asArray(contract.inputs).filter(isObject);
  const alternatives = !editor ? [] : editTypes.filter(candidate => candidate.key === editingType?.key || asArray(editingContract?.inputs).filter(isObject).every(input => {
    const declared = candidate.inputs.find(slot => slot.name === input.name);
    const schema = refs.find(ref => ref.value === input.ref)?.schema;
    return declared && (!schema || !declared.schema || fieldCompatible(schema, declared.schema));
  }));
  const bound = contractSources(contractId, src.config, []);
  const inputRows = [...new Set([...(typeEntry?.inputs.map(input => input.name) ?? []), ...inputs.map(input => str(input.name))])];
  const readInputs = <ul className="divide-y divide-line">
    {inputRows.length === 0 && <li className="px-4 py-3 text-sm text-muted">{uiText('features.contracts.ContractDetailPage.keine_eingange_gebunden_b03857')}</li>}
    {inputRows.map(name => {
      const declaration = typeEntry?.inputs.find(input => input.name === name);
      const input = bound.find(item => item.inputName === name);
      return <li key={name} className="space-y-1 px-4 py-3 text-sm">
        <div className="flex flex-wrap items-center gap-x-2">
          <span className="font-medium">{fieldLabel(name)}</span>
          {declaration && <Badge tone={declaration.required ? 'accent' : 'neutral'}>{uiText(declaration.required ? 'editors.RegistryObjects.pflicht_6a047f' : 'editors.RegistryObjects.optional_ec91fd')}</Badge>}
        </div>
        {input ? <><p className="text-muted">{input.name}</p><InputReadLinks input={input} config={src.config} /></> : <p className="text-muted">{uiText('editors.RegistryObjects.nicht_gebunden_4105e9')}</p>}
      </li>;
    })}
  </ul>;
  const refreshBindings = asArray(editor?.config.bindings).filter(isObject);
  const refreshSources = asArray(editor?.config.sources).filter(isObject).map(source => {
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
      <ObjectReadBack fallback={{ to: '/contracts/list/all', label: uiText('features.contracts.ContractDetailPage.alle_contracts_67b49f') }} />
      <PageHeader
        title={<span className="block truncate" title={contractId}>{str(contract.display_name) || contractId}</span>}
        description={uiText('alpha9.pages.contract')}
        actions={
          <>
            {!editor && <ObjectActions key={contractId} collection="contracts" idKey="contract_id" value={contract} editing={false} onEdit={beginEdit} primary />}
            {asObject(typeEntry?.parameterSchema?.properties).wake_settings && <Button disabled={doc.starting || doc.saving} onClick={() => setWakeBase(src.config)}>{uiText('b22.wake.edit')}</Button>}
            {draftOnly ? <Badge tone="info">{uiText('alpha7.contract.state.draft')}</Badge> : <Badge>{enabled ? uiText("features.contracts.ContractDetailPage.aktiviert_acb27b") : uiText("features.contracts.ContractDetailPage.deaktiviert_10f33e")}</Badge>}
            <ContractExport contractId={contractId} source={src.source === 'draft' ? 'draft' : 'active'} />
            <Button size="sm" onClick={() => navigate(`/settings/diagnostics?contract=${encodeURIComponent(contractId)}`)}><Stethoscope size={14} /> {uiText("features.contracts.ContractDetailPage.diagnose_64dd65")}</Button>
          </>
        }
      />
      {wakeBase && typeEntry?.parameterSchema && <WakePlanningDialog base={wakeBase} contractId={contractId} parameterSchema={typeEntry.parameterSchema} onClose={() => setWakeBase(null)} />}
      <ReadOnlyNotice what={uiText("features.contracts.ContractDetailPage.contract_konfiguration_d1e14f")} />
      <EditorialPanel object={{ kind: 'contract', id: contractId }} />
      <Tabs
        value={params.get('tab') ?? 'state'}
        onValueChange={(tab) => setParams(previous => {
          const next = new URLSearchParams(previous);
          next.set('tab', tab);
          next.delete('focus');
          return next;
        }, { replace: true, state: location.state })}
        items={[
          {
            value: 'state',
            label: uiText("features.contracts.ContractDetailPage.zustand_630d1a"),
            content: draftOnly ? <Empty title={uiText('alpha7.contract.state.draft')}>{uiText('alpha7.contract.draftHelp')}</Empty> : !enabled ? (
              <Empty title={uiText("features.contracts.ContractDetailPage.contract_deaktiviert_f0d824")}>{uiText("features.contracts.ContractDetailPage.deaktivierte_contracts_liefern_keinen_zustand_de_a31528")}</Empty>
            ) : envelope ? (
              <ContractSummary envelope={envelope} stale={live.stale || live.registryRevision !== active.data?.revision} fields={displayFields} config={active.data?.config ?? null} machine={activeType?.machine ?? false} contracts={live.contracts} />
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
              <div className="max-w-4xl space-y-4">
                <Panel><PanelHeader title={uiText('a2.read.template')} aside={<ContractTemplateLink contract={contract} config={src.config} showVersion />} /></Panel>
                <Panel><PanelHeader title={uiText('features.contracts.ContractDetailPage.eingange_53fb39')} />{readInputs}</Panel>
                <Panel className="p-4"><h3 className="mb-3 font-medium">{uiText('a2.parameters.contractTitle')}</h3>{src.source === 'draft' && Object.keys(asObject(typeEntry?.parameterSchema?.properties)).length > 0 && <EffectStrip effect="draft" />}<ContractParameters entry={typeEntry} value={asObject(contract.parameters)} pointer={`${pointer}/parameters`} /></Panel>
              </div>
            ),
          },
          {
            value: 'deps',
            label: uiText("features.contracts.ContractDetailPage.abhangigkeiten_327cb1"),
            content: (
              <PanelLayout view="contract.dependencies"><LayoutPanel id="inputs" title={uiText("features.contracts.ContractDetailPage.eingange_53fb39")} span={2}>
                <Panel>
                  <PanelHeader title={uiText("features.contracts.ContractDetailPage.eingange_hangt_ab_von_24b089")} />
                  {readInputs}
                </Panel>
                </LayoutPanel><LayoutPanel id="consumers" title={uiText("features.contracts.ContractDetailPage.verwendet_von_656221")} span={1}><Panel>
                  <PanelHeader title={uiText("features.contracts.ContractDetailPage.verwendet_von_656221")} description={uiText("features.contracts.ContractDetailPage.contracts_die_diesen_contract_als_eingang_nutzen_763bf2")} />
                  <ul className="divide-y divide-line">
                    {consumers.length === 0 && <li className="px-4 py-3 text-sm text-muted">{uiText("features.contracts.ContractDetailPage.kein_anderer_contract_nutzt_ihn_4582b8")}</li>}
                    {consumers.map((id) => (
                      <li key={id} className="px-4 py-2.5 text-sm"><ObjectReadLink to={contractHref(id)}>{contractTitle(list.find(row => row.contract_id === id), id)}</ObjectReadLink></li>
                    ))}
                  </ul>
                </Panel>
                </LayoutPanel><LayoutPanel id="type" title={uiText("features.contracts.ContractDetailPage.typ_1b0241")}><Panel>
                  <PanelHeader title={uiText('a2.read.template')} />
                  <div className="p-4 text-sm">
                    <ContractTemplateLink contract={contract} config={src.config} showVersion />
                  </div>
                </Panel>
                </LayoutPanel></PanelLayout>
            ),
          },
        ]}
      />
      {editor && <DraftEditor base={editor.base} input={editor.config} dirty={!deepEqual(editor.base, editor.config)} plan={() => ({ config: editor.config })} onClose={() => setEditor(null)} onSaved={() => { if (!editingContract) navigate('/contracts/list/all'); }} size="xl" title={uiText('a2.read.editContract')} footer={editingContract && <>
        <Button size="sm" variant="danger" onClick={() => setRemoving(true)}><Trash2 size={14} /> {uiText('features.contracts.ContractDetailPage.entfernen_382837')}</Button>
        <ObjectActions key={contractId} collection="contracts" idKey="contract_id" value={editingContract} editing onLocalPatch={patch => apply(c => setAt(c, editingPointer, { ...editingContract, ...patch }))} config={editor.config} toggle />
      </>}>
        {editingContract ? <>
          <ContractEditor value={editingContract} pointer={editingPointer} types={alternatives} refs={refs} refreshSources={refreshSources} onChange={next => apply(c => ({ ...setAt(c, editingPointer, next), ...(next.refresh_plan !== undefined ? { config_schema_version: Math.max(2, Number(c.config_schema_version ?? 1)) } : {}) }))} onAddDevice={setDeviceInput} />
          <OpeningHeartbeatSettings config={editor.config} contract={editingContract} onChange={(sourceIndex, interval) => apply(c => { const source = asObject(asArray(c.sources)[sourceIndex]); return setAt(c, `/sources/${sourceIndex}`, ownRule(source, { ...effectiveRule(c, source), interval_s: interval })); })} />
        </> : <p>{uiText('a2.edit.removalPending')}</p>}
      </DraftEditor>}
      {editor && deviceInput && <DeviceWizard open onOpenChange={open => !open && setDeviceInput(null)} contract={{ id: contractId, input: deviceInput }} expectedSchema={editingType?.inputs.find(input => input.name === deviceInput)?.schema} local={{ config: editor.config, onChange: config => apply(() => config) }} />}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={uiText("features.contracts.ContractDetailPage.contract_entfernen_f6bc55")}
        description={<>„{contractId}{uiText("features.contracts.ContractDetailPage.wird_aus_dem_draft_entfernt_contracts_die_ihn_al_932341")}{consumers.length > 0 && <> {uiText("features.contracts.ContractDetailPage.betroffen_c20137")}{consumers.join(', ')}.</>}</>}
        confirmLabel={uiText("features.contracts.ContractDetailPage.entfernen_382837")}
        tone="danger"
        onConfirm={() => {
          apply(c => setAt(c, editingPointer, undefined));
          setRemoving(false);
        }}
      />
    </>
  );
}

export type { JsonObject };
