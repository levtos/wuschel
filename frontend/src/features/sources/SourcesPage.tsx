import { Collapsible } from '@/components/ui/controls';
import { t as uiText } from '@/i18n';
import { Copy, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router';
import { MasterDetail } from '@/components/MasterDetail';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/dialog';
import { Empty, PageHeader } from '@/components/ui/layout';
import { ReadOnlyNotice } from '@/features/authoring/DraftBar';
import { useAllIssues } from '@/features/authoring/issues';
import { asArray, asObject, str, uniqueName } from '@/features/authoring/model';
import { bindingsUsingCatalog, consumersOfSource, sourcesUsingDependency } from '@/features/authoring/usage';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { BindingEditor, CatalogEditor, DependencyEditor, EntityPicker, SourceEditor, sourceCast } from '@/editors/RegistryObjects';
import { clone, isObject, setAt, type JsonObject } from '@/lib/json';
import { useActiveRevision, useCapabilities } from '@/lib/queries';
import { describeError } from '@/lib/api';
import { DeviceWizard } from './DeviceWizard';
import { CalendarSourceDialog } from './CalendarSourceDialog';
import { CalendarSourceStatus } from './CalendarSourceStatus';
import { ManualSourceDialog } from './ManualSourceDialog';
import { ManualSourcePanel } from './ManualSourcePanel';
import { codeLabel } from '@/i18n/codes';
import { useSourceSelection } from './useSourceSelection';
import { SourceMigrationDialog } from './SourceMigrationDialog';
import { ObjectActions } from '@/features/authoring/ObjectActions';
import { useRouteFilters } from '@/lib/route-filters';
import { useHaCatalog } from './useHaCatalog';

type Name = 'dependencies' | 'sources' | 'bindings' | 'catalogs';

const newSource = (config: JsonObject, ids: string[]): JsonObject => ({
  source_id: uniqueName('source.neu', ids), source_type: 'boolean', source_origin: 'local',
  freshness: { mode: 'event_stateful', future_tolerance_s: 1 },
  dependency_id: str(asArray(config.dependencies).filter(isObject)[0]?.dependency_id),
  physical_source_key: uniqueName('physical.neu', asArray(config.sources).filter(isObject).map(source => str(source.physical_source_key))),
});

interface CollectionProps {
  name: Name;
  idKey: string;
  singular: string;
  make: (config: JsonObject, ids: string[]) => JsonObject;
  render: (item: JsonObject, pointer: string, onChange: (next: JsonObject) => void, config: JsonObject) => ReactNode;
  subtitle: (item: JsonObject) => string;
  usage: (item: JsonObject, config: JsonObject) => ReactNode;
  copyKeys?: string[];
  update?: (config: JsonObject, next: JsonObject, pointer: string) => JsonObject;
  onMove?: (id: string) => void;
  onEntity?: (sourceId: string, bindingId?: string) => void;
}

import { freshnessLabel } from './freshness';

function Collection({ name, idKey, singular, make, render, subtitle, usage, onMove, onEntity, update }: CollectionProps) {
  const src = useAuthoringSource();
  const doc = useWorkingDocument();
  const active = useActiveRevision();
  const issues = useAllIssues();
  const config = src.config ?? {};
  const ha = useHaCatalog(name === 'sources');
  const list = useMemo(() => asArray(config[name]).filter(isObject), [config, name]);
  const [selected, setSelected, selectIdentity] = useSourceSelection(name, list, idKey, src.editable);
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  useEffect(() => {
    if (pendingSelection && src.editable && list.some(item => item[idKey] === pendingSelection)) { selectIdentity(pendingSelection); setPendingSelection(null); }
  }, [pendingSelection, src.editable, list]);
  const [removing, setRemoving] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const previousDraft = useRef(doc.draft?.draft_id);
  useEffect(() => { if (previousDraft.current && previousDraft.current !== doc.draft?.draft_id) setEditingId(null); previousDraft.current = doc.draft?.draft_id; }, [doc.draft?.draft_id]);
  useEffect(() => setEditingId(null), [name]);
  const [addError, setAddError] = useState('');
  const [filterParams, setFilterParams] = useSearchParams();
  const templateFilter = filterParams.get('freshness');
  const route = useRouteFilters(); const [deviations, setDeviations] = route.field('deviations', 'false');
  const deviationsOnly = deviations === 'true'; const setDeviationsOnly = (value: boolean) => setDeviations(value ? 'true' : 'false');
  const [listSearch, setListSearch] = route.field('list_search', '');

  const ids = list.map((item) => str(item[idKey]));
  const items = list.map((item, index) => ({
    key: String(index),
    label: name === 'sources' ? str(item.display_name) || ha.entities.find(entity => asArray(config.bindings).filter(isObject).some(binding => binding.source_id === item.source_id && isObject(binding.adapter) && binding.adapter.ha_entity_id === entity.entity_id))?.display_name || str(item[idKey]) : str(item[idKey]),
    sub: name === 'sources' ? `${str(item[idKey])} · ${freshnessLabel(config, item)}` : subtitle(item),
    errors: issues.filter((i) => i.path === `/${name}/${index}` || i.path.startsWith(`/${name}/${index}/`)).length,
  })).filter(item => { if (name !== 'sources') return true; const source = list[Number(item.key)]!; const selection = asObject(source.freshness_selection); return (!templateFilter || selection.template_id === templateFilter || selection.comparison_template_id === templateFilter) && (!deviationsOnly || freshnessLabel(config, source).includes(uiText('b4.differs'))); });
  const add = async () => {
    setAddError('');
    try {
      const current = await doc.ensureDraft();
      const item = make(current, asArray(current[name]).filter(isObject).map(item => str(item[idKey])));
      doc.edit((c) => setAt(c, `/${name}`, [...asArray(c[name]), item]));
      setPendingSelection(str(item[idKey])); setEditingId(str(item[idKey]));
    } catch (error) { setAddError(describeError(error)); }
  };
  const duplicate = (index: number) => {
    const copy = clone(list[index]!);
    copy[idKey] = uniqueName(`${str(copy[idKey])}_kopie`, ids);
    doc.edit((c) => setAt(c, `/${name}`, [...asArray(c[name]), copy]));
    selectIdentity(str(copy[idKey]));
    setEditingId(str(copy[idKey]));
  };
  const current = selected !== null ? list[selected] : undefined;
  const activeSource = name === 'sources' ? asArray(active.data?.config?.sources).filter(isObject).find(source => source.source_id === current?.source_id) : undefined;
  const activeBindings = asArray(active.data?.config?.bindings).filter(isObject);
  useEffect(() => {
    if (filterParams.get('edit') !== '1' || !current || !src.editable) return;
    if (filterParams.has('item') && selected !== Number(filterParams.get('item'))) return;
    if (filterParams.has(idKey) && current[idKey] !== filterParams.get(idKey)) return;
    setEditingId(str(current[idKey]));
    const next = new URLSearchParams(filterParams); next.delete('edit'); setFilterParams(next, { replace: true });
  }, [filterParams, current, src.editable]);
  const editable = src.editable && !!current && editingId === str(current[idKey]);
  const updateCurrent = (next: JsonObject) => {
    if (editable) setEditingId(str(next[idKey]));
    doc.edit(c => update ? update(c, next, `/${name}/${selected}`) : ({ ...setAt(c, `/${name}/${selected}`, next), ...(name === 'catalogs' && next.kind === 'managed' ? { config_schema_version: 3, fingerprint_version: 3 } : name === 'sources' && (next.sensor_profile || next.value_origin) ? { config_schema_version: Math.max(2, Number(c.config_schema_version ?? 1)) } : {}) }));
  };
  const beginEdit = async () => { setAddError(''); try { if (editable) { if (await doc.save() === 'saved') setEditingId(null); } else { const id = str(current?.[idKey]); const next = await doc.ensureDraft(); if (!asArray(next[name]).filter(isObject).some(row => row[idKey] === id)) throw new Error(uiText('b22.object.missing')); setPendingSelection(id); setEditingId(id); } } catch (error) { setAddError(describeError(error)); } };

  return (
    <>
      {name !== 'sources' && <PageHeader title={uiText(`alpha7.nav.${name}`)} description={uiText(`alpha9.pages.${name}`)} actions={<Button variant="primary" disabled={doc.starting || doc.saving} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => void add()}>{uiText(`alpha9.actions.add${name === 'bindings' ? 'Binding' : name === 'catalogs' ? 'Catalog' : 'Dependency'}`)}</Button>} />}
      {name !== 'sources' && <ReadOnlyNotice what={singular} />}
      {addError && <p role="alert" className="mb-3 text-sm text-bad">{addError}</p>}
      {name === 'sources' && <div className="mb-3 flex flex-wrap gap-3"><label className="inline-flex min-h-11 items-center gap-2"><input type="checkbox" checked={deviationsOnly} onChange={event => setDeviationsOnly(event.target.checked)} />{uiText('b4.differs')}</label>{templateFilter && <Button onClick={() => { const next = new URLSearchParams(filterParams); next.delete('freshness'); setFilterParams(next); }}>{uiText('b4.clearFilter')}</Button>}</div>}
      <MasterDetail
        title={singular}
        search={listSearch} onSearchChange={setListSearch}
        items={items}
        selected={current ? String(selected) : null}
        onSelect={(key) => setSelected(Number(key))}
        onAdd={undefined}
        addLabel={uiText("features.sources.SourcesPage.v0_hinzufugen_198d5a", { v0: (singular) })}
        empty={<Empty title={uiText("features.sources.SourcesPage.v0_wahlen_24a7a9", { v0: (singular) })}>{src.editable ? uiText("features.sources.SourcesPage.wahlen_sie_links_einen_eintrag_oder_legen_sie_mi_17add4") : uiText("features.sources.SourcesPage.wahlen_sie_links_einen_eintrag_zur_ansicht_257139")}</Empty>}
      >
        {current && selected !== null && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">{str(current.display_name) || str(current[idKey]) || uiText("features.sources.SourcesPage.neu_391955")}</h2>
              <ObjectActions key={`${name}:${str(current[idKey])}`} collection={name} idKey={idKey} value={current} editing={editable} onEdit={beginEdit} rename={name === 'sources' || name === 'bindings'} />
              {onEntity && (name === 'bindings' ? str(asObject(current.adapter).kind).startsWith('ha_') : asArray(config.bindings).filter(isObject).some(row => row.source_id === current.source_id && str(asObject(row.adapter).kind).startsWith('ha_'))) && <Button onClick={() => onEntity(name === 'bindings' ? str(current.source_id) : str(current[idKey]), name === 'bindings' ? str(current.binding_id) : undefined)}>{uiText('b22.entity.change')}</Button>}
              {onMove && <Button size="sm" onClick={() => onMove(str(current[idKey]))}>{uiText('alpha7.move.action')}</Button>}
              {editable && (
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => duplicate(selected)}><Copy size={13} /> {uiText("features.sources.SourcesPage.duplizieren_9d400c")}</Button>
                  <Button size="sm" variant="danger" onClick={() => setRemoving(selected)}><Trash2 size={13} /> {uiText("features.sources.SourcesPage.entfernen_382837")}</Button>
                </div>
              )}
            </div>
            <div className="text-xs text-muted">{usage(current, config)}</div>
            {activeSource && !editable && activeBindings.some(binding => binding.source_id === activeSource.source_id && asObject(binding.adapter).kind === 'ha_calendar') && <CalendarSourceStatus sourceId={str(activeSource.source_id)} />}
            {activeSource?.origin === 'manual' && !editable ? <>
              <ManualSourcePanel key={str(current.source_id)} sourceId={str(current.source_id)} />
              <Collapsible title={uiText('alpha9.manual.configuration')}><fieldset disabled={!editable} className="min-w-0">
              {render(current, `/${name}/${selected}`, updateCurrent, config)}
            </fieldset></Collapsible>
            </> : <><fieldset disabled={!editable} className="min-w-0">
              {render(current, `/${name}/${selected}`, updateCurrent, config)}
            </fieldset></>}
          </div>
        )}
      </MasterDetail>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={uiText("features.sources.SourcesPage.v0_entfernen_f1cd8a", { v0: (singular) })}
        description={<>„{removing !== null ? str(list[removing]?.[idKey]) : ''}{uiText("features.sources.SourcesPage.wird_aus_dem_draft_entfernt_verweise_darauf_werd_295ba2")}</>}
        confirmLabel={uiText("features.sources.SourcesPage.entfernen_382837")}
        tone="danger"
        onConfirm={() => {
          if (removing !== null) doc.edit((c) => setAt(c, `/${name}/${removing}`, undefined));
          setRemoving(null);
          setSelected(null);
        }}
      />
    </>
  );
}

