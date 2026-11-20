import { PageLoading } from '@/components/PageLoading';
import { t as uiText } from '@/i18n';
import { GitBranchPlus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/controls';
import { ConfirmDialog } from '@/components/ui/dialog';
import { EditButton } from '@/components/EditButton';
import { Empty, Notice, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { DefinitionEditor, LockBadge } from '@/editors/DefinitionEditor';
import { ReadOnlyNotice } from '@/features/authoring/DraftBar';
import { asArray, definitionKey, nextVersionOf, str } from '@/features/authoring/model';
import { contractsUsingDefinition } from '@/features/authoring/usage';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { clone, deepEqual, isObject, setAt } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { useCapabilities } from '@/lib/queries';
import { ContractsTabs } from './ContractsPage';
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
  const [editing, setEditing] = useState<{ base: Registry; config: Registry; index: number; lockedSince: number | null; removed: boolean } | null>(null);
  const [versioning, setVersioning] = useState<{ base: Registry; copy: Registry } | null>(null);
  const [migration, setMigration] = useState<{ base: Registry; version: number } | null>(null);
  const [removing, setRemoving] = useState(false);
  const [migrate, setMigrate] = useState<string[]>([]);
  useEffect(() => { setEditing(null); setVersioning(null); setMigration(null); setRemoving(false); setMigrate([]); }, [typeId, version]);

  const config = editing?.base ?? versioning?.base ?? migration?.base ?? src.config;
  const definitions = asArray(config?.definitions).filter(isObject);
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
        <Empty title={uiText("features.contracts.DefinitionDetailPage.definition_v0_v1_nicht_gefunden_d09de5", { v0: (typeId), v1: (version) })}>{src.source === 'draft' ? uiText("features.contracts.DefinitionDetailPage.sie_existiert_im_geoffneten_draft_nicht_0632aa") : uiText("features.contracts.DefinitionDetailPage.sie_existiert_in_der_aktiven_registry_nicht_8e5152")}</Empty>
      </>
    );
  }

  const key = definitionKey(definition);
  const lockedSince = src.activatedVersions.get(key) ?? null;
  const pointer = `/definitions/${index}`;
  const users = contractsUsingDefinition(config, typeId, Number(version));
  const editingPointer = `/definitions/${editing?.index}`;
  const openVersion = () => { if (config) setVersioning({ base: config, copy: nextVersionOf(definition, definitions) }); };
  const finishMigration = (target: number) => {
    setMigration(null); setMigrate([]);
    navigate(`/templates/details/${encodeURIComponent(typeId)}/${target}`);
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
            <EditButton disabled={doc.starting || doc.saving} onClick={() => { if (config) setEditing({ base: config, config: clone(config), index, lockedSince, removed: false }); }} />
            <Button variant={lockedSince !== null ? 'primary' : 'secondary'} disabled={doc.starting || doc.saving} onClick={openVersion}><GitBranchPlus size={14} />{uiText('features.contracts.DefinitionDetailPage.neue_version_e81cfe')}</Button>
          </>
        }
      />
      {doc.starting && <p role="status" className="mb-3 text-sm text-muted">{uiText('alpha7.draft.preparing')}</p>}
      <ReadOnlyNotice what={uiText("features.contracts.DefinitionDetailPage.definition_9813fa")} />
      <EditorialPanel object={{ kind: 'template', id: typeId }} />
      <PanelLayout view="definition.detail"><LayoutPanel id="editor" title={uiText("features.contracts.DefinitionDetailPage.definitionseditor_c9b1ee")}><div className="min-w-0">
        <DefinitionEditor
          value={definition}
          pointer={pointer}
          caps={caps.data}
          initialTab={params.get('tab') ?? undefined}
          locked={lockedSince !== null}
          readOnly
          lockReason={lockedSince !== null ? uiText("features.contracts.DefinitionDetailPage.diese_version_war_in_revision_v0_aktiv_7f3ad4", { v0: (lockedSince) }) : undefined}
          onChange={() => undefined}
        />
      </div>
      </LayoutPanel><LayoutPanel id="usage" title={uiText("features.contracts.DefinitionDetailPage.verwendung_947c0a")}><section className="text-sm text-muted">
        {uiText("features.contracts.DefinitionDetailPage.verwendet_von_f83bfe")}{users.length === 0 ? uiText("features.contracts.DefinitionDetailPage.keiner_instanz_86ff43") : users.map((id) => <Link key={id} to={`/contracts/view/${encodeURIComponent(id)}`} className="mr-2 font-mono text-xs text-fg hover:underline">{id}</Link>)}
      </section>
      </LayoutPanel></PanelLayout>

      {editing && <DraftEditor base={editing.base} input={editing.config} dirty={!deepEqual(editing.base, editing.config)} plan={() => ({ config: editing.config })} title={uiText('a2.edit.template')} size="xl" onClose={() => setEditing(null)} onSaved={() => {
        const row = asArray(editing.config.definitions)[editing.index];
        if (editing.removed) navigate('/templates/own');
        else if (isObject(row) && (str(row.type_id) !== typeId || String(row.type_version) !== version)) navigate(`/templates/details/${encodeURIComponent(str(row.type_id))}/${String(row.type_version)}`);
      }} footer={!editing.removed && <Button size="sm" variant="danger" onClick={() => setRemoving(true)}><Trash2 size={14} />{uiText('features.contracts.DefinitionDetailPage.entfernen_382837')}</Button>}>
        {editing.removed ? <p>{uiText('a2.edit.removalPending')}</p> : <DefinitionEditor value={asArray(editing.config.definitions).filter(isObject)[editing.index]!} pointer={editingPointer} caps={caps.data} initialTab={params.get('tab') ?? undefined} locked={editing.lockedSince !== null} onChange={next => setEditing(current => current && { ...current, config: { ...setAt(current.config, editingPointer, next), ...(Number(next.language_version) === 4 ? { config_schema_version: 3, fingerprint_version: 3 } : Number(next.language_version) >= 3 ? { config_schema_version: Math.max(2, Number(current.config.config_schema_version ?? 1)) } : {}) } })} />}
      </DraftEditor>}
      {versioning && <DraftEditor base={versioning.base} input={versioning.copy} dirty plan={() => ({ config: setAt(versioning.base, '/definitions', [...asArray(versioning.base.definitions), versioning.copy]) })} onClose={() => setVersioning(null)} onSaved={saved => {
        const next = Number(versioning.copy.type_version);
        if (contractsUsingDefinition(saved.config, typeId, Number(version)).length) setMigration({ base: saved.config, version: next });
        else finishMigration(next);
      }} title={uiText('features.contracts.DefinitionDetailPage.neue_version_von_v0_8ee68e', { v0: typeId })}>
        <p>{uiText('a2.edit.copyHelp', { source: version, target: versioning.copy.type_version })}</p>
      </DraftEditor>}
      {migration && <DraftEditor base={migration.base} input={migrate} dirty={migrate.length > 0} ready={migrate.length > 0} plan={() => ({ config: setAt(migration.base, '/contracts', asArray(migration.base.contracts).map(row => isObject(row) && migrate.includes(str(row.contract_id)) ? { ...row, type_version: migration.version } : row)) })} onClose={() => finishMigration(migration.version)} onNavigateAway={() => { setMigration(null); setMigrate([]); }} title={uiText('a2.edit.migrateTitle', { version: migration.version })}>
        <Notice tone="warn">{uiText('a2.edit.migrationWarning')}</Notice>
        <div className="mt-3 space-y-2">{asArray(migration.base.contracts).filter(isObject).filter(row => users.includes(str(row.contract_id))).map(row => {
          const id = str(row.contract_id);
          return <Checkbox key={id} checked={migrate.includes(id)} onCheckedChange={on => setMigrate(current => on ? [...current, id] : current.filter(entry => entry !== id))} label={str(row.display_name) || id} />;
        })}</div>
      </DraftEditor>}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={uiText("features.contracts.DefinitionDetailPage.definition_entfernen_2fb8aa")}
        description={<>{key} {uiText("features.contracts.DefinitionDetailPage.wird_aus_dem_draft_entfernt_2fe205")}{users.length > 0 && <> {uiText("features.contracts.DefinitionDetailPage.noch_verwendet_von_cd365f")}{users.join(', ')}.</>}{lockedSince !== null && <> {uiText("features.contracts.DefinitionDetailPage.diese_version_war_bereits_aktiv_sie_kann_spater__eb414d")}</>}</>}
        confirmLabel={uiText("features.contracts.DefinitionDetailPage.entfernen_382837")}
        tone="danger"
        onConfirm={() => {
          setEditing(current => current && { ...current, config: setAt(current.config, editingPointer, undefined), removed: true });
          setRemoving(false);
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
