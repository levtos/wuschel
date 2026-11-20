import { useRef, useState } from 'react';
import { Link } from 'react-router';
import { t } from '@/i18n';
import { codeLabel } from '@/i18n/codes';
import { Button } from '@/components/ui/button';
import { Checkbox, Collapsible } from '@/components/ui/controls';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { StringListEditor } from '@/editors/common';
import { asObject, str, type FieldSchema } from '@/features/authoring/model';
import { consumersOfSource } from '@/features/authoring/usage';
import { useWorkingDocument, type WorkingDocument } from '@/features/authoring/WorkingDocument';
import { EffectStrip, useEditorBase } from '@/features/authoring/DraftEditor';
import { useEditorGuard } from '@/features/authoring/EditorGuard';
import { applyEditorChanges, EditorInputError, SavedDraftAccepted } from '@/features/authoring/editorChanges';
import { DiffView } from '@/features/registry/DiffView';
import { IssueList } from '@/features/registry/IssueList';
import { ApiError, describeError, type Issue } from '@/lib/api';
import { clone, deepEqual, type JsonObject } from '@/lib/json';
import { validIdentifier } from '@/lib/identifiers';
import { useRevisions, validateConfig } from '@/lib/queries';
import { deviceValue } from './DeviceWizard';
import { useHaCatalog } from './useHaCatalog';
import { migrationSourceId, objects, planSourceMigration, sourceSchema, SourceMigrationError, type MigrationOptions } from './sourceMigration';
import { planEntityChange } from './sourceMigration';
import { EntityPicker } from '@/editors/RegistryObjects';
import type { ValidationResult } from '@/lib/types';

/** Mounted for one source and one draft; no writes until its exact candidate has passed validation. */
export function SourceMigrationDialog(props: { sourceId: string; bindingId?: string; entityOnly?: boolean; onClose: () => void }) {
  return props.entityOnly ? <EntityChangeDialog {...props} /> : <MigrationDialog {...props} />;
}

async function saveMigration(doc: WorkingDocument, base: JsonObject, checked: JsonObject) {
  const target = await doc.ensureDraft();
  const merged = applyEditorChanges(base, checked, target);
  if (!deepEqual(merged, checked) && !(await validateConfig(merged)).valid) {
    throw new EditorInputError(t('alpha7.move.fixBeforeMove'));
  }
  await doc.editAndSave(current => {
    if (!deepEqual(current, target)) throw new EditorInputError(t('alpha7.move.changed'));
    return merged;
  });
}

