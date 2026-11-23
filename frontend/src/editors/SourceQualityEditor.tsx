import { t } from '@/i18n';
import { Checkbox, Collapsible } from '@/components/ui/controls';
import { Field, Input, Select } from '@/components/ui/form';
import { asObject, str } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';

export function newSensorProfile(sourceId: string) {
  const names = ['unchanged_for_s', 'tolerance', 'min_reports', 'recovery_reports'];
  return { id: `profile.${sourceId.replace(/^source\./, '')}`, version: 1, display_name: t('alpha9.sensor.defaultName'),
    stuck: Object.fromEntries(names.map(name => [name, { op: 'parameter', name }])),
    parameter_schema: Object.fromEntries(names.map(name => [name, { schema: { type: 'number', required: true, physical_state: false }, minimum: name === 'tolerance' ? 0 : 1 }])) };
}

export function SourceQualityEditor({ value, onChange, sources = [] }: { value: JsonObject; onChange: (next: JsonObject) => void; sources?: JsonObject[] }) {
  const origin = asObject(value.value_origin); const profile = asObject(value.sensor_profile); const stuck = asObject(profile.stuck); const parameters = asObject(value.sensor_parameters);
  const numeric = asObject(value.value_schema).type === 'number' && value.origin !== 'manual' && (!origin.kind || origin.kind === 'measured');
  const profiles = [...new Map(sources.filter(item => item.sensor_profile).map(item => { const p = asObject(item.sensor_profile); return [`${p.id}:${p.version}`, item] as const; })).values()];
  const nextVersion = () => Math.max(Number(profile.version), ...profiles.filter(item => asObject(item.sensor_profile).id === profile.id).map(item => Number(asObject(item.sensor_profile).version))) + 1;
  const slot = (name: string) => Number(typeof stuck[name] === 'number' ? stuck[name] : parameters[str(asObject(stuck[name]).name)] ?? 0);
  const changeSlot = (name: string, number: number) => {
    const pointer = asObject(stuck[name]);
    if (pointer.op === 'parameter') onChange({ ...value, sensor_parameters: { ...parameters, [str(pointer.name)]: number } });
    else onChange({ ...value, sensor_profile: { ...profile, version: nextVersion(), stuck: { ...stuck, [name]: { op: 'parameter', name } }, parameter_schema: { ...asObject(profile.parameter_schema), [name]: { schema: { type: 'number', required: true, physical_state: false }, minimum: name === 'tolerance' ? 0 : 1 } } }, sensor_parameters: { ...parameters, [name]: number } });
  };
  const ref = asObject(stuck.reference);
  return <section className="space-y-4 rounded border border-line p-3">
    <Field label={t('alpha9.valueOrigin.label')} help={t('a2.help.valueOrigin')}>{props => <Select {...props} value={str(origin.kind)} onChange={event => { const next = { ...value }; if (event.target.value) next.value_origin = { kind: event.target.value, provider: str(origin.provider) }; else delete next.value_origin; onChange(next); }}><option value="">{t('alpha9.valueOrigin.unknown')}</option>{['measured', 'replacement', 'model', 'forecast'].map(kind => <option key={kind} value={kind}>{t(`alpha9.valueOrigin.${kind}`)}</option>)}</Select>}</Field>
    {['model', 'replacement', 'forecast'].includes(str(origin.kind)) && <p className="text-xs text-muted">{t(`alpha9.valueOrigin.${origin.kind}Help`)}</p>}
    {numeric && <>
      <Checkbox label={t('alpha9.sensor.profile')} checked={!!value.sensor_profile} onCheckedChange={enabled => { const next = { ...value }; if (enabled) { next.sensor_profile = newSensorProfile(str(value.source_id)); next.sensor_parameters = { unchanged_for_s: 1814400, tolerance: .05, min_reports: 10, recovery_reports: 2 }; } else { delete next.sensor_profile; delete next.sensor_parameters; } onChange(next); }} />
      <p className="text-xs text-muted">{t('alpha9.sensor.help')}</p>
      {!!value.sensor_profile && <>
        <Field label={t('alpha9.sensor.choose')}>{props => <Select {...props} value={`${profile.id}:${profile.version}`} onChange={event => { const selected = profiles.find(item => { const p = asObject(item.sensor_profile); return `${p.id}:${p.version}` === event.target.value; }); if (selected) onChange({ ...value, sensor_profile: selected.sensor_profile!, sensor_parameters: selected.sensor_parameters ?? {} }); }}><option value={`${profile.id}:${profile.version}`}>{str(profile.display_name) || str(profile.id)}</option>{profiles.filter(item => asObject(item.sensor_profile).id !== profile.id).map(item => { const p = asObject(item.sensor_profile); return <option key={`${p.id}:${p.version}`} value={`${p.id}:${p.version}`}>{str(p.display_name) || str(p.id)}</option>; })}</Select>}</Field>
        <div className="grid gap-3 sm:grid-cols-2">{([
          ['unchanged_for_s', 'alpha9.sensor.unchangedFor', 1/86400, 365, 86400], ['tolerance', 'alpha9.sensor.tolerance', 0, undefined, 1],
          ['min_reports', 'alpha9.sensor.minReports', 2, 10000, 1], ['recovery_reports', 'alpha9.sensor.recovery', 1, 100, 1],
        ] as const).map(([name, label, min, max, scale]) => <Field key={name} label={t(label)}>{props => <Input {...props} type="number" min={min} max={max} step={name === 'tolerance' || name === 'unchanged_for_s' ? 'any' : 1} value={slot(name)/scale} onChange={event => changeSlot(name, event.target.value === '' ? 0 : event.target.valueAsNumber*scale)} />}</Field>)}</div>
        <Field label={t('alpha9.sensor.reference')}>{props => <Select {...props} value={str(ref.ref)} onChange={event => { const next = { ...stuck }; if (event.target.value) next.reference = { ref: event.target.value, min_delta: 1 }; else delete next.reference; onChange({ ...value, sensor_profile: { ...profile, version: nextVersion(), stuck: next } }); }}><option value="">{t('editors.RegistryObjects.keine_db028e')}</option>{sources.filter(item => item.source_id !== value.source_id && asObject(item.value_schema).type === 'number' && asObject(item.value_schema).unit === asObject(value.value_schema).unit).map(item => <option key={str(item.source_id)} value={str(item.source_id)}>{str(item.display_name) || str(item.source_id)}</option>)}</Select>}</Field>
        {!!ref.ref && <Field label={t('alpha9.sensor.referenceDelta')}>{props => <Input {...props} type="number" step="any" min={.000001} value={typeof ref.min_delta === 'number' ? ref.min_delta : Number(parameters[str(asObject(ref.min_delta).name)] ?? 1)} onChange={event => { const name = str(asObject(ref.min_delta).name); if (name) onChange({ ...value, sensor_parameters: { ...parameters, [name]: (event.target.value === '' ? 0 : event.target.valueAsNumber) } }); else onChange({ ...value, sensor_profile: { ...profile, version: nextVersion(), stuck: { ...stuck, reference: { ...ref, min_delta: { op: 'parameter', name: 'reference_delta' } } }, parameter_schema: { ...asObject(profile.parameter_schema), reference_delta: { schema: { type: 'number', required: true, physical_state: false }, minimum: .000001 } } }, sensor_parameters: { ...parameters, reference_delta: (event.target.value === '' ? 0 : event.target.valueAsNumber) } }); }} />}</Field>}
      </>}
    </>}
    <Collapsible title={t('editors.RegistryObjects.erweitert_25e56e')}>
      {!!origin.kind && <Field label={t('alpha9.valueOrigin.provider')}>{props => <Input {...props} value={str(origin.provider)} maxLength={128} onChange={event => onChange({ ...value, value_origin: { ...origin, provider: event.target.value } })} />}</Field>}
      {!!value.sensor_profile && <Field label={t('alpha9.sensor.profileVersion')}>{props => <Input {...props} type="number" min={1} value={Number(profile.version)} onChange={event => onChange({ ...value, sensor_profile: { ...profile, version: (event.target.value === '' ? 0 : event.target.valueAsNumber) } })} />}</Field>}
    </Collapsible>
  </section>;
}
