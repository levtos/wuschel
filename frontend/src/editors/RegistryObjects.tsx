import { SourceQualityEditor } from './SourceQualityEditor';
import { codeLabel, fieldLabel } from '@/i18n/codes';
import { t as uiText } from '@/i18n';
import { Search, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, Switch } from '@/components/ui/controls';
import { Dialog } from '@/components/ui/dialog';
import { Input, Select, Textarea } from '@/components/ui/form';
import { Notice, Spinner } from '@/components/ui/layout';
import { typeKey, type RefOption, type TypeEntry } from '@/features/authoring/catalog';
import { asArray, asObject, newFieldSchema, str, type FieldSchema } from '@/features/authoring/model';
import { enumAt, requiredWhen, type JsonSchema } from '@/lib/capabilities';
import { join, type Json, type JsonObject } from '@/lib/json';
import { useHaEntities } from '@/lib/queries';
import { sourceCast } from '@/lib/sourceCast';
import { formatNumber } from '@/lib/format';
import { Link } from 'react-router';
import { CalendarFields } from '@/features/sources/CalendarSourceDialog';
import { BindingRefreshEditor } from '@/features/sources/BindingRefreshEditor';
import { RefreshPlanEditor, type RefreshSource } from './RefreshPlanEditor';
import { AddButton, Fld, IconButton, IssuesAt, NumberField, SectionTitle } from './common';
import { FieldSchemaEditor } from './FieldSchemaEditor';
import { ValueEditor } from './ValueEditor';
import { SchemaForm } from './SchemaForm';
import { Extras } from './Extras';
import { SourceFreshnessEditor } from '@/features/sources/SourceFreshnessEditor';

function patcher(value: JsonObject, onChange: (next: JsonObject) => void) {
  return (patch: Record<string, Json | undefined>) => {
    const next: JsonObject = { ...value };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === '') delete next[k];
      else next[k] = v;
    }
    onChange(next);
  };
}

/* ───────────────────────────── Dependency ───────────────────────────── */

export function DependencyEditor({ value, onChange, pointer, sourceIds, schema }: { value: JsonObject; onChange: (n: JsonObject) => void; pointer: string; sourceIds: string[]; schema?: JsonSchema }) {
  const set = patcher(value, onChange);
  const needsSource = requiredWhen(schema, 'Dependency', 'monitor', str(value.monitor)).includes('source_id') || value.monitor === 'source';
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Fld label={uiText("editors.RegistryObjects.dependency_id_62578d")} pointer={join(pointer, 'dependency_id')}>
        {(p) => <Input {...p} value={str(value.dependency_id)} onChange={(e) => set({ dependency_id: e.target.value })} />}
      </Fld>
      <Fld label={uiText("editors.RegistryObjects.art_75df35")} pointer={join(pointer, 'kind')}>
        {(p) => (
          <Select {...p} value={str(value.kind)} onChange={(e) => set({ kind: e.target.value })}>
            {enumAt(schema, 'Dependency', 'kind', ['ha', 'mqtt', 'other']).map((k) => (
              <option key={k} value={k}>{codeLabel(k)}</option>
            ))}
          </Select>
        )}
      </Fld>
      <Fld label={uiText("editors.RegistryObjects.uberwachung_93542e")} pointer={join(pointer, 'monitor')} hint={uiText("editors.RegistryObjects.internal_die_app_uberwacht_selbst_source_eine_qu_135fdc")}>
        {(p) => (
          <Select {...p} value={str(value.monitor)} onChange={(e) => set({ monitor: e.target.value, ...(e.target.value === 'internal' ? { source_id: undefined } : {}) })}>
            {enumAt(schema, 'Dependency', 'monitor', ['internal', 'source']).map((k) => (
              <option key={k} value={k}>{codeLabel(k)}</option>
            ))}
          </Select>
        )}
      </Fld>
      <div className="sm:col-span-2"><Extras def="Dependency" value={value} pointer={pointer} onChange={onChange} /></div>
      {needsSource && (
        <Fld label={uiText("editors.RegistryObjects.uberwachende_quelle_4f9c7d")} pointer={join(pointer, 'source_id')}>
          {(p) => (
            <Select {...p} value={str(value.source_id)} onChange={(e) => set({ source_id: e.target.value })}>
              <option value="">{uiText("editors.RegistryObjects.wahlen_602246")}</option>
              {sourceIds.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </Select>
          )}
        </Fld>
      )}
    </div>
  );
}

/* ─────────────────────────────── Source ─────────────────────────────── */

