import { t as uiText } from '@/i18n';
import { createColumnHelper } from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { DataTable } from '@/components/DataTable';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Collapsible } from '@/components/ui/controls';
import { codeLabel } from '@/i18n/codes';
import { Field, Input, Select } from '@/components/ui/form';
import { ID_HINT, ID_LIMIT, validIdentifier } from '@/lib/identifiers';
import { Empty, PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { LockBadge } from '@/editors/DefinitionEditor';
import { ReadOnlyNotice } from '@/features/authoring/DraftBar';
import { useAllIssues } from '@/features/authoring/issues';
import { asArray, asObject, definitionKey, newDefinition, str, uniqueName } from '@/features/authoring/model';
import { contractsUsingDefinition } from '@/features/authoring/usage';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { isObject } from '@/lib/json';
import { useCapabilities } from '@/lib/queries';
import { ContractsTabs, NewContractDialog } from './ContractsPage';
import { describeError } from '@/lib/api';
import { CategoryBadge, CategorySelect, EditorialPanel, useEditorial } from '@/features/editorial/Editorial';
import { useRouteFilters } from '@/lib/route-filters';
import { NO_ROOM, groupItems, type Category } from '@/features/editorial/model';

interface Row {
  key: string;
  typeId: string;
  version: number;
  name: string;
  inputs: number;
  outputs: number;
  machine: boolean;
  usedBy: number;
  lockedSince: number | null;
  errors: number;
  category: Category;
}
const col = createColumnHelper<Row>();

export default function DefinitionsPage() {
  const builtin = useLocation().pathname === '/templates/built-in';
  const src = useAuthoringSource();
  const doc = useWorkingDocument();
  const caps = useCapabilities();
  const issues = useAllIssues();
  const navigate = useNavigate();
  const metadata = useEditorial();
  const [category, setCategory] = useState<Category>('other'); const [busy, setBusy] = useState(false);
  const route = useRouteFilters(); const [categoryFilter, setCategoryFilter] = route.field<Category | ''>('category', ''); const [groupValue, setGroupValue] = route.field('group', 'none'); const group = groupValue === 'category'; const setGroup = (value: boolean) => setGroupValue(value ? 'category' : 'none');
  const [creating, setCreating] = useState(false);
  const [creatingContract, setCreatingContract] = useState(false);
  const [typeId, setTypeId] = useState('');
  const [name, setName] = useState('');
  const [creationError, setCreationError] = useState('');
  const start = async () => {
    setCreationError('');
    try { await doc.ensureDraft(); setTypeId(''); setName(''); setCreating(true); }
    catch (error) { setCreationError(describeError(error)); }
  };

  const definitions = asArray(src.config?.definitions).filter(isObject);
  const rows: Row[] = definitions.map((d, index) => ({
    key: definitionKey(d),
    category: metadata.item({ kind: 'template', id: str(d.type_id) }).category,
    typeId: str(d.type_id),
    version: Number(d.type_version),
    name: str(d.display_name),
    inputs: asArray(d.inputs).length,
    outputs: Object.keys(asObject(asObject(d.schema).fields)).length,
    machine: isObject(d.machine),
    usedBy: contractsUsingDefinition(src.config, str(d.type_id), Number(d.type_version)).length,
    lockedSince: src.activatedVersions.get(definitionKey(d)) ?? null,
    errors: issues.filter((i) => i.path.startsWith(`/definitions/${index}`)).length,
  }));
  const packageTypes = (caps.data?.types ?? []).filter((t) => t.definition === undefined && (!categoryFilter || metadata.item({ kind: 'template', id: t.type_id }).category === categoryFilter));
  const groups = groupItems(rows.filter(row => !categoryFilter || row.category === categoryFilter), row => group ? row.category : 'all');
  const packageGroups = groupItems(packageTypes, item => group ? metadata.item({ kind: 'template', id: item.type_id }).category : 'all');

  const columns = useMemo(
    () => [
      col.accessor('typeId', { header: uiText("features.contracts.DefinitionsPage.typ_id_b7db41"), cell: (c) => <code className="text-xs text-fg">{c.getValue()}</code> }),
      col.accessor('version', { header: uiText("features.contracts.DefinitionsPage.version_dd1679"), cell: (c) => <Badge tone="neutral">{uiText("features.contracts.DefinitionsPage.v_4c9448")}{c.getValue()}</Badge> }),
      col.accessor('name', { header: uiText("features.contracts.DefinitionsPage.name_dcd1d5"), cell: (c) => c.getValue() || <span className="text-faint">—</span> }),
      col.accessor('category', { header: uiText('alpha9.category.label'), cell: c => <CategoryBadge category={c.getValue()} /> }),
      col.accessor('inputs', { header: uiText("features.contracts.DefinitionsPage.eingange_53fb39") }),
      col.accessor('outputs', { header: uiText("features.contracts.DefinitionsPage.ausgaben_44e52a") }),
      col.accessor('machine', { header: uiText("features.contracts.DefinitionsPage.machine_8f1cc4"), cell: (c) => (c.getValue() ? <Badge tone="accent">{uiText("features.contracts.DefinitionsPage.ja_3702fc")}</Badge> : <span className="text-faint">–</span>) }),
      col.accessor('usedBy', { header: uiText("features.contracts.DefinitionsPage.instanzen_a94c7e") }),
      col.accessor((r) => r.lockedSince ?? 0, { id: 'status', header: uiText("features.contracts.DefinitionsPage.status_920e41"), cell: (c) => (c.row.original.lockedSince !== null ? <span className="flex items-center gap-1.5"><LockBadge /><span className="text-[11px] text-faint">{uiText("features.contracts.DefinitionsPage.seit_rev_6ccd9c")}{c.row.original.lockedSince}</span></span> : <Badge tone="info">{uiText("features.contracts.DefinitionsPage.entwurf_2ba46e")}</Badge>) }),
      col.accessor('errors', { header: uiText("features.contracts.DefinitionsPage.fehler_f6cc01"), cell: (c) => (c.getValue() ? <Badge tone="bad">{c.getValue()}</Badge> : null) }),
    ],
    [],
  );

  const usedIds = [...definitions.map(definition => str(definition.type_id)), ...(caps.data?.types.map(type => type.type_id) ?? [])];
  const suggested = name.trim().toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss').replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 96) || 'vorlage';
  const fullId = typeId.trim() ? (typeId.includes('.') ? typeId.trim() : `vertrag.${typeId.trim()}`) : uniqueName(`vertrag.${suggested}`, usedIds);
  const exists = usedIds.includes(fullId);
  const validId = validIdentifier(fullId);

  return (
    <>
      <PageHeader
        title={uiText(builtin ? 'alpha7.nav.builtinTemplates' : 'alpha7.nav.ownTemplates')}
        description={uiText(builtin ? 'alpha9.pages.templatesBuiltin' : 'alpha9.pages.templatesOwn')}
        actions={
          builtin ? <Button variant="primary" disabled={doc.starting} disabledReason={uiText('alpha7.copy.inProgress')} onClick={async () => { setCreationError(''); try { await doc.ensureDraft(); setCreatingContract(true); } catch (error) { setCreationError(describeError(error)); } }}>{uiText('alpha9.actions.createContract')}</Button> : <Button variant="primary" disabled={doc.starting} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => void start()}>
            <Plus size={15} /> {uiText("features.contracts.DefinitionsPage.neue_definition_fcff16")}</Button>
        }
      />
      {creationError && <p role="alert" className="mb-3 text-sm text-bad">{creationError}</p>}
      {doc.starting && <p role="status" className="mb-3 text-sm text-muted">{uiText('alpha7.draft.preparing')}</p>}
      <div className="mb-4 flex flex-wrap items-end gap-3"><div className="w-56"><CategorySelect value={categoryFilter} onChange={setCategoryFilter} all /></div><Field label={uiText('alpha9.editorial.group')}>{props => <Select {...props} value={group ? 'category' : 'none'} onChange={event => setGroup(event.target.value === 'category')}><option value="none">{uiText('alpha9.editorial.noGrouping')}</option><option value="category">{uiText('alpha9.category.group')}</option></Select>}</Field></div>
      <ContractsTabs />
      <NewContractDialog initialManual open={creatingContract} onOpenChange={setCreatingContract} onCreated={id => navigate(`/contracts/view/${encodeURIComponent(id)}`)} />
      <ReadOnlyNotice what={uiText("features.contracts.DefinitionsPage.definitionen_871a91")} />
      <PanelLayout view={builtin ? 'templates.builtin' : 'templates.own'}>{!builtin && <LayoutPanel id="definitions" title={uiText("features.contracts.DefinitionsPage.deklarative_definitionen_3a5155")}><Panel>
        <PanelHeader title={uiText("features.contracts.DefinitionsPage.deklarative_definitionen_3a5155")} description={uiText("features.contracts.DefinitionsPage.aktivierte_versionen_sind_gesperrt_eine_logikand_5deacf")} />
        {(groups.length ? groups : [['empty', []] as [string, Row[]]]).map(([key, members]) => <section key={key}>
        {group && members[0] && <h2 className="border-b border-line bg-raised px-4 py-3"><CategoryBadge category={members[0].category} /></h2>}
        <DataTable
          caption={uiText("features.contracts.DefinitionsPage.deklarative_definitionen_3a5155")}
          data={members}
          columns={columns}
          getRowId={(r) => r.key}
          onRowClick={(r) => navigate(`/templates/details/${encodeURIComponent(r.typeId)}/${r.version}`)}
          empty={<Empty title={uiText("features.contracts.DefinitionsPage.noch_keine_definitionen_57fe40")}>{uiText("features.contracts.DefinitionsPage.legen_sie_in_einem_draft_die_erste_deklarative_d_063da9")}</Empty>}
        /></section>)}
      </Panel>
      </LayoutPanel>}{builtin && <LayoutPanel id="packages" title={uiText("features.contracts.DefinitionsPage.paket_typen_a517d6")}><Panel>
        <PanelHeader title={uiText("features.contracts.DefinitionsPage.paket_typen_a517d6")} description={uiText("features.contracts.DefinitionsPage.fest_im_code_der_app_nicht_uber_die_workbench_an_49f18f")} />
        {packageGroups.map(([key, members]) => <section key={key}>
        {group && members[0] && <h2 className="border-b border-line bg-raised px-4 py-3"><CategoryBadge category={metadata.item({ kind: 'template', id: members[0].type_id }).category} /></h2>}
        <ul className="divide-y divide-line">
          {members.map((t) => (
            <li key={`${t.type_id}@${t.type_version}`} className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm">
              <code className="text-xs">{t.type_id}</code>
              <Badge>{uiText("features.contracts.DefinitionsPage.v_4c9448")}{t.type_version}</Badge>
              <div className="w-full"><EditorialPanel object={{ kind: 'template', id: t.type_id }} /></div>
              <span className="text-xs text-muted">{codeLabel(t.producer)} · {uiText(Object.keys(t.schema.fields).length === 1 ? 'alpha7.template.oneField' : 'alpha7.template.fields', { count: Object.keys(t.schema.fields).length })}</span>
            </li>
          ))}
          {packageTypes.length === 0 && <li className="px-4 py-3 text-sm text-muted">{uiText("features.contracts.DefinitionsPage.keine_paket_typen_gemeldet_e04f62")}</li>}
        </ul></section>)}
        {packageTypes.length === 0 && <p className="p-4 text-sm text-muted">{uiText('alpha9.editorial.noMatches')}</p>}
      </Panel>
      </LayoutPanel>}</PanelLayout>
      <Dialog
        open={creating}
        onOpenChange={setCreating}
        title={uiText("features.contracts.DefinitionsPage.neue_definition_fcff16")}
        description={uiText("features.contracts.DefinitionsPage.beginnt_mit_einem_eingang_und_einem_ausgabefeld__b84536")}
        footer={
          <>
            <Button onClick={() => setCreating(false)}>{uiText("features.contracts.DefinitionsPage.abbrechen_f7ff11")}</Button>
            <Button
              variant="primary"
              disabled={!validId || exists || busy || metadata.isPending || metadata.isError} disabledReason={exists ? uiText('features.contracts.DefinitionsPage.diese_typ_id_existiert_bereits_fur_anderungen_ei_01c137') : uiText('alpha7.copy.checkIdentifier')}
              onClick={async () => {
                setBusy(true); setCreationError('');
                try {
                  const id = fullId; const object = { kind: 'template' as const, id };
                  await doc.editAndSave((c) => ({ ...c, fingerprint_version: Math.max(2, Number(c.fingerprint_version ?? 1)), definitions: [...asArray(c.definitions), { ...newDefinition(id, 1), ...(name.trim() ? { display_name: name.trim() } : {}) }] }), [{ object, category, room: NO_ROOM, expected_editorial_version: metadata.item(object).editorial_version }]);
                  setCreating(false); navigate(`/templates/details/${encodeURIComponent(id)}/1`);
                } catch (failure) { setCreationError(describeError(failure)); }
                finally { setBusy(false); }
              }}
            >
              {uiText("features.contracts.DefinitionsPage.anlegen_d34c0e")}</Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label={uiText('alpha7.template.name')}>{props => <Input {...props} autoFocus maxLength={128} value={name} placeholder={uiText('alpha7.template.nameExample')} onChange={event => setName(event.target.value)} />}</Field>
          <CategorySelect value={category} onChange={value => setCategory(value || 'other')} />
          {creationError && <p role="alert" className="text-sm text-bad">{creationError}</p>}
          <p className="text-sm text-muted">{uiText('alpha7.template.initial')}</p>
          <Collapsible title={uiText('alpha7.device.advanced')}>
            <Field label={uiText("features.contracts.DefinitionsPage.typ_id_b7db41")} hint={uiText('alpha7.template.prefix')} errors={typeId && exists ? [{ code: 'duplicate_identity', message: uiText("features.contracts.DefinitionsPage.diese_typ_id_existiert_bereits_fur_anderungen_ei_01c137") }] : typeId && !validId ? [{ code: 'schema.pattern', message: ID_HINT }] : []}>
              {(p) => <Input {...p} maxLength={ID_LIMIT} value={typeId} placeholder={fullId} title={ID_HINT} onChange={(e) => setTypeId(e.target.value)} />}
            </Field>
          </Collapsible>
        </div>
      </Dialog>
    </>
  );
}
