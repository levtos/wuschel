import { t } from '@/i18n';
import { Checkbox } from '@/components/ui/controls';
import { Field, Select } from '@/components/ui/form';
import { SecondsField } from '@/components/SecondsField';
import { asArray, asObject, str } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';
import { isObject } from '@/lib/json';
import { RuleEditor } from './RuleEditor';
import type { Rule } from '@/features/authoring/model';

export function MachineDeadlinesEditor({ value, definition, onChange, pointer = '/machine' }: { value: JsonObject; definition: JsonObject; onChange: (next: JsonObject) => void; pointer?: string }) {
  const states = asArray(value.states).map(String); const durations = asObject(value.deadline_by_state);
  const parameters = Object.entries(asObject(definition.parameter_schema)).filter(([, raw]) => asObject(asObject(raw).schema).type === 'number').map(([name]) => name);
  const stamps = asArray(definition.inputs).map(asObject).filter(input => asObject(input.schema).type === 'timestamp').map(input => str(input.name));
  const absolute = asObject(value.absolute_transition); const enabled = value.unknown_deadline === 'keep_and_block_transition';
  const change = (patch: JsonObject) => onChange({ ...value, ...patch });
  const duration = (state: string, next: JsonObject | null) => { const copy = { ...durations }; if (next) copy[state] = next; else delete copy[state]; change({ deadline_by_state: copy }); };
  return <section className="space-y-3 rounded border border-line p-3">
    <Checkbox label={t('alpha9.deadline.unknown')} checked={enabled} onCheckedChange={on => { const copy = { ...value }; if (on) { copy.unknown_deadline = 'keep_and_block_transition'; copy.deadline_by_state = Object.fromEntries(states.map(state => [state, { duration: value.deadline_s ?? 60 }])); delete copy.deadline_s; } else { delete copy.unknown_deadline; delete copy.deadline_by_state; delete copy.absolute_transition; copy.transitions = asArray(value.transitions).map(raw => { const item = { ...asObject(raw) }; delete item.deadline_action; return item; }); } onChange(copy); }} />
    <p className="text-xs text-muted">{t('alpha9.deadline.unknownHelp')}</p>
    {enabled && <>
      {states.map(state => { const rule = asObject(durations[state]); const parameter = asObject(rule.duration); return <div key={state} className="grid gap-3 rounded border border-line p-3 sm:grid-cols-3">
        <Checkbox label={t('alpha9.deadline.stateDuration', { state })} checked={state in durations} onCheckedChange={on => duration(state, on ? { duration: 60 } : null)} />
        {state in durations && <>
          <Field label={t('alpha8.parameters.deadlineName')}>{props => <Select {...props} value={str(parameter.name)} onChange={event => duration(state, { duration: event.target.value ? { op: 'parameter', name: event.target.value } : 60 })}><option value="">{t('alpha9.deadline.fixed')}</option>{parameters.map(name => <option key={name}>{name}</option>)}</Select>}</Field>
          {!parameter.name && <SecondsField label={t('alpha9.deadline.duration')} value={Number(rule.duration)} min={1} max={31536000} onChange={duration_s => duration(state, { duration: duration_s })} />}
        </>}
      </div>; })}
      <Field label={t('alpha9.deadline.absolute')}>{props => <Select {...props} value={isObject(absolute.at_rule) ? '$rule' : str(absolute.at_input)} onChange={event => { const copy = { ...value }; if (event.target.value) copy.absolute_transition = { states: states.filter(state => state !== states.at(-1)), target: states.at(-1) ?? '', ...(event.target.value === '$rule' ? { at_rule: { op: 'ref', name: Object.keys(asObject(definition.rules))[0] ?? '' } } : { at_input: event.target.value }), priority: 'absolute' }; else delete copy.absolute_transition; onChange(copy); }}><option value="">{t('editors.RegistryObjects.keine_db028e')}</option><option value="$rule">{t('sleep.machine.timeRule')}</option>{stamps.map(name => <option key={name}>{name}</option>)}</Select>}</Field>
      {isObject(absolute.at_rule) && <RuleEditor value={absolute.at_rule as Rule} pointer={`${pointer}/absolute_transition/at_rule`} label={t('sleep.machine.timeRule')} onChange={at_rule => change({ absolute_transition: { ...absolute, at_rule } })} />}
      {(absolute.at_input || isObject(absolute.at_rule)) && <div className="space-y-3">
        <Field label={t('alpha9.deadline.target')}>{props => <Select {...props} value={str(absolute.target)} onChange={event => change({ absolute_transition: { ...absolute, target: event.target.value, states: asArray(absolute.states).filter(state => state !== event.target.value) } })}>{states.map(state => <option key={state}>{state}</option>)}</Select>}</Field>
        {states.filter(state => state !== absolute.target).map(state => <Checkbox key={state} label={t('alpha9.deadline.from', { state })} checked={asArray(absolute.states).includes(state)} onCheckedChange={on => change({ absolute_transition: { ...absolute, states: on ? [...asArray(absolute.states), state] : asArray(absolute.states).filter(item => item !== state) } })} />)}
        <Field label={t('alpha9.deadline.priority')}>{props => <Select {...props} value={str(absolute.priority)} onChange={event => change({ absolute_transition: { ...absolute, priority: event.target.value } })}><option value="absolute">{t('alpha9.deadline.absolute')}</option><option value="duration">{t('alpha9.deadline.duration')}</option></Select>}</Field>
      </div>}
    </>}
  </section>;
}