export function SourceEditor({ value, onChange, pointer, dependencyIds, sourceIds, livenessSourceIds = sourceIds, sources = [], schema, freshnessConfig }: { value: JsonObject; onChange: (n: JsonObject) => void; pointer: string; dependencyIds: string[]; sourceIds: string[]; livenessSourceIds?: string[]; sources?: JsonObject[]; schema?: JsonSchema; freshnessConfig?: JsonObject }) {
  const set = patcher(value, onChange);
  const freshness = asObject(value.freshness);
  const mode = str(freshness.mode);
  const setFreshness = (patch: Record<string, Json | undefined>) => {
    const next: JsonObject = { ...freshness };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === '') delete next[k];
      else next[k] = v;
    }
    onChange({ ...value, freshness: next });
  };
  const hasSchema = value.value_schema !== undefined && value.value_schema !== null;
  const manual = value.origin === 'manual';
  const userType = asObject(value.value_schema).type === 'enum' ? 'selection' : str(value.source_type);
  const fp = join(pointer, 'freshness');
  return (
    <div className="flex flex-col gap-5">
      <Fld label={uiText('alpha7.device.name')} pointer={join(pointer, 'display_name')}>
        {(p) => <Input {...p} value={str(value.display_name)} onChange={(e) => set({ display_name: e.target.value })} />}
      </Fld>
      <Collapsible title={uiText('alpha7.device.advanced')}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Fld label={uiText("editors.RegistryObjects.source_id_a3a484")} pointer={join(pointer, 'source_id')} hint={uiText("editors.RegistryObjects.beginnt_mit_source_ea3ff7")}>
          {(p) => <Input {...p} value={str(value.source_id)} onChange={(e) => set({ source_id: e.target.value })} />}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.typ_1b0241")} pointer={join(pointer, 'source_type')}>
          {(p) => (
            <Select {...p} value={userType} onChange={(e) => {
              const type = e.target.value;
              const fieldType = ({ numeric: 'number', boolean: 'bool', text: "text", selection: 'enum' } as const)[type as 'numeric' | 'boolean' | 'text' | 'selection'];
              set({ source_type: type === 'selection' ? 'state' : type, ...(hasSchema || type === 'selection' ? { value_schema: { ...asObject(value.value_schema), ...newFieldSchema(fieldType) } } : {}) });
            }}>
              <option value="numeric">{uiText("editors.RegistryObjects.zahl_654eb4")}</option><option value="boolean">{uiText("editors.RegistryObjects.ja_nein_0d2c61")}</option><option value="text">{uiText("editors.RegistryObjects.text_71988c")}</option><option value="selection">{uiText("editors.RegistryObjects.auswahl_e8b7ad")}</option>
              {!['numeric', 'boolean', 'text', 'selection'].includes(userType) && <option value={userType}>{codeLabel(userType)}</option>}
            </Select>
          )}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.herkunft_514fce")} pointer={join(pointer, 'source_origin')} hint={uiText("editors.RegistryObjects.z_b_local_f170b9")}>
          {(p) => <Input {...p} value={str(value.source_origin)} onChange={(e) => set({ source_origin: e.target.value })} />}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.physischer_schlussel_36a2c7")} pointer={join(pointer, 'physical_source_key')} hint={uiText("editors.RegistryObjects.identitat_der_physischen_messung_verhindert_dopp_afdbad")}>
          {(p) => <Input {...p} value={str(value.physical_source_key)} onChange={(e) => set({ physical_source_key: e.target.value })} />}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.dependency_711595")} pointer={join(pointer, 'dependency_id')}>
          {(p) => (
            <Select {...p} value={str(value.dependency_id)} onChange={(e) => set({ dependency_id: e.target.value })}>
              {!dependencyIds.includes(str(value.dependency_id)) && <option value={str(value.dependency_id)}>{str(value.dependency_id) || uiText("editors.RegistryObjects.wahlen_602246")}</option>}
              {dependencyIds.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </Select>
          )}
        </Fld>
      </div>

      <Collapsible title={uiText("editors.RegistryObjects.erweitert_25e56e")}>
        <Fld label={uiText("editors.RegistryObjects.technische_quellenart_e949b0")} pointer={join(pointer, 'source_type')}>
          {(p) => <Select {...p} value={str(value.source_type)} onChange={(e) => set({ source_type: e.target.value })}>{enumAt(schema, 'Source', 'source_type', ['state', 'attribute', 'event', 'numeric', 'text', 'boolean']).map((k) => <option key={k} value={k}>{codeLabel(k)}</option>)}</Select>}
        </Fld>
      </Collapsible>
      </Collapsible>

      {value.freshness_selection ? <SourceFreshnessEditor value={value} onChange={onChange} sources={sources} config={freshnessConfig} /> : <section className="flex flex-col gap-3">
        <SectionTitle>{uiText("editors.RegistryObjects.frische_freshness_be29ab")}</SectionTitle>
        {!manual && <Link className="inline-flex min-h-11 items-center underline" to="/sources/freshness">{uiText('b4.assign')}</Link>}
        <p className="text-xs text-muted">{uiText(`alpha7.freshness.${mode}Help`)}</p>
        <IssuesAt pointer={fp} />
        <div className="grid gap-3 sm:grid-cols-4">
          <Fld label={uiText("editors.RegistryObjects.modus_45517c")} pointer={join(fp, 'mode')} className="sm:col-span-2">
            {(p) => (
               <Select {...p} value={mode} onChange={(e) => {
                 const nextMode = e.target.value;
                 const needs = requiredWhen(schema, 'Freshness', 'mode', nextMode);
                  setFreshness({ mode: nextMode, ...(needs.includes('interval_s') && freshness.interval_s == null ? { interval_s: 60 } : {}) });
               }}>
                {enumAt(schema, 'Freshness', 'mode', ['report_heartbeat', 'liveness_source', 'periodic_ttl', 'event_stateful']).filter(k => !manual || k === 'periodic_ttl' || k === 'event_stateful').map((k) => (
                  <option key={k} value={k}>{codeLabel(k)}</option>
                ))}
              </Select>
            )}
          </Fld>
          {!manual && <NumberField label={uiText("editors.RegistryObjects.zukunftstoleranz_s_a11f48")} pointer={join(fp, 'future_tolerance_s')} min={0} value={Number(freshness.future_tolerance_s ?? 0)} onChange={(n) => setFreshness({ future_tolerance_s: n })} />}
          {mode !== 'event_stateful' && (
            <NumberField label={uiText("editors.RegistryObjects.intervall_s_v0_3a6c0e", { v0: ' *' })} hint={uiText(Number(freshness.interval_s ?? 60) === 60 ? 'alpha7.freshness.oneMinute' : 'alpha7.freshness.minutes', { minutes: formatNumber(Number(freshness.interval_s ?? 60) / 60, 1) })} pointer={join(fp, 'interval_s')} min={1} value={Number(freshness.interval_s ?? 60)} onChange={(n) => setFreshness({ interval_s: n })} />
          )}
        </div>
        {mode === 'liveness_source' && (
          <Fld label={uiText("editors.RegistryObjects.liveness_quelle_66259e")} hint={uiText('post1.freshness.livenessRequired')} pointer={join(fp, 'liveness_source')}>
            {(p) => (
              <Select {...p} value={str(freshness.liveness_source)} onChange={(e) => setFreshness({ liveness_source: e.target.value })}>
                <option value="">{uiText("editors.RegistryObjects.wahlen_602246")}</option>
                {livenessSourceIds.filter((id) => id !== str(value.source_id)).map((id) => (
                  <option key={id} value={id}>{id}</option>
                ))}
              </Select>
            )}
          </Fld>
        )}
        {mode === 'event_stateful' && <p className="text-xs text-muted">{uiText("editors.RegistryObjects.zustandsbasiert_gilt_bis_ein_neues_ereignis_eint_8c5c3d")}</p>}
        <Extras def="Freshness" value={freshness} pointer={fp} onChange={(next) => onChange({ ...value, freshness: next })} />
      </section>}

      <Collapsible title={uiText('alpha7.device.schemaDetails')}>
        <Switch checked={hasSchema} onCheckedChange={(on) => set({ value_schema: on ? newFieldSchema(value.source_type === 'boolean' ? 'bool' : value.source_type === 'text' ? 'text' : 'number') : undefined })} label={uiText("editors.RegistryObjects.eigenes_wertschema_deklarieren_value_schema_82fd15")} />
        <p className="text-xs text-muted">{uiText("editors.RegistryObjects.quellen_der_typen_boolean_numeric_text_haben_ihr_e970bc")}</p>
        {hasSchema && <FieldSchemaEditor pointer={join(pointer, 'value_schema')} value={asObject(value.value_schema) as FieldSchema} onChange={(next) => set({ value_schema: next })} />}
      </Collapsible>
      {manual && <section className="space-y-3">
        <p className="text-sm text-muted">{uiText('alpha9.manual.origin')}</p>
        <Switch label={uiText('alpha9.manual.future')} checked={value.manual_allow_future === true} onCheckedChange={on => set({ manual_allow_future: on || undefined })} />
        {asObject(value.value_schema).type === 'number' && <div className="grid gap-3 sm:grid-cols-2">{(['manual_minimum', 'manual_maximum'] as const).map(key => <Fld key={key} label={uiText(`alpha9.manual.${key}`)} pointer={join(pointer, key)}>{props => <Input {...props} type="number" step="any" value={typeof value[key] === 'number' ? Number(value[key]) : ''} onChange={event => set({ [key]: event.target.value === '' ? undefined : event.target.valueAsNumber })} />}</Fld>)}</div>}
      </section>}
      <SourceQualityEditor value={value} onChange={onChange} sources={sources} />
      <Extras def="Source" value={value} pointer={pointer} onChange={onChange} />
    </div>
  );
}

