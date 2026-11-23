import { asArray, asObject, str } from '@/features/authoring/model';
import { hasTranslation, t } from '@/i18n';
import { fieldLabel } from '@/i18n/codes';
import type { JsonObject } from '@/lib/json';
import { ruleDuration, ruleSentence } from './RuleReading';

const stateLabel = (state: string) => hasTranslation('codes.sleep.' + state) ? t('codes.sleep.' + state) : state;
const eventLabel = (event: string) => hasTranslation('a2.machine.event.' + event) ? t('a2.machine.event.' + event) : fieldLabel(event);

export function MachineReading({ definition }: { definition: JsonObject }) {
  const machine = asObject(definition.machine);
  const states = asArray(machine.states).map(String);
  const absolute = asObject(machine.absolute_transition);
  const durations = asObject(machine.deadline_by_state);
  const required = asObject(machine.required_inputs_by_state);
  const transitions = asArray(machine.transitions).map(asObject);
  const duration = (state: string) => ruleDuration(asObject(durations[state]).duration ?? machine.deadline_s, definition);
  const seen = new Map<string, number>();
  const rows = transitions.map(transition => {
    const source = str(transition.source);
    const event = str(transition.event);
    const key = source + '/' + event;
    const priority = (seen.get(key) ?? 0) + 1;
    seen.set(key, priority);
    const condition = ruleSentence(transition.guard, definition);
    const guard = asObject(transition.guard);
    return {
      source, target: str(transition.target),
      condition: t(guard.op === 'literal' && guard.value === true ? 'a2.machine.unconditional' : 'a2.machine.condition', { event: eventLabel(event), condition }),
      priority: String(priority),
      deadline: event === 'deadline' ? duration(source) : '–',
      effect: [
        transition.deadline_action === 'reset' ? t(transition.reset_trigger ? 'a2.machine.resetEvent' : 'a2.machine.reset') : '',
        transition.deadline_action === 'clear' ? t('a2.machine.clear') : '',
        transition.new_episode ? t('a2.machine.starts') : '',
        transition.ends_episode ? t('a2.machine.ends') : '',
      ].filter(Boolean).join(' '),
    };
  });
  for (const source of asArray(absolute.states).map(String)) rows.push({
    source, target: str(absolute.target), condition: t('a2.machine.atPlannedTime'),
    priority: t(absolute.priority === 'absolute' ? 'a2.machine.absoluteFirst' : 'a2.machine.durationFirst'),
    deadline: absolute.at_rule ? ruleSentence(absolute.at_rule, definition) : fieldLabel(str(absolute.at_input)),
    effect: '',
  });

  return <section className="space-y-4 text-sm">
    <h3 className="font-semibold">{t('a2.machine.overview')}</h3>
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <caption className="sr-only">{t('a2.machine.states')}</caption>
        <thead><tr>{['state', 'required', 'deadline'].map(column => <th key={column} className="border-b border-line p-2" scope="col">{t('a2.machine.' + column)}</th>)}</tr></thead>
        <tbody>{states.map(state => <tr key={state} className="border-b border-line">
          <th className="p-2 font-normal" scope="row">{stateLabel(state)}{state === machine.initial && ' · ' + t('a2.machine.initial')}</th>
          <td className="p-2">{asArray(required[state]).map(name => fieldLabel(String(name))).join(', ') || '–'}</td>
          <td className="p-2">{duration(state)}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <caption className="sr-only">{t('a2.machine.transitions')}</caption>
        <thead><tr>{['from', 'to', 'conditionTitle', 'priority', 'deadline', 'effect'].map(column => <th key={column} className="border-b border-line p-2" scope="col">{t('a2.machine.' + column)}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index} className="border-b border-line align-top">
          <td className="p-2">{stateLabel(row.source)}</td><td className="p-2">{stateLabel(row.target)}</td>
          <td className="p-2">{row.condition}</td><td className="p-2">{row.priority}</td>
          <td className="p-2">{row.deadline}</td><td className="p-2">{row.effect || '–'}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p className="text-muted">{t('a2.machine.priorityHelp')}</p>
    <section><h4 className="font-medium">{t('a2.machine.unknown')}</h4>
      <p>{t('a2.machine.unknownHelp')}</p>
      {machine.unknown_deadline && <p>{t('a2.machine.unknownDeadline')}</p>}
    </section>
    <section><h4 className="font-medium">{t('a2.machine.restore')}</h4>
      <p>{t('a2.machine.restoreHelp')}</p>
      <p>{t('a2.machine.reinitialize', { condition: ruleSentence(machine.reinitialize_when, definition) })}</p>
    </section>
    <section><h4 className="font-medium">{t('a2.machine.episode')}</h4>
      <p>{t('a2.machine.episodeHelp')}</p>
      {Object.entries(asObject(machine.captures)).map(([slot, raw]) => <p key={slot}>{t('a2.machine.capture', {
        value: fieldLabel(slot), state: stateLabel(str(asObject(raw).on_enter)),
      })}</p>)}
    </section>
  </section>;
}