const chips = (label: string, ids: string[], to?: (id: string) => string) =>
  ids.length === 0 ? (
    <span>{label}: –</span>
  ) : (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {label}:
      {ids.slice(0, 8).map((id) => (to ? <Link key={id} to={to(id)} className="rounded-full border border-line-strong bg-raised px-2 py-0.5 font-mono text-[11px] text-fg hover:border-faint">{id}</Link> : <Badge key={id}>{id}</Badge>))}
      {ids.length > 8 && <span>+{ids.length - 8}</span>}
    </span>
  );

function SourceSection({ items }: { items: { value: string; label: string; content: ReactNode }[] }) {
  const section = useLocation().pathname.split('/')[2] ?? 'devices';
  return <>{items.find(item => item.value === (section === 'devices' ? 'sources' : section))?.content}</>;
}

export default function SourcesPage() {
  const caps = useCapabilities();
  const location = useLocation();
  const [picker, setPicker] = useState<null | ((entityId: string, attributes: string[]) => void)>(null);
  const [addingDevice, setAddingDevice] = useState(false);
  const [addingCalendar, setAddingCalendar] = useState(false);
  const [addingManual, setAddingManual] = useState(false);
  const [deviceError, setDeviceError] = useState('');
  const [movingSource, setMovingSource] = useState<string | null>(null);
  const [changingEntity, setChangingEntity] = useState<{ sourceId: string; bindingId?: string } | null>(null);
  const doc = useWorkingDocument();
  const [params, setParams] = useSearchParams();
  const addDevice = async () => { setDeviceError(''); try { await doc.ensureDraft(); setAddingDevice(true); } catch (error) { setDeviceError(describeError(error)); } };
  const addWithoutDevice = async () => {
    try {
      const config = await doc.ensureDraft();
      const sources = asArray(config.sources);
      doc.edit(current => setAt(current, '/sources', [...sources, newSource(config, sources.filter(isObject).map(source => str(source.source_id)))]));
      setAddingDevice(false);
      setParams({ item: String(sources.length), edit: '1' }, { replace: true });
    } catch (error) { setDeviceError(describeError(error)); }
  };
  const beginMove = async (id: string) => { setDeviceError(''); try { await doc.ensureDraft(); setMovingSource(id); } catch (error) { setDeviceError(describeError(error)); } };
  const beginEntity = async (sourceId: string, bindingId?: string) => { setDeviceError(''); try { await doc.ensureDraft(); setChangingEntity({ sourceId, bindingId }); } catch (error) { setDeviceError(describeError(error)); } };
  const requestedMove = params.get('move');
  useEffect(() => {
    if (!requestedMove) return;
    const next = new URLSearchParams(params); next.delete('move'); setParams(next, { replace: true });
    void beginMove(requestedMove);
  }, [requestedMove]);
  const registrySchema = caps.data?.schemas.registry;
  return (
    <>
      {location.pathname === '/sources/devices' && <PageHeader title={uiText('alpha7.nav.sources')} description={uiText('alpha9.pages.sources')} actions={<Button variant="primary" disabled={doc.starting || doc.saving} onClick={() => void addDevice()}>{uiText('alpha9.actions.addSource')}</Button>} />}
      {deviceError && <p role="alert" className="mb-3 text-sm text-bad">{deviceError}</p>}
      <DeviceWizard open={addingDevice} onOpenChange={setAddingDevice} manualOption={<><Button onClick={() => { setAddingDevice(false); setAddingManual(true); }}>{uiText('alpha9.manual.title')}</Button><Button onClick={() => { setAddingDevice(false); setAddingCalendar(true); }}>{uiText('alpha9.calendar.add')}</Button><Button onClick={() => void addWithoutDevice()}>{uiText('alpha7.device.withoutDevice')}</Button></>} onAdded={result => setParams({ tab: 'sources', item: String(result.sourceIndex), edit: '1' }, { replace: true })} />
      <ManualSourceDialog open={addingManual} onOpenChange={setAddingManual} onAdded={index => setParams({ tab: 'sources', item: String(index), edit: '1' }, { replace: true })} />
      <CalendarSourceDialog open={addingCalendar} onOpenChange={setAddingCalendar} onAdded={index => setParams({ tab: 'sources', item: String(index), edit: '1' }, { replace: true })} />
      {changingEntity && <SourceMigrationDialog {...changingEntity} entityOnly onClose={() => setChangingEntity(null)} />}
      {movingSource && <SourceMigrationDialog key={movingSource} sourceId={movingSource} onClose={() => setMovingSource(null)} />}
      {location.pathname === '/sources/devices' && <ReadOnlyNotice what={uiText("features.sources.SourcesPage.sources_bindings_613cff")} />}
      <SourceSection
        items={[
          {
            value: 'sources',
            label: uiText("features.sources.SourcesPage.sources_caf85b"),
            content: (
              <Collection
                name="sources"
                idKey="source_id"
                singular={uiText("features.sources.SourcesPage.source_0e570c")}
                onMove={id => void beginMove(id)}
                onEntity={(sourceId, bindingId) => void beginEntity(sourceId, bindingId)}
                subtitle={(s) => `${codeLabel(s.source_type)} · ${codeLabel((isObject(s.freshness) ? s.freshness : {}).mode)}`}
                make={newSource}
                usage={(item, config) => {
                  const used = consumersOfSource(config, str(item.source_id));
                  return <span className="flex flex-col gap-1">{chips(uiText('alpha7.nav.bindings'), used.bindings)}{chips(uiText('alpha7.nav.contracts'), used.contracts, (id) => `/contracts/view/${encodeURIComponent(id)}`)}{used.sources.length > 0 && chips(uiText("features.sources.SourcesPage.liveness_fur_09aba4"), used.sources)}</span>;
                }}
                render={(item, pointer, onChange, config) => (
                  <SourceEditor
                    freshnessConfig={config}
                    sources={asArray(config.sources).filter(isObject)}
                    value={item}
                    pointer={pointer}
                    onChange={onChange}
                    schema={registrySchema}
                    dependencyIds={asArray(config.dependencies).filter(isObject).map((d) => str(d.dependency_id))}
                    sourceIds={asArray(config.sources).filter(isObject).map((s) => str(s.source_id))}
                    livenessSourceIds={asArray(config.sources).filter(isObject).filter((s) => s.source_type === 'boolean' || (isObject(s.value_schema) && s.value_schema.type === 'bool')).map((s) => str(s.source_id))}
                  />
                )}
              />
            ),
          },
          {
            value: 'bindings',
            label: uiText("features.sources.SourcesPage.bindings_7697ab"),
            content: (
              <Collection
                name="bindings"
                idKey="binding_id"
                update={(current, next, pointer) => {
                  const adapter = asObject(next.adapter);
                  const changed = setAt(current, pointer, next);
                  if (adapter.kind !== 'ha_calendar') return next.refresh !== undefined ? { ...changed, config_schema_version: Math.max(2, Number(current.config_schema_version ?? 1)) } : changed;
                  return { ...changed, config_schema_version: Math.max(2, Number(current.config_schema_version ?? 1)), sources: asArray(current.sources).map(source => isObject(source) && source.source_id === next.source_id ? { ...source, freshness: { ...asObject(source.freshness), mode: 'periodic_ttl', interval_s: adapter.max_age_s ?? 3600 } } : source) };
                }}
                onEntity={(sourceId, bindingId) => void beginEntity(sourceId, bindingId)}
                singular={uiText("features.sources.SourcesPage.binding_164c69")}
                subtitle={(b) => `${codeLabel((isObject(b.adapter) ? b.adapter : {}).kind)} → ${str(b.source_id)}`}
                make={(c, ids) => {
                  const source = asArray(c.sources).filter(isObject)[0];
                  const transform = sourceCast(source);
                  return { binding_id: uniqueName('binding.neu', ids), source_id: str(source?.source_id), adapter: { kind: 'ha_state' }, ...(transform ? { transform } : {}) };
                }}
                usage={(item, config) => (
                  <span className="flex flex-col gap-1">
                    {chips(uiText('alpha7.contract.source'), [str(item.source_id)].filter(Boolean))}
                    {chips(uiText("features.sources.SourcesPage.contracts_uber_diese_quelle_22a5c9"), consumersOfSource(config, str(item.source_id)).contracts, (id) => `/contracts/view/${encodeURIComponent(id)}`)}
                  </span>
                )}
                render={(item, pointer, onChange, config) => (
                  <BindingEditor
                    value={item}
                    pointer={pointer}
                    onChange={onChange}
                    schema={registrySchema}
                    sourceIds={asArray(config.sources).filter(isObject).map((s) => str(s.source_id))}
                    catalogIds={asArray(config.catalogs).filter(isObject).map((c) => str(c.catalog_id))}
                    sources={asArray(config.sources).filter(isObject)}
                    onPickEntity={(apply) => setPicker(() => apply)}
                  />
                )}
              />
            ),
          },
          {
            value: 'catalogs',
            label: uiText("features.sources.SourcesPage.kataloge_daf282"),
            content: (
              <Collection
                name="catalogs"
                idKey="catalog_id"
                singular={uiText("features.sources.SourcesPage.katalog_1c58e0")}
                subtitle={(c) => uiText("features.sources.SourcesPage.v_v0_v1_eintrage_a26921", { v0: (String(c.version ?? 1)), v1: (Object.keys(isObject(c.entries) ? c.entries : {}).length) })}
                make={(_, ids) => ({ catalog_id: uniqueName('catalog.neu', ids), kind: 'map', version: 1, entries: { an: true } })}
                usage={(item, config) => chips(uiText("features.sources.SourcesPage.verwendet_von_bindings_6b4d50"), bindingsUsingCatalog(config, str(item.catalog_id)))}
                render={(item, pointer, onChange) => <CatalogEditor value={item} pointer={pointer} onChange={onChange} />}
              />
            ),
          },
          {
            value: 'dependencies',
            label: uiText("features.sources.SourcesPage.dependencies_2e41b1"),
            content: (
              <Collection
                name="dependencies"
                idKey="dependency_id"
                singular={uiText("features.sources.SourcesPage.dependency_711595")}
                subtitle={(d) => `${codeLabel(d.kind)} · ${codeLabel(d.monitor)}`}
                make={(_, ids) => ({ dependency_id: uniqueName('dep.neu', ids), kind: 'ha', monitor: 'internal' })}
                usage={(item, config) => chips(uiText('alpha7.nav.sources'), sourcesUsingDependency(config, str(item.dependency_id)))}
                render={(item, pointer, onChange, config) => (
                  <DependencyEditor value={item} pointer={pointer} onChange={onChange} schema={registrySchema} sourceIds={asArray(config.sources).filter(isObject).map((s) => str(s.source_id))} />
                )}
              />
            ),
          },
        ]}
      />
      <EntityPicker open={picker !== null} onOpenChange={(o) => !o && setPicker(null)} onPick={(entity, attributes) => picker?.(entity, attributes)} />
    </>
  );
}