/* ─────────────────────────────── Binding ────────────────────────────── */

export { sourceCast } from '@/lib/sourceCast';

export function BindingEditor({ value, onChange, pointer, sourceIds, sources = [], catalogIds, schema, onPickEntity }: { value: JsonObject; onChange: (n: JsonObject) => void; pointer: string; sourceIds: string[]; sources?: JsonObject[]; catalogIds: string[]; schema?: JsonSchema; onPickEntity: (apply: (entityId: string, attributes: string[]) => void) => void }) {
  const set = patcher(value, onChange);
  const adapter = asObject(value.adapter);
  const kind = str(adapter.kind);
  const castFor = (id: string) => sourceCast(sources.find((s) => s.source_id === id));
  const cast = castFor(str(value.source_id));
  const required = requiredWhen(schema, 'AdapterConfig', 'kind', kind);
  const ap = join(pointer, 'adapter');
  const setAdapter = (patch: Record<string, Json | undefined>) => {
    const next: JsonObject = { ...adapter };
    const copy = { ...value };
    if (patch.kind && patch.kind !== kind) {
      for (const key of ['window', 'refresh_s', 'max_age_s', 'ha_entity_id', 'ha_attribute', 'ha_time_attribute', 'mqtt_topic', 'mqtt_value_path']) delete next[key];
      delete copy.refresh;
      if (patch.kind === 'ha_calendar') { next.window = { timezone: 'Europe/Berlin', past_days: 1, future_days: 14 }; next.refresh_s = 900; next.max_age_s = 3600; delete copy.transform; }
    }
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === '') delete next[k];
      else next[k] = v;
    }
    onChange({ ...copy, adapter: next });
  };
  const transform = value.transform === null || value.transform === undefined ? null : asObject(value.transform);
  const tp = join(pointer, 'transform');
  const setTransform = (next: JsonObject | undefined) => set({ transform: next });
  const transformKind = transform ? str(transform.kind) : '';
  const ha = kind.startsWith('ha_');

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <Fld label={uiText("editors.RegistryObjects.binding_id_45ba45")} pointer={join(pointer, 'binding_id')}>
          {(p) => <Input {...p} value={str(value.binding_id)} onChange={(e) => set({ binding_id: e.target.value })} />}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.quelle_037889")} pointer={join(pointer, 'source_id')}>
          {(p) => (
            <Select {...p} value={str(value.source_id)} onChange={(e) => set({ source_id: e.target.value, ...(kind.startsWith('ha_') ? { transform: castFor(e.target.value) } : {}) })}>
              {!sourceIds.includes(str(value.source_id)) && <option value={str(value.source_id)}>{str(value.source_id) || uiText("editors.RegistryObjects.wahlen_602246")}</option>}
              {sourceIds.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </Select>
          )}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.anzeigename_3ab2ea")} pointer={join(pointer, 'display_name')}>
          {(p) => <Input {...p} value={str(value.display_name)} onChange={(e) => set({ display_name: e.target.value })} />}
        </Fld>
      </div>

      <section className="flex flex-col gap-3">
        <SectionTitle>{uiText("editors.RegistryObjects.adapter_0252b8")}</SectionTitle>
        <IssuesAt pointer={ap} />
        <Fld label={uiText("editors.RegistryObjects.art_75df35")} pointer={join(ap, 'kind')} className="max-w-xs">
          {(p) => (
            <Select {...p} value={kind} onChange={(e) => setAdapter({ kind: e.target.value })}>
              {enumAt(schema, 'AdapterConfig', 'kind', ['ha_state', 'ha_attribute', 'ha_calendar', 'mqtt']).filter((k) => k !== 'scheduler').map((k) => (
                <option key={k} value={k}>{codeLabel(k)}</option>
              ))}
            </Select>
          )}
        </Fld>
        {ha && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Fld label={uiText("editors.RegistryObjects.ha_entitat_96b970")} pointer={join(ap, 'ha_entity_id')} hint={uiText("editors.RegistryObjects.auswahl_uber_die_schreibgeschutzte_ha_erkennung__c0d877")}>
              {(p) => (
                <div className="flex gap-2">
                  <Input {...p} className="font-mono text-xs" value={str(adapter.ha_entity_id)} placeholder={uiText("editors.RegistryObjects.sensor_beispiel_be295b")} onChange={(e) => setAdapter({ ha_entity_id: e.target.value })} />
                  <Button variant="outline" aria-label={uiText("editors.RegistryObjects.ha_entitat_auswahlen_bed2ae")} onClick={() => onPickEntity((entity, attributes) => setAdapter({ ha_entity_id: entity, ...(kind === 'ha_attribute' && attributes.length && !attributes.includes(str(adapter.ha_attribute)) ? { ha_attribute: undefined } : {}) }))}>
                    <Search size={14} /> {uiText("editors.RegistryObjects.wahlen_eda09c")}</Button>
                </div>
              )}
            </Fld>
            {(kind === 'ha_attribute' || required.includes('ha_attribute')) && (
              <Fld label={uiText("editors.RegistryObjects.attribut_ac1f2f")} pointer={join(ap, 'ha_attribute')}>
                {(p) => <Input {...p} className="font-mono text-xs" value={str(adapter.ha_attribute)} onChange={(e) => setAdapter({ ha_attribute: e.target.value })} />}
              </Fld>
            )}
            <Fld label={uiText("editors.RegistryObjects.zeit_attribut_optional_6aedff")} pointer={join(ap, 'ha_time_attribute')} hint={uiText("editors.RegistryObjects.attribut_mit_dem_messzeitpunkt_falls_vorhanden_296ae8")}>
              {(p) => <Input {...p} className="font-mono text-xs" value={str(adapter.ha_time_attribute)} onChange={(e) => setAdapter({ ha_time_attribute: e.target.value })} />}
            </Fld>
          </div>
        )}
        {kind === 'ha_calendar' && <CalendarFields value={{ past: Number(asObject(adapter.window).past_days ?? 1), future: Number(asObject(adapter.window).future_days ?? 14), refreshMinutes: Number(adapter.refresh_s ?? 900)/60, validMinutes: Number(adapter.max_age_s ?? 3600)/60 }} onChange={next => setAdapter({ window: { timezone: 'Europe/Berlin', past_days: next.past, future_days: next.future }, refresh_s: next.refreshMinutes*60, max_age_s: next.validMinutes*60 })} />}
        {kind === 'mqtt' && (
          <div className="grid gap-3 sm:grid-cols-3">
            <Fld label={uiText("editors.RegistryObjects.topic_7b08c3")} pointer={join(ap, 'mqtt_topic')}>
              {(p) => <Input {...p} className="font-mono text-xs" value={str(adapter.mqtt_topic)} onChange={(e) => setAdapter({ mqtt_topic: e.target.value })} />}
            </Fld>
            <Fld label={uiText("editors.RegistryObjects.wertpfad_optional_bf35e4")} pointer={join(ap, 'mqtt_value_path')} hint={uiText("editors.RegistryObjects.pfad_im_json_payload_5eecf1")}>
              {(p) => <Input {...p} className="font-mono text-xs" value={str(adapter.mqtt_value_path)} onChange={(e) => setAdapter({ mqtt_value_path: e.target.value })} />}
            </Fld>
            <Fld label={uiText("editors.RegistryObjects.zeitpfad_optional_790a47")} pointer={join(ap, 'mqtt_time_path')}>
              {(p) => <Input {...p} className="font-mono text-xs" value={str(adapter.mqtt_time_path)} onChange={(e) => setAdapter({ mqtt_time_path: e.target.value })} />}
            </Fld>
          </div>
        )}
        <Extras def="AdapterConfig" value={adapter} pointer={ap} onChange={(next) => onChange({ ...value, adapter: next })} />
        {ha && kind !== 'ha_calendar' && <BindingRefreshEditor value={value} onChange={onChange} />}
        {kind === 'scheduler' && <p className="text-xs text-muted">{uiText("editors.RegistryObjects.der_scheduler_ist_kein_quellen_adapter_und_wird__39b766")}</p>}
      </section>

      <section className="flex flex-col gap-3">
        <SectionTitle>{uiText("editors.RegistryObjects.transformation_cf2a63")}</SectionTitle>
        {ha && !transform && cast && <Button size="sm" onClick={() => setTransform(cast)}>{uiText("editors.RegistryObjects.umwandlung_erganzen_bb9be5")}</Button>}
        <IssuesAt pointer={tp} />
        <Fld label={uiText("editors.RegistryObjects.transformation_cf2a63")} pointer={join(tp, 'kind')} className="max-w-xs">
          {(p) => (
            <Select
              {...p}
              value={transformKind}
              onChange={(e) => {
                const k = e.target.value;
                if (!k) return setTransform(undefined);
                setTransform({ ...transform, ...(k === 'type_cast' ? (cast ?? { kind: k, target: 'text' }) : k === 'unit_scale' ? { kind: k, factor: 1, offset: 0 } : { kind: k, catalog_id: catalogIds[0] ?? '' }) });
              }}
            >
              <option value="">{uiText("editors.RegistryObjects.keine_db028e")}</option>
              {enumAt(schema, 'Transform', 'kind', ['type_cast', 'unit_scale', 'map_ref']).map((k) => (
                <option key={k} value={k}>{k === 'type_cast' ? uiText("editors.RegistryObjects.typumwandlung_8e7b19") : k === 'unit_scale' ? uiText("editors.RegistryObjects.einheit_skalieren_f57b15") : uiText("editors.RegistryObjects.katalog_zuordnung_b96c20")}</option>
              ))}
            </Select>
          )}
        </Fld>
        {transform && transformKind === 'type_cast' && (
          <Fld label={uiText("editors.RegistryObjects.zieltyp_46a669")} pointer={join(tp, 'target')} className="max-w-xs">
            {(p) => (
              <Select {...p} value={str(transform.target)} onChange={(e) => setTransform({ ...transform, target: e.target.value })}>
                {enumAt(schema, 'Transform', 'target', ['bool', 'number', 'text']).map((k) => (
                  <option key={k} value={k}>{codeLabel(k)}</option>
                ))}
              </Select>
            )}
          </Fld>
        )}
        {transform && transformKind === 'unit_scale' && (
          <div className="grid max-w-md gap-3 sm:grid-cols-2">
            <NumberField label={uiText("editors.RegistryObjects.faktor_105199")} pointer={join(tp, 'factor')} value={Number(transform.factor ?? 1)} onChange={(n) => setTransform({ ...transform, factor: n })} hint={uiText("editors.RegistryObjects.wert_faktor_offset_d0477e")} />
            <NumberField label={uiText("editors.RegistryObjects.offset_b1a1e8")} pointer={join(tp, 'offset')} value={Number(transform.offset ?? 0)} onChange={(n) => setTransform({ ...transform, offset: n })} />
          </div>
        )}
        {transform && transformKind === 'map_ref' && (
          <Fld label={uiText("editors.RegistryObjects.katalog_1c58e0")} pointer={join(tp, 'catalog_id')} className="max-w-xs">
            {(p) => (
              <Select {...p} value={str(transform.catalog_id)} onChange={(e) => setTransform({ ...transform, catalog_id: e.target.value })}>
                {!catalogIds.includes(str(transform.catalog_id)) && <option value={str(transform.catalog_id)}>{str(transform.catalog_id) || uiText("editors.RegistryObjects.wahlen_602246")}</option>}
                {catalogIds.map((id) => (
                  <option key={id} value={id}>{id}</option>
                ))}
              </Select>
            )}
          </Fld>
        )}
        {transform && <Extras def="Transform" value={transform} pointer={tp} onChange={(next) => setTransform(next)} />}
      </section>
      <Extras def="Binding" value={value} pointer={pointer} onChange={onChange} />
    </div>
  );
}

