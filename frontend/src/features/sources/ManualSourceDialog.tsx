import { useState } from 'react';
import { t } from '@/i18n';
import { Checkbox } from '@/components/ui/controls';
import { Field, Input, Select } from '@/components/ui/form';
import { DurationField as SecondsField } from '@/components/SecondsField';
import { FieldSchemaEditor } from '@/editors/FieldSchemaEditor';
import { asArray, newFieldSchema, str, uniqueName, type FieldSchema } from '@/features/authoring/model';
import { DraftEditor, useEditorBase } from '@/features/authoring/DraftEditor';
import { clone, deepEqual, isObject, type JsonObject } from '@/lib/json';

export function planManual(config: JsonObject, name: string, schema: FieldSchema, seconds: number | null, future: boolean) {
  if (!name.trim() || seconds !== null && (!Number.isFinite(seconds) || seconds <= 0)) throw new Error(t('alpha9.form.unavailable'));
  const next = clone(config); const sources = asArray(next.sources).filter(isObject); const bindings = asArray(next.bindings).filter(isObject); const dependencies = asArray(next.dependencies).filter(isObject);
  let dep = dependencies.find(item => item.kind === 'manual' && item.monitor === 'internal');
  if (!dep) { dep = { dependency_id: uniqueName('dep.manual', dependencies.map(item => str(item.dependency_id))), kind: 'manual', monitor: 'internal' }; dependencies.push(dep); }
  const sourceId = uniqueName('source.angabe', sources.map(item => str(item.source_id)));
  sources.push({ source_id: sourceId, display_name: name.trim(), source_type: schema.type === 'bool' ? 'boolean' : schema.type === 'number' ? 'numeric' : schema.type === 'text' ? 'text' : 'state', source_origin: 'local', origin: 'manual', manual_allow_future: future,
    dependency_id: dep.dependency_id!, physical_source_key: uniqueName('manual.angabe', sources.map(item => str(item.physical_source_key))), value_schema: schema,
    freshness: seconds === null ? { mode: 'event_stateful', future_tolerance_s: 0 } : { mode: 'periodic_ttl', interval_s: seconds, future_tolerance_s: 0 } });
  bindings.push({ binding_id: uniqueName('binding.angabe', bindings.map(item => str(item.binding_id))), source_id: sourceId, adapter: { kind: 'manual' } });
  return { config: { ...next, config_schema_version: Math.max(2, Number(next.config_schema_version ?? 1)), sources, bindings, dependencies }, sourceId, sourceIndex: sources.length - 1 };
}

interface ManualSourceProps { open: boolean; onOpenChange: (open: boolean) => void; onAdded: (index: number) => void }
export function ManualSourceDialog(props: ManualSourceProps) {
  return props.open ? <ManualSourceEditor {...props} /> : null;
}

function ManualSourceEditor({ onOpenChange, onAdded }: ManualSourceProps) {
  const base = useEditorBase();
  const [name, setName] = useState(''); const [schema, setSchema] = useState<FieldSchema>(() => newFieldSchema('bool'));
  const [seconds, setSeconds] = useState<number | null>(3600); const [future, setFuture] = useState(false);
  const plan = () => planManual(base, name, schema, seconds, future);
  return <DraftEditor base={base} dirty={!!name || seconds !== 3600 || future || !deepEqual(schema, newFieldSchema('bool'))} input={[name, schema, seconds, future]} ready={!!name.trim() && (seconds === null || Number.isFinite(seconds) && seconds > 0)} plan={plan} title={t('alpha9.manual.title')} description={t('alpha9.manual.description')} onClose={() => onOpenChange(false)} onSaved={saved => {
    const id = plan().sourceId;
    onAdded(asArray(saved.config.sources).findIndex(row => isObject(row) && row.source_id === id));
  }}>
    <div className="space-y-4"><Field label={t('alpha7.device.name')}>{props => <Input {...props} maxLength={128} value={name} onChange={event => setName(event.target.value)} />}</Field>
      <FieldSchemaEditor compact pointer="/value_schema" value={schema} onChange={setSchema} />
      <Field label={t('alpha9.manual.validUntil')}>{props => <Select {...props} value={seconds === null ? 'unbounded' : 'duration'} onChange={event => setSeconds(event.target.value === 'unbounded' ? null : 3600)}><option value="duration">{t('alpha9.deadline.duration')}</option><option value="unbounded">{t('alpha9.manual.unbounded')}</option></Select>}</Field>
      {seconds !== null && <SecondsField label={t('alpha9.deadline.duration')} value={seconds} min={1} max={31536000} onChange={setSeconds} />}
      <Checkbox label={t('alpha9.manual.future')} checked={future} onCheckedChange={setFuture} />
    </div>
  </DraftEditor>;
}