function EntityChangeDialog({ sourceId, bindingId, onClose }: { sourceId: string; bindingId?: string; onClose: () => void }) {
  const doc = useWorkingDocument(); const base = useEditorBase(); const owner = useRef({ id: doc.draft?.draft_id, config: doc.config });
  const triedSaving = useRef(false);
  const latest = useRef(doc); latest.current = doc;
  const bindings = objects(base.bindings).filter(row => row.source_id === sourceId && str(asObject(row.adapter).kind).startsWith('ha_'));
  const [selectedBinding, setSelectedBinding] = useState(bindingId || str(bindings[0]?.binding_id));
  const [entity, setEntity] = useState(''); const [picker, setPicker] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [issues, setIssues] = useState<Issue[]>([]);
  const [review, setReview] = useState<{ config: JsonObject; binding: string; entity: string; diff?: ValidationResult['diff'] } | null>(null);
  const chosen = bindings.find(row => row.binding_id === selectedBinding);
  const unchanged = () => triedSaving.current || (owner.current.id === latest.current.draft?.draft_id && deepEqual(owner.current.config, latest.current.config));
  const fresh = review && unchanged() && review.binding === selectedBinding && review.entity === entity;
  const guard = useEditorGuard(!!entity || selectedBinding !== (bindingId || str(bindings[0]?.binding_id)), onClose, busy);
  const check = async () => {
    if (!unchanged()) { setError(t('b22.object.changed')); return; }
    setBusy(true); setError(''); setIssues([]); setReview(null);
    const binding = selectedBinding; const target = entity;
    try {
      const config = planEntityChange(base, sourceId, binding, target);
      const result = await validateConfig(config);
      if (!unchanged()) { setError(t('b22.object.changed')); return; }
      if (!result.valid) { setIssues(result.issues); return; }
      setReview({ config, binding, entity: target, diff: result.diff });
    } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); }
  };
  const save = async () => {
    if (!fresh || !review) return;
    triedSaving.current = true;
    setBusy(true); setError('');
    try { await saveMigration(doc, base, review.config); guard.finish(); }
    catch (failure) { if (failure instanceof SavedDraftAccepted) guard.finish(); else setError(failure instanceof EditorInputError ? failure.message : describeError(failure)); }
    finally { setBusy(false); }
  };
  return <><Dialog open onOpenChange={open => !open && guard.requestClose()} title={t('b22.entity.change')} description={t('b22.entity.help')} footer={<><Button disabled={busy} onClick={() => guard.requestClose()}>{t('common.cancel')}</Button><Button disabled={busy || !entity || !chosen} onClick={() => void check()}>{t('a2.edit.check')}</Button><Button variant="primary" disabled={busy || !fresh} onClick={() => void save()}>{t('a2.edit.save')}</Button></>}>
    <EffectStrip effect="draft" />
    {bindings.length > 1 && <Field label={t('alpha7.nav.bindings')}>{props => <Select {...props} value={selectedBinding} onChange={event => { setSelectedBinding(event.target.value); setReview(null); }}>{bindings.map(row => <option key={str(row.binding_id)} value={str(row.binding_id)}>{str(row.display_name) || str(row.binding_id)}</option>)}</Select>}</Field>}
    <p>{entity || t('b22.entity.chooseFirst')}</p><Button disabled={busy} onClick={() => setPicker(true)}>{t('b22.entity.choose')}</Button>
    {error && <p role="alert">{error}</p>}{issues.length > 0 && <IssueList issues={issues} config={review?.config ?? base} />}
    {fresh && <p role="status">{t('b22.entity.retained')}</p>}
    {fresh && review?.diff && <DiffView diff={review.diff} config={review.config} />}
  </Dialog>{guard.confirmation}<EntityPicker open={picker} onOpenChange={setPicker} onPick={(id, attributes) => {
    const attribute = str(asObject(chosen?.adapter).ha_attribute);
    if (attribute && !attributes.includes(attribute)) { setError(t('b22.entity.attributeMissing')); return; }
    setEntity(id); setReview(null); setError(''); setPicker(false);
  }} /></>;
}