/** Read-only HA entity discovery: choose an entity (never creates anything in Home Assistant). */
export function EntityPicker({ open, onOpenChange, onPick }: { open: boolean; onOpenChange: (o: boolean) => void; onPick: (entityId: string, attributes: string[]) => void }) {
  const [search, setSearch] = useState('');
  const query = useHaEntities('all', open);
  const [pages, setPages] = useState(1);
  // A search can only see loaded pages: fetch the rest (bounded by the server's 10 000 entity cap).
  useEffect(() => {
    if (open && search && query.hasNextPage && !query.isFetchingNextPage && pages < 60) {
      setPages((n) => n + 1);
      void query.fetchNextPage();
    }
  }, [open, search, query, pages]);
  const all = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const needle = search.trim().toLowerCase();
  const shown = (needle ? all.filter((e) => e.entity_id.toLowerCase().includes(needle) || e.display_name.toLowerCase().includes(needle)) : all).slice(0, 300);
  const unavailable = query.isError;
  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg" title={uiText("editors.RegistryObjects.home_assistant_entitat_wahlen_6ad8d6")} description={uiText("editors.RegistryObjects.schreibgeschutzte_erkennung_es_werden_nur_kennun_776d6f")}>
      <div className="flex flex-col gap-3">
        <Input autoFocus aria-label={uiText("editors.RegistryObjects.entitaten_durchsuchen_4437b8")} placeholder={uiText("editors.RegistryObjects.suchen_nach_name_oder_entity_id_61f175")} value={search} onChange={(e) => setSearch(e.target.value)} />
        {query.isPending && <Spinner label={uiText("editors.RegistryObjects.entitaten_werden_gelesen_c2b17f")} />}
        {unavailable && <Notice tone="warn" title={uiText("editors.RegistryObjects.erkennung_derzeit_nicht_verfugbar_521c30")}>{uiText("editors.RegistryObjects.die_entitaten_id_kann_trotzdem_direkt_im_feld_ei_6ffc69")}</Notice>}
        <ul className="max-h-[50vh] divide-y divide-line overflow-auto rounded-[var(--radius-md)] border border-line">
          {shown.map((entity) => (
            <li key={entity.entity_id}>
              <button
                type="button"
                className="flex w-full cursor-pointer flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-raised"
                onClick={() => {
                  onPick(entity.entity_id, entity.attributes);
                  onOpenChange(false);
                }}
              >
                <span className="flex flex-wrap items-center gap-2">
                  <code className="text-sm">{entity.entity_id}</code>
                  {entity.unit && <Badge>{entity.unit}</Badge>}
                  {entity.device_class && <Badge tone="info">{entity.device_class}</Badge>}
                </span>
                {entity.display_name !== entity.entity_id && <span className="text-xs text-muted">{entity.display_name}</span>}
                {entity.attributes.length > 0 && <span className="line-clamp-1 text-[11px] text-faint">{uiText("editors.RegistryObjects.attribute_8a0a7a")}{entity.attributes.slice(0, 12).join(', ')}{entity.attributes.length > 12 ? ' …' : ''}</span>}
              </button>
            </li>
          ))}
          {!query.isPending && shown.length === 0 && !unavailable && <li className="px-3 py-6 text-center text-sm text-muted">{uiText("editors.RegistryObjects.keine_treffer_6df7e9")}{query.hasNextPage ? uiText("editors.RegistryObjects.weitere_seiten_werden_geladen_f45e5e") : ''}.</li>}
        </ul>
        {!needle && query.hasNextPage && (
          <div>
            <Button variant="outline" disabled={query.isFetchingNextPage} disabledReason={uiText('alpha7.copy.inProgress')} onClick={() => void query.fetchNextPage()}>
              {uiText("editors.RegistryObjects.weitere_laden_f77bb3")}</Button>
          </div>
        )}
      </div>
    </Dialog>
  );
}

