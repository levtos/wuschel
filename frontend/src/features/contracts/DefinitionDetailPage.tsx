import { PageLoading } from '@/components/PageLoading';
import { t as uiText } from '@/i18n';
import { GitBranchPlus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/controls';
import { ConfirmDialog, Dialog } from '@/components/ui/dialog';
import { Empty, Notice, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { DefinitionEditor, LockBadge } from '@/editors/DefinitionEditor';
import { ReadOnlyNotice } from '@/features/authoring/DraftBar';
import { asArray, definitionKey, nextVersionOf, str } from '@/features/authoring/model';
import { contractsUsingDefinition } from '@/features/authoring/usage';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { isObject, setAt } from '@/lib/json';
import { useCapabilities } from '@/lib/queries';
import { ContractsTabs } from './ContractsPage';
import { describeError } from '@/lib/api';
import { EditorialPanel } from '@/features/editorial/Editorial';
import { ObjectReadBack } from '@/features/authoring/ObjectReadLinks';
import { codeLabel, fieldLabel } from '@/i18n/codes';

export default function DefinitionDetailPage() {
  const { typeId = '', version = '' } = useParams();
  const src = useAuthoringSource();
  const doc = useWorkingDocument();
  const caps = useCapabilities();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [versioning, setVersioning] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [migrate, setMigrate] = useState<Set<string>>(new Set());
  const [versionError, setVersionError] = useState('');

  const definitions = asArray(src.config?.definitions).filter(isObject);
  const index = definitions.findIndex((d) => str(d.type_id) === typeId && String(d.type_version) === version);
  const definition = index >= 0 ? definitions[index]! : null;
  const packageType = caps.data?.types.find(type => type.type_id === typeId && String(type.type_version) === version && !type.definition);

  if (!definition && !packageType && (src.loading || caps.isPending)) return <PageLoading title={uiText('alpha7.nav.templates')} description={uiText('alpha9.pages.template')} />;
  if (!definition && packageType) return <>
    <ObjectReadBack fallback={{ to: '/templates/built-in', label: uiText('alpha7.nav.builtinTemplates') }} />
    <PageHeader title={typeId} description={uiText('a2.read.packageTemplate')} actions={<Badge>{uiText('a2.read.version', { version })}</Badge>} />
    <Panel className="max-w-4xl">
      <PanelHeader title={uiText('features.contracts.ContractDetailPage.eingange_53fb39')} />
      <ul className="space-y-2 px-4 pb-4 text-sm">{packageType.inputs.map(input => <li key={input.name} className="flex items-center gap-2"><span>{fieldLabel(input.name)}</span><Badge>{uiText(input.required ? 'editors.RegistryObjects.pflicht_6a047f' : 'editors.RegistryObjects.optional_ec91fd')}</Badge></li>)}</ul>
      <PanelHeader title={uiText('features.contracts.DefinitionsPage.ausgaben_44e52a')} />
      <ul className="space-y-2 px-4 pb-4 text-sm">{Object.entries(packageType.schema.fields).map(([name, field]) => <li key={name} className="flex items-center gap-2"><span>{fieldLabel(name)}</span><Badge>{codeLabel(field.type)}</Badge></li>)}</ul>
    </Panel>
  </>;
  if (!definition) {
    return (
      <>
        <PageHeader title={uiText('alpha7.nav.templates')} description={uiText('alpha9.pages.template')} actions={<Button variant="primary" onClick={() => navigate('/templates/own')}>{uiText('components.RouteError.zur_ubersicht_22bfde')}</Button>} />
        <Empty title={uiText("features.contracts.DefinitionDetailPage.definition_v0_v1_nicht_gefunden_d09de5", { v0: (typeId), v1: (version) })}>{versionError || (src.source === 'draft' ? uiText("features.contracts.DefinitionDetailPage.sie_existiert_im_geoffneten_draft_nicht_0632aa") : uiText("features.contracts.DefinitionDetailPage.sie_existiert_in_der_aktiven_registry_nicht_8e5152"))}</Empty>
      </>
    );
  }

  const key = definitionKey(definition);
  const lockedSince = src.activatedVersions.get(key) ?? null;
  const locked = lockedSince !== null || !src.editable;
  const pointer = `/definitions/${index}`;
  const users = contractsUsingDefinition(src.config, typeId, Number(version));
  const nextVersion = nextVersionOf(definition, definitions);
  const beginVersion = async () => {
    setVersionError('');
    try {
      const config = await doc.ensureDraft();
      if (!asArray(config.definitions).filter(isObject).some(item => str(item.type_id) === typeId && String(item.type_version) === version)) throw new Error(uiText('alpha7.draft.templateMissing'));
      setMigrate(new Set(contractsUsingDefinition(config, typeId, Number(version))));
      setVersioning(true);
    } catch (error) { setVersionError(describeError(error)); }
  };

  const createVersion = () => {
    doc.edit((c) => {
      const withDefinition = setAt(c, '/definitions', [...asArray(c.definitions), nextVersion]);
      return setAt(
        withDefinition,
        '/contracts',
        asArray(withDefinition.contracts).map((contract) =>
          isObject(contract) && migrate.has(str(contract.contract_id)) ? { ...contract, type_version: Number(nextVersion.type_version) } : contract,
        ),
      );
    });
    setVersioning(false);
    navigate(`/templates/details/${encodeURIComponent(typeId)}/${String(nextVersion.type_version)}`);
  };

  return (
    <>
      <Back />
      <PageHeader
        title={str(definition.display_name) || uiText('alpha9.template.unnamed')}
        description={uiText('alpha9.pages.template')}
        actions={
          <>
            <Badge>{uiText("features.contracts.DefinitionDetailPage.v_4c9448")}{version}</Badge>
            {lockedSince !== null ? <LockBadge /> : <Badge tone="info">{uiText("features.contracts.DefinitionDetailPage.entwurf_2ba46e")}</Badge>}
            <Button variant="primary" disabled={doc.starting || doc.saving} disabledReason={uiText('alpha7.copy.inProgress')} onClick={async () => {
              if (locked) { await beginVersion(); return; }
              if (await doc.save() !== 'saved') setVersionError(uiText('alpha7.draft.saveBeforeReview'));
            }}>{locked ? <><GitBranchPlus size={14} />{uiText('features.contracts.DefinitionDetailPage.neue_version_e81cfe')}</> : uiText('alpha9.actions.saveDraft')}</Button>
            {!locked && <Button size="sm" disabled={doc.starting} onClick={() => void beginVersion()}>{uiText('features.contracts.DefinitionDetailPage.neue_version_e81cfe')}</Button>}
            {src.editable && <Button size="sm" variant="danger" onClick={() => setRemoving(true)}><Trash2 size={14} />{uiText('features.contracts.DefinitionDetailPage.entfernen_382837')}</Button>}
          </>
        }
      />
      {versionError && <p role="alert" className="mb-3 text-sm text-bad">{versionError}</p>}
      {doc.starting && <p role="status" className="mb-3 text-sm text-muted">{uiText('alpha7.draft.preparing')}</p>}
      <ReadOnlyNotice what={uiText("features.contracts.DefinitionDetailPage.definition_9813fa")} />
      <EditorialPanel object={{ kind: 'template', id: typeId }} />
      <PanelLayout view="definition.detail"><LayoutPanel id="editor" title={uiText("features.contracts.DefinitionDetailPage.definitionseditor_c9b1ee")}><div className="min-w-0">
        <DefinitionEditor
          value={definition}
          pointer={pointer}
          caps={caps.data}
          initialTab={params.get('tab') ?? undefined}
          locked={locked}
          readOnly={!src.editable}
          lockReason={lockedSince !== null ? uiText("features.contracts.DefinitionDetailPage.diese_version_war_in_revision_v0_aktiv_7f3ad4", { v0: (lockedSince) }) : src.editable ? undefined : uiText("features.contracts.DefinitionDetailPage.schreibgeschutzte_ansicht_der_aktiven_registry_10e355")}
          onChange={(next) => doc.edit((c) => ({ ...setAt(c, pointer, next), ...(Number(next.language_version) === 4 ? { config_schema_version: 3, fingerprint_version: 3 } : Number(next.language_version) >= 3 ? { config_schema_version: Math.max(2, Number(c.config_schema_version ?? 1)) } : {}) }))}
        />
      </div>
      </LayoutPanel><LayoutPanel id="usage" title={uiText("features.contracts.DefinitionDetailPage.verwendung_947c0a")}><section className="text-sm text-muted">
        {uiText("features.contracts.DefinitionDetailPage.verwendet_von_f83bfe")}{users.length === 0 ? uiText("features.contracts.DefinitionDetailPage.keiner_instanz_86ff43") : users.map((id) => <Link key={id} to={`/contracts/view/${encodeURIComponent(id)}`} className="mr-2 font-mono text-xs text-fg hover:underline">{id}</Link>)}
      </section>
      </LayoutPanel></PanelLayout>

      <Dialog
        open={versioning}
        onOpenChange={setVersioning}
        title={uiText("features.contracts.DefinitionDetailPage.neue_version_von_v0_8ee68e", { v0: (typeId) })}
        description={uiText("features.contracts.DefinitionDetailPage.es_wird_v_v0_als_kopie_von_v_v1_angelegt_die_bis_cdd655", { v0: (String(nextVersion.type_version)), v1: (version) })}
        footer={<><Button onClick={() => setVersioning(false)}>{uiText("features.contracts.DefinitionDetailPage.abbrechen_f7ff11")}</Button><Button variant="primary" onClick={createVersion}>{uiText("features.contracts.DefinitionDetailPage.version_anlegen_3d33f7")}</Button></>}
      >
        {users.length > 0 ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-muted">{uiText("features.contracts.DefinitionDetailPage.instanzen_gleich_auf_die_neue_version_umstellen__a98ead")}</p>
            {users.map((id) => (
              <Checkbox key={id} checked={migrate.has(id)} onCheckedChange={(on) => setMigrate((s) => { const next = new Set(s); if (on) next.add(id); else next.delete(id); return next; })} label={<code>{id}</code>} />
            ))}
          </div>
        ) : (
          <Notice tone="info">{uiText("features.contracts.DefinitionDetailPage.keine_instanz_verwendet_diese_version_2f0d3f")}</Notice>
        )}
      </Dialog>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={uiText("features.contracts.DefinitionDetailPage.definition_entfernen_2fb8aa")}
        description={<>{key} {uiText("features.contracts.DefinitionDetailPage.wird_aus_dem_draft_entfernt_2fe205")}{users.length > 0 && <> {uiText("features.contracts.DefinitionDetailPage.noch_verwendet_von_cd365f")}{users.join(', ')}.</>}{lockedSince !== null && <> {uiText("features.contracts.DefinitionDetailPage.diese_version_war_bereits_aktiv_sie_kann_spater__eb414d")}</>}</>}
        confirmLabel={uiText("features.contracts.DefinitionDetailPage.entfernen_382837")}
        tone="danger"
        onConfirm={() => {
          doc.edit((c) => setAt(c, pointer, undefined));
          setRemoving(false);
          navigate("/templates/own");
        }}
      />
    </>
  );
}

function Back() {
  return (
    <>
      <ObjectReadBack fallback={{ to: '/templates/own', label: uiText('features.contracts.DefinitionDetailPage.alle_definitionen_bd3333') }} />
      <ContractsTabs />
    </>
  );
}