function MigrationDialog({ sourceId, onClose }: { sourceId: string; onClose: () => void }) {
  const doc = useWorkingDocument(); const revisions = useRevisions(); const base = useEditorBase();
  const owner = useRef({ id: doc.draft?.draft_id, config: doc.config });
  const triedSaving = useRef(false);
  const ha = useHaCatalog(true, true);
  const latest = useRef(doc); latest.current = doc;
  const source = objects(base.sources).find(item => item.source_id === sourceId);
  const binding = objects(base.bindings).find(item => item.source_id === sourceId);
  const users = consumersOfSource(base, sourceId).contracts;
  const [name, setName] = useState(str(source?.display_name) || t('alpha7.move.defaultName'));
  const [id, setId] = useState(() => migrationSourceId(base, revisions.data ?? [], sourceId));
  const [schema, setSchema] = useState<FieldSchema>(() => sourceSchema(source ?? {}));
  const [adapter, setAdapter] = useState<JsonObject | null>(() => binding ? clone(asObject(binding.adapter)) : null);
  const [chosen, setChosen] = useState(users);
  const [templates, setTemplates] = useState<Record<string, { type_id: string; type_version: number }>>({});
  const [adapt, setAdapt] = useState(true);
  const [transform, setTransform] = useState<JsonObject | undefined>(() => binding && asObject(binding.transform).kind !== 'type_cast' ? clone(asObject(binding.transform)) : undefined);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [issues, setIssues] = useState<Issue[]>([]);
  const [review, setReview] = useState<{ options: MigrationOptions; result: ReturnType<typeof planSourceMigration>; diff?: ValidationResult['diff'] } | null>(null);
  const options: MigrationOptions = { sourceId: id, name, schema, adapter, contracts: chosen, templates, adaptPassthrough: adapt, ...(transform ? { transform } : {}) };
  const currentOptions = useRef(options); currentOptions.current = options;
  const initial = useRef(options);
  const guard = useEditorGuard(!deepEqual(options, initial.current) || !!review, onClose, busy);
  const unchanged = () => triedSaving.current || (owner.current.id === latest.current.draft?.draft_id && deepEqual(owner.current.config, latest.current.config));
  let plan: ReturnType<typeof planSourceMigration> | null = null; let problem = '';
  try { plan = planSourceMigration(base, revisions.data ?? [], sourceId, options); }
  catch (failure) { problem = failure instanceof SourceMigrationError ? failure.message : describeError(failure); }
  const disabledReason = busy ? t('alpha7.move.checking') : !unchanged() ? t('alpha7.move.changed') : !source ? t('alpha7.move.sourceGone') : revisions.isPending ? t('alpha7.move.loadingHistory') : revisions.isError ? t('alpha7.move.historyUnavailable') : !id.startsWith('source.') || !validIdentifier(id) ? t('alpha7.device.badIdentifier') : schema.type === 'enum' && (!Array.isArray(schema.value_catalog) || !schema.value_catalog.length) ? t('alpha7.move.catalogRequired') : problem;
  const fresh = !!review && unchanged() && deepEqual(review.options, options);
  const check = async () => {
    if (disabledReason || !plan) return;
    const candidate = plan; const form = clone(options);
    setBusy(true); setError(''); setIssues([]); setReview(null);
    try {
      const verdict = await validateConfig(candidate.config);
      if (!unchanged() || !deepEqual(currentOptions.current, form)) { setError(t('alpha7.move.changed')); return; }
      if (!verdict.valid) { setIssues(verdict.issues); return; }
      setReview({ options: form, result: candidate, diff: verdict.diff });
    } catch (failure) {
      if (failure instanceof ApiError && failure.issues.length) setIssues(failure.issues); else setError(describeError(failure));
    } finally { setBusy(false); }
  };
  const apply = async () => {
    if (!fresh || !review || disabledReason) return;
    triedSaving.current = true;
    setBusy(true); setError('');
    try { await saveMigration(doc, base, review.result.config); guard.finish(); }
    catch (failure) { if (failure instanceof SavedDraftAccepted) guard.finish(); else setError(failure instanceof EditorInputError ? failure.message : describeError(failure)); }
    finally { setBusy(false); }
  };
  const definitions = objects(base.definitions);
  return <><Dialog open onOpenChange={open => !open && guard.requestClose()} size="xl" title={t('alpha7.move.title')} description={t('alpha7.move.explanation')} footer={<>
    <Button disabled={busy} disabledReason={t('alpha7.copy.inProgress')} onClick={() => guard.requestClose()}>{t('common.cancel')}</Button>
    <Button disabled={!!disabledReason} onClick={() => void check()}>{t('a2.edit.check')}</Button>
    <Button variant="primary" disabled={!fresh || !!disabledReason} onClick={() => void apply()}>{t('a2.edit.save')}</Button>
  </>}>
    <EffectStrip effect="draft" />
    <fieldset disabled={busy} className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Field label={t('alpha7.move.name')}>{props => <Input {...props} value={name} onChange={event => setName(event.target.value)} maxLength={128} />}</Field>
        <Field label={t('alpha7.move.type')}>{props => <Select {...props} value={schema.type} onChange={event => { const type = event.target.value as FieldSchema['type']; setSchema({ type, required: schema.required, physical_state: schema.physical_state, ...(type === 'enum' ? { value_catalog: [] } : {}) }); setTransform(type === 'enum' ? { kind: 'map_ref', catalog_id: '' } : undefined); }}>
          {['number', 'bool', 'text', 'enum'].map(type => <option key={type} value={type}>{codeLabel(type)}</option>)}
        </Select>}</Field>
      </div>
      {schema.type === 'number' && <Field label={t('alpha7.move.unit')} hint={t('alpha7.move.unitHelp')}>{props => <Input {...props} value={str(schema.unit)} onChange={event => { const next = { ...schema }; if (event.target.value) next.unit = event.target.value; else delete next.unit; setSchema(next); }} />}</Field>}
      {schema.type === 'enum' && <StringListEditor label={t('alpha7.move.catalog')} pointer="/migration/catalog" value={(schema.value_catalog ?? []) as string[]} onChange={value => setSchema({ ...schema, value_catalog: value })} />}
      {adapter && adapter.kind !== 'mqtt' && <Field label={t('alpha7.move.device')} hint={t('alpha7.move.deviceHelp')}>{props => <Select {...props} value={str(adapter.ha_entity_id)} onChange={event => setAdapter({ ...adapter, ha_entity_id: event.target.value })}>
        {!ha.entities.some(entity => entity.entity_id === adapter.ha_entity_id) && <option value={str(adapter.ha_entity_id)}>{t('alpha7.move.currentDevice')}</option>}
        {ha.entities.map(entity => <option key={entity.entity_id} value={entity.entity_id}>{entity.display_name} · {deviceValue(entity)}</option>)}
      </Select>}</Field>}
      {ha.isError && adapter?.kind !== 'mqtt' && <p className="text-sm text-muted">{t('alpha7.move.devicesUnavailable')}</p>}
      <section className="space-y-3"><h3 className="text-sm font-semibold">{t('alpha7.move.contracts')}</h3>
        {!users.length && <p className="text-sm text-muted">{t('alpha7.move.noContracts')}</p>}
        {objects(base.contracts).filter(contract => users.includes(str(contract.contract_id))).map(contract => {
          const contractId = str(contract.contract_id); const label = str(contract.display_name) || contractId;
          return <div key={contractId} className="space-y-2 rounded-lg border border-line p-3">
            <Checkbox label={label} checked={chosen.includes(contractId)} onCheckedChange={on => setChosen(previous => on ? [...previous, contractId] : previous.filter(id => id !== contractId))} />
            {chosen.includes(contractId) && <Collapsible title={t('alpha7.move.templateAdvanced')}><Field label={t('alpha7.move.templateFor', { name: label })}>{props => <Select {...props} value={templates[contractId] ? `${templates[contractId].type_id}@${templates[contractId].type_version}` : ''} onChange={event => { const next = { ...templates }; const definition = definitions.find(definition => `${str(definition.type_id)}@${String(definition.type_version)}` === event.target.value); if (definition) next[contractId] = { type_id: str(definition.type_id), type_version: Number(definition.type_version) }; else delete next[contractId]; setTemplates(next); }}><option value="">{t('alpha7.move.keepTemplate')}</option>{definitions.map(definition => <option key={`${str(definition.type_id)}@${String(definition.type_version)}`} value={`${str(definition.type_id)}@${String(definition.type_version)}`}>{str(definition.display_name) || str(definition.type_id)} · {t('alpha7.move.version', { version: String(definition.type_version) })}</option>)}</Select>}</Field></Collapsible>}
          </div>;
        })}
        <Checkbox checked={adapt} onCheckedChange={setAdapt} label={t('alpha7.move.adaptTemplate')} />
        <p className="text-xs text-muted">{t('alpha7.move.templateHelp')} <Link to="/templates/own" className="text-fg hover:underline">{t('alpha7.nav.ownTemplates')}</Link></p>
      </section>
      <Collapsible title={t('alpha7.device.advanced')}><div className="space-y-3">
        <Field label={t('alpha7.device.sourceId')}>{props => <Input {...props} value={id} onChange={event => setId(event.target.value)} maxLength={128} />}</Field>
        {adapter?.kind === 'ha_attribute' && <Field label={t('editors.RegistryObjects.attribut_ac1f2f')}>{props => <Input {...props} value={str(adapter.ha_attribute)} onChange={event => setAdapter({ ...adapter, ha_attribute: event.target.value })} />}</Field>}
        {adapter?.kind === 'mqtt' && <><Field label={t('editors.RegistryObjects.topic_7b08c3')}>{props => <Input {...props} value={str(adapter.mqtt_topic)} onChange={event => setAdapter({ ...adapter, mqtt_topic: event.target.value })} />}</Field><Field label={t('editors.RegistryObjects.wertpfad_optional_bf35e4')}>{props => <Input {...props} value={str(adapter.mqtt_value_path)} onChange={event => { const next = { ...adapter }; if (event.target.value) next.mqtt_value_path = event.target.value; else delete next.mqtt_value_path; setAdapter(next); }} />}</Field></>}
        <Field label={t('editors.RegistryObjects.transformation_cf2a63')}>{props => <Select {...props} value={transform?.kind ? str(transform.kind) : 'type_cast'} onChange={event => setTransform(event.target.value === 'type_cast' ? undefined : event.target.value === 'unit_scale' ? { kind: 'unit_scale', factor: 1, offset: 0 } : { kind: 'map_ref', catalog_id: '' })}>
          {schema.type !== 'enum' && <option value="type_cast">{t('editors.RegistryObjects.typumwandlung_8e7b19')}</option>}{schema.type === 'number' && <option value="unit_scale">{t('editors.RegistryObjects.einheit_skalieren_f57b15')}</option>}<option value="map_ref">{t('editors.RegistryObjects.katalog_zuordnung_b96c20')}</option>
        </Select>}</Field>
        {transform?.kind === 'unit_scale' && <div className="grid grid-cols-2 gap-3">{['factor', 'offset'].map(key => <Field key={key} label={t(key === 'factor' ? 'editors.RegistryObjects.faktor_105199' : 'editors.RegistryObjects.offset_b1a1e8')}>{props => <Input {...props} type="number" step="any" value={Number(transform[key])} onChange={event => setTransform({ ...transform, [key]: Number(event.target.value) })} />}</Field>)}</div>}
        {transform?.kind === 'map_ref' && <Field label={t('alpha7.move.mapping')}>{props => <Select {...props} value={str(transform.catalog_id)} onChange={event => setTransform({ ...transform, catalog_id: event.target.value })}><option value="">{t('alpha7.move.chooseMapping')}</option>{objects(base.catalogs).map(catalog => <option key={str(catalog.catalog_id)} value={str(catalog.catalog_id)}>{str(catalog.display_name) || str(catalog.catalog_id)}</option>)}</Select>}</Field>}
      </div></Collapsible>
    </fieldset>
    {disabledReason && disabledReason !== error && <p role="status" className="mt-4 text-sm text-muted">{disabledReason}</p>}
    {error && <p role="alert" className="mt-4 text-sm text-bad">{error}</p>}
    {issues.length > 0 && <div className="mt-4 space-y-2"><p className="text-sm">{t('alpha7.move.fixBeforeMove')}</p><IssueList issues={issues} config={plan?.config ?? base} /></div>}
    {fresh && <div role="status" className="mt-4 space-y-2 rounded-lg border border-line p-3 text-sm"><p>{t('alpha7.move.checked', { direct: review!.result.direct.length, indirect: review!.result.indirect.length, versions: review!.result.newVersions })}</p><p>{t('alpha7.move.retained')}</p><p>{t('alpha7.move.onlyDraft')}</p></div>}
    {!fresh && !disabledReason && <p className="mt-3 text-sm text-muted">{t('alpha7.move.reviewFirst')}</p>}
    {fresh && review?.diff && <DiffView diff={review.diff} config={review.result.config} />}
  </Dialog>{guard.confirmation}</>;
}