/* ─────────────────────────────── Catalog ────────────────────────────── */

export function CatalogEditor({ value, onChange, pointer }: { value: JsonObject; onChange: (n: JsonObject) => void; pointer: string }) {
  const set = patcher(value, onChange);
  const entries = Object.entries(asObject(value.entries));
  const setEntries = (next: [string, Json][]) => onChange({ ...value, entries: Object.fromEntries(next) });
  const keys = new Set(entries.map(([k]) => k));
  const kindField = <Fld label={uiText('alpha10.catalog.kind')} pointer={join(pointer, 'kind')}>{p => <Select {...p} value={str(value.kind)} onChange={e => {
    if (e.target.value === 'managed') onChange({ catalog_id: value.catalog_id ?? '', version: value.version ?? 1, kind: 'managed', identity_rule: { version: 1, fields: ['scope', 'media_type', 'signal_type', 'normalized_key'], normalizer: 'normalize_key' }, classification_schema: { type: 'enum', values: ['other'] }, unassigned_value: 'other', master_depth: 1 });
    else onChange({ catalog_id: value.catalog_id ?? '', version: value.version ?? 1, kind: 'map', entries: {} });
  }}><option value="map">{uiText('alpha10.catalog.inline')}</option><option value="managed">{uiText('alpha10.catalog.managed')}</option></Select>}</Fld>;
  if (value.kind === 'managed') return <div className="space-y-4">
    <Fld label={uiText('editors.RegistryObjects.katalog_id_e6aea3')} pointer={join(pointer, 'catalog_id')}>{p => <Input {...p} value={str(value.catalog_id)} onChange={e => set({ catalog_id: e.target.value })} />}</Fld>
    {kindField}
    <NumberField label={uiText('editors.RegistryObjects.version_dd1679')} pointer={join(pointer, 'version')} min={1} step={1} value={Number(value.version ?? 1)} onChange={n => set({ version: Math.max(1, Math.trunc(n)) })} />
    <Fld label={uiText('alpha10.catalog.classes')} pointer={join(pointer, 'classification_schema')}>{p => <Textarea {...p} value={asArray(asObject(value.classification_schema).values).map(str).join('\n')} onChange={e => set({ classification_schema: { type: 'enum', values: e.target.value.split('\n') } })} />}</Fld>
    <Fld label={uiText('alpha10.catalog.default')} pointer={join(pointer, 'unassigned_value')}>{p => <Select {...p} value={str(value.unassigned_value)} onChange={e => set({ unassigned_value: e.target.value })}>{asArray(asObject(value.classification_schema).values).map(str).map(name => <option key={name} value={name}>{name}</option>)}</Select>}</Fld>
    <p className="text-sm text-muted">{uiText('alpha10.catalog.definitionHelp')}</p>
  </div>;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Fld label={uiText("editors.RegistryObjects.katalog_id_e6aea3")} pointer={join(pointer, 'catalog_id')}>
          {(p) => <Input {...p} value={str(value.catalog_id)} onChange={(e) => set({ catalog_id: e.target.value })} />}
        </Fld>
        {kindField}
        <NumberField label={uiText("editors.RegistryObjects.version_dd1679")} pointer={join(pointer, 'version')} min={1} step={1} value={Number(value.version ?? 1)} onChange={(n) => set({ version: Math.max(1, Math.trunc(n)) })} />
      </div>
      <SectionTitle
        aside={
          <AddButton
            onClick={() => {
              let n = 1;
              while (keys.has(`schluessel${n}`)) n += 1;
              setEntries([...entries, [`schluessel${n}`, '']]);
            }}
          >
            {uiText("editors.RegistryObjects.eintrag_99b925")}</AddButton>
        }
      >
        {uiText("editors.RegistryObjects.zuordnungen_rohwert_wert_656ea9")}</SectionTitle>
      <p className="text-xs text-muted">{uiText("editors.RegistryObjects.schlussel_sind_die_rohwerte_der_quelle_als_text__256b6c")}<code>{uiText("editors.RegistryObjects.on_b8d31e")}</code>{uiText("editors.RegistryObjects.werte_werden_per_map_ref_eingesetzt_1dbcfa")}</p>
      <IssuesAt pointer={join(pointer, 'entries')} />
      {entries.map(([key, entry], i) => (
        <div key={i} className="grid items-start gap-2 rounded-[var(--radius-md)] border border-line p-2 sm:grid-cols-[minmax(10rem,16rem)_1fr_auto]">
          <Fld label={uiText("editors.RegistryObjects.rohwert_a3deeb")} pointer={join(pointer, 'entries', key)}>
            {(p) => (
              <Input
                {...p}
                defaultValue={key}
                key={`${i}:${key}`}
                onBlur={(e) => {
                  const to = e.target.value;
                  if (to === key || keys.has(to)) return void (e.target.value = key);
                  setEntries(entries.map(([k, v], j) => (j === i ? [to, v] : [k, v])));
                }}
              />
            )}
          </Fld>
          <ValueEditor label={uiText("editors.RegistryObjects.wert_fur_v0_a5063f", { v0: (key || uiText("editors.RegistryObjects.leer_ac5c66")) })} pointer={join(pointer, 'entries', key)} value={entry} onChange={(next) => setEntries(entries.map(([k, v], j) => (j === i ? [k, next] : [k, v])))} />
          <div className="pt-5">
            <IconButton label={uiText("editors.RegistryObjects.eintrag_entfernen_706a15")} tone="danger" onClick={() => setEntries(entries.filter((_, j) => j !== i))}>
              <Trash2 size={14} />
            </IconButton>
          </div>
        </div>
      ))}
      <Extras def="Catalog" value={value} pointer={pointer} onChange={onChange} />
    </div>
  );
}

/* ────────────────────────── Contract instance ───────────────────────── */

export function ContractEditor({ value, onChange, pointer, types, refs, onAddDevice, refreshSources = [] }: { value: JsonObject; onChange: (n: JsonObject) => void; pointer: string; types: TypeEntry[]; refs: RefOption[]; onAddDevice?: (input: string) => void; refreshSources?: RefreshSource[] }) {
  const set = patcher(value, onChange);
  const key = typeKey(str(value.type_id), Number(value.type_version));
  const entry = types.find((t) => t.key === key);
  const inputs = asArray(value.inputs).map(asObject);
  const declared = entry?.inputs ?? [];
  const refFor = (name: string) => inputs.find((i) => str(i.name) === name);
  const setRef = (name: string, ref: string) => {
    const without = inputs.filter((i) => str(i.name) !== name);
    onChange({ ...value, inputs: ref ? [...without, { name, ref }].sort((a, b) => declared.findIndex((d) => d.name === str(a.name)) - declared.findIndex((d) => d.name === str(b.name))) : without });
  };
  const undeclared = inputs.filter((i) => !declared.some((d) => d.name === str(i.name)));
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-3 sm:grid-cols-2">
        <Fld label={uiText("editors.RegistryObjects.contract_id_f9f7ff")} pointer={join(pointer, 'contract_id')} hint={uiText("editors.RegistryObjects.z_b_wohnzimmer_licht_807245")}>
          {(p) => <Input {...p} value={str(value.contract_id)} onChange={(e) => set({ contract_id: e.target.value })} />}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.anzeigename_3ab2ea")} pointer={join(pointer, 'display_name')}>
          {(p) => <Input {...p} value={str(value.display_name)} onChange={(e) => set({ display_name: e.target.value })} />}
        </Fld>
        <Fld label={uiText("editors.RegistryObjects.typ_version_1b7183")} pointer={join(pointer, 'type_id')} hint={entry ? undefined : uiText("editors.RegistryObjects.dieser_typ_ist_weder_als_paket_typ_noch_als_defi_0b1092")}>
          {(p) => (
            <Select
              {...p}
              value={key}
              onChange={(e) => {
                const next = types.find((t) => t.key === e.target.value);
                if (next) onChange({ ...value, type_id: next.type_id, type_version: next.type_version, parameters: {} });
              }}
            >
              {!entry && <option value={key}>{key}</option>}
              {types.filter(t => t.lifecycle !== 'retired' || t.key === key).map((t) => (
                <option key={t.key} value={t.key}>{t.type_id} {uiText("editors.RegistryObjects.v_0bb917")}{t.type_version}{t.origin === 'draft-definition' ? uiText("editors.RegistryObjects.draft_definition_fd7330") : t.declarative ? uiText("editors.RegistryObjects.definition_96ce96") : uiText("editors.RegistryObjects.paket_92cb87")}</option>
              ))}
            </Select>
          )}
        </Fld>
        <div className="flex items-end pb-1.5">
          <Switch checked={value.enabled !== false} onCheckedChange={(on) => set({ enabled: on })} label={uiText("editors.RegistryObjects.aktiviert_10639c")} />
        </div>
      </div>
      {entry?.lifecycle && entry.lifecycle !== 'active' && <Notice tone="warn" title={codeLabel(entry.lifecycle)}>{uiText('alpha8.lifecycle.existing')}</Notice>}
      <Switch checked={value.ha_projection === true} disabled={value.ha_projection !== true && (!entry?.fields.value || entry.fields.value.type === 'object')} onCheckedChange={on => set({ ha_projection: on || undefined })} label={uiText('beta5.projection.label')} />
      <p className="text-xs text-muted">{uiText('beta5.projection.hint')}</p>
      <Fld label={uiText("editors.RegistryObjects.beschreibung_e9262f")} pointer={join(pointer, 'description')}>
        {(p) => <Textarea {...p} rows={2} className="font-sans text-sm" value={str(value.description)} onChange={(e) => set({ description: e.target.value })} />}
      </Fld>

      <section className="flex flex-col gap-3">
        <SectionTitle>{uiText("editors.RegistryObjects.eingange_bindung_an_quellen_und_andere_contracts_c3d83b")}</SectionTitle>
        <IssuesAt pointer={join(pointer, 'inputs')} />
        {declared.length === 0 && undeclared.length === 0 && <p className="text-xs text-faint">{uiText("editors.RegistryObjects.dieser_typ_deklariert_keine_eingange_8d0dc9")}</p>}
        {declared.map((input) => {
          const current = refFor(input.name);
          const index = inputs.findIndex((i) => str(i.name) === input.name);
          const ref = str(current?.ref);
          const ip = index >= 0 ? join(pointer, 'inputs', index) : join(pointer, 'inputs');
          const availableRefs = refs.filter((ref) => !input.schema || (ref.schema && (input.schema.type === 'object' || ref.schema.type === input.schema.type) && (input.schema.unit ?? '') === (ref.schema.unit ?? '')));
          return (
            <div key={input.name} className="grid items-start gap-2 sm:grid-cols-[12rem_1fr]">
              <div className="pt-6 text-sm">
                <code>{input.name}</code>{' '}
                <Badge tone={input.required ? 'accent' : 'neutral'}>{input.required ? uiText("editors.RegistryObjects.pflicht_6a047f") : uiText("editors.RegistryObjects.optional_ec91fd")}</Badge>
                {input.schema && <Badge className="ml-1">{codeLabel(input.schema.type)}{input.schema.unit ? uiText("editors.RegistryObjects.v0_8b43bb", { v0: (String(input.schema.unit)) }) : ''}</Badge>}
              </div>
              <div className="space-y-2"><Fld label={uiText("editors.RegistryObjects.referenz_e13113")} pointer={join(ip, 'ref')}>
                {(p) => (
                  <Select {...p} value={ref} onChange={(e) => setRef(input.name, e.target.value)}>
                    <option value="">{input.required ? uiText("editors.RegistryObjects.wahlen_602246") : uiText("editors.RegistryObjects.nicht_gebunden_4105e9")}</option>
                    {ref && !availableRefs.some((r) => r.value === ref) && <option value={ref}>{ref} {uiText("editors.RegistryObjects.ungultige_oder_unbekannte_referenz_f8a620")}</option>}
                    <optgroup label={uiText("editors.RegistryObjects.quellen_c78600")}>
                      {availableRefs.filter((r) => r.kind === 'source').map((r) => (
                        <option key={r.value} value={r.value}>{r.label} · {codeLabel(r.detail)}</option>
                      ))}
                    </optgroup>
                    <optgroup label={uiText("editors.RegistryObjects.contracts_0488ac")}>
                      {availableRefs.filter((r) => r.kind === 'contract').map((r) => (
                        <option key={r.value} value={r.value}>{r.label} · {codeLabel(r.detail)}</option>
                      ))}
                    </optgroup>
                  </Select>
                )}
              </Fld>{onAddDevice && <Button size="sm" onClick={() => onAddDevice(input.name)}>{uiText('alpha7.device.missingSource')}</Button>}</div>
            </div>
          );
        })}
        {undeclared.map((input) => (
          <Notice key={str(input.name)} tone="warn" title={uiText("editors.RegistryObjects.eingang_v0_ist_im_typ_nicht_deklariert_d04398", { v0: (str(input.name)) })}>
            <Button size="sm" variant="outline" onClick={() => setRef(str(input.name), '')}>{uiText("editors.RegistryObjects.entfernen_382837")}</Button>
          </Notice>
        ))}
      </section>

      <section className="flex flex-col gap-3">
        <SectionTitle>{uiText('alpha8.parameters.title')}</SectionTitle>
        <p className="text-xs text-muted">{uiText('alpha8.parameters.draftHint')}</p>
        {entry?.parameterSchema ? (
          <SchemaForm root={entry.parameterSchema} node={entry.parameterSchema} value={asObject(value.parameters)} pointer={join(pointer, 'parameters')} onChange={(next) => set({ parameters: (next ?? {}) as JsonObject })} />
        ) : (
          <p className="text-xs text-faint">{uiText("editors.RegistryObjects.keine_parameter_bekannt_272f5e")}</p>
        )}
        <IssuesAt pointer={join(pointer, 'parameters')} />
      </section>
      <RefreshPlanEditor value={value.refresh_plan && typeof value.refresh_plan === 'object' ? asObject(value.refresh_plan) : null} onChange={refresh_plan => set({ refresh_plan })} sources={refreshSources} references={[...refs, ...Object.entries(entry?.fields ?? {}).map(([name, schema]) => ({ value: `${str(value.contract_id)}.${name}`, label: uiText('alpha9.refresh.reference', { name: str(value.display_name) || str(value.contract_id), field: fieldLabel(name) }), kind: 'contract' as const, schema }))]} />
      <Extras def="Contract" value={value} pointer={pointer} onChange={onChange} />
    </div>
  );
}
