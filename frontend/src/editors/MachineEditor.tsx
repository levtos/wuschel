import { t as uiText } from '@/i18n';
import { Trash2 } from 'lucide-react';
import { Checkbox, Collapsible } from '@/components/ui/controls';
import { Input, Select } from '@/components/ui/form';
import { Button } from '@/components/ui/button';
import { Notice } from '@/components/ui/layout';
import {
  asArray,
  asObject,
  newFieldSchema,
  newRule,
  renameKey,
  ruleContext,
  str,
  uniqueName,
  type FieldSchema,
  type Rule,
} from '@/features/authoring/model';
import type { Capabilities } from '@/lib/capabilities';
import { isObject, join, type Json, type JsonObject } from '@/lib/json';
import { AddButton, Fld, IconButton, IssueCount, IssuesAt, NumberField, RowActions, SectionTitle, TypedValueEditor } from './common';
import { FieldSchemaEditor } from './FieldSchemaEditor';
import { operatorDefaults, RuleEditor, RuleEnvProvider } from './RuleEditor';
import { Extras } from './Extras';
import { MachineDeadlinesEditor } from './MachineDeadlinesEditor';
import { minuteHint } from '@/components/SecondsField';

const RESERVED_EVENTS = ['input', 'deadline'];

/**
 * State machine of a definition: states, guarded transitions, typed commands,
 * episodes/sessions and the single deadline. Guards are stateless expressions.
 * Commands only request named transitions — there is no "set state".
 */
export function MachineEditor({
  value,
  onChange,
  pointer,
  definition,
  caps,
  locked,
}: {
  value: JsonObject;
  onChange: (next: JsonObject) => void;
  pointer: string;
  definition: JsonObject;
  caps: Capabilities | undefined;
  locked: boolean;
}) {
  const states = asArray(value.states).map(String);
  const transitions = asArray(value.transitions).map(asObject);
  const commands = asObject(value.commands);
  const extended = value.unknown_deadline === 'keep_and_block_transition';
  const hasDeadline = typeof value.deadline_s === 'number' || isObject(value.deadline_s) || Object.keys(asObject(value.deadline_by_state)).length > 0;
  const events = ['input', ...(hasDeadline ? ['deadline'] : []), ...Object.keys(commands)];
  const env = { ctx: ruleContext(definition, true), caps };
  const defaults = operatorDefaults(caps);
  const set = (patch: Record<string, Json | undefined>) => {
    const next: JsonObject = { ...value };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete next[k];
      else next[k] = v;
    }
    onChange(next);
  };
  const setTransitions = (next: JsonObject[]) => set({ transitions: next });
  const guardRule = (allow = true): Rule => ({ ...newRule('literal', env.ctx, defaults), value: allow });

  const renameState = (from: string, to: string) => {
    if (!to || states.includes(to)) return;
    onChange({
      ...value,
      states: states.map((s) => (s === from ? to : s)),
      initial: value.initial === from ? to : (value.initial ?? ''),
      ...(value.deadline_by_state ? { deadline_by_state: renameKey(asObject(value.deadline_by_state), from, to) } : {}),
      ...(value.absolute_transition ? { absolute_transition: { ...asObject(value.absolute_transition), states: asArray(asObject(value.absolute_transition).states).map(state => state === from ? to : state), target: asObject(value.absolute_transition).target === from ? to : asObject(value.absolute_transition).target ?? '' } } : {}),
      transitions: transitions.map((t) => ({ ...t, source: t.source === from ? to : (t.source ?? ''), target: t.target === from ? to : (t.target ?? '') })),
    });
  };
  const stateUsed = (name: string) => value.initial === name || name in asObject(value.deadline_by_state) || asArray(asObject(value.absolute_transition).states).includes(name) || asObject(value.absolute_transition).target === name || transitions.some((t) => t.source === name || t.target === name);

  const renameCommand = (from: string, to: string) => {
    if (!to || to in commands || RESERVED_EVENTS.includes(to)) return;
    onChange({
      ...value,
      commands: renameKey(commands as Record<string, Json>, from, to),
      transitions: transitions.map((t) => (t.event === from ? { ...t, event: to } : t)),
    });
  };

  return (
    <RuleEnvProvider value={{ ...env, locked }}>
      <fieldset disabled={locked} className="flex min-w-0 flex-col gap-6">
        <IssuesAt pointer={pointer} />
        <Extras def="MachineDefinition" value={value} pointer={pointer} onChange={onChange} />
        <section className="grid gap-3 sm:grid-cols-4">
          <Fld label={uiText("editors.MachineEditor.maschinen_id_bdfa88")} pointer={join(pointer, 'id')}>
            {(p) => <Input {...p} value={str(value.id)} onChange={(e) => set({ id: e.target.value })} />}
          </Fld>
          <Fld label={uiText("editors.MachineEditor.anfangszustand_da00a2")} pointer={join(pointer, 'initial')}>
            {(p) => (
              <Select {...p} value={str(value.initial)} onChange={(e) => set({ initial: e.target.value })}>
                {!states.includes(str(value.initial)) && <option value={str(value.initial)}>{str(value.initial) || '—'}</option>}
                {states.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </Select>
            )}
          </Fld>
          <div className="flex flex-col justify-end gap-2 pb-1.5">
            <Checkbox checked={value.sessions === true} onCheckedChange={(on) => set({ sessions: on })} label={uiText("editors.MachineEditor.sessions_fuhren_a20d63")} />
            {!extended && <Checkbox checked={hasDeadline} onCheckedChange={(on) => set({ deadline_s: on ? 60 : undefined })} label={uiText("editors.MachineEditor.deadline_verwenden_f1deab")} />}
          </div>
          {hasDeadline && !extended && (isObject(value.deadline_s) ? <Fld label={uiText('alpha8.parameters.deadlineName')} pointer={join(pointer, 'deadline_s', 'name')}>{props => <Input {...props} value={str(asObject(value.deadline_s).name)} onChange={event => set({ deadline_s: { op: 'parameter', name: event.target.value } })} />}</Fld> : <NumberField label={uiText("editors.MachineEditor.deadline_s_5ebf14")} pointer={join(pointer, 'deadline_s')} value={Number(value.deadline_s)} min={0} hint={minuteHint(Number(value.deadline_s))} onChange={(n) => set({ deadline_s: n })} />)}
        </section>

        <MachineDeadlinesEditor value={value} definition={definition} onChange={onChange} pointer={pointer} />

        <section className="space-y-2">
          <Checkbox checked={value.allow_manual_restart === true} onCheckedChange={on => set({ allow_manual_restart: on ? true : undefined })} label={uiText('alpha7.restart.allow')} />
          <p className="text-xs text-muted">{uiText('alpha7.restart.allowHelp')}</p>
        </section>

        <section>
          <SectionTitle aside={<AddButton onClick={() => set({ states: [...states, uniqueName('zustand', states)] })}>{uiText("editors.MachineEditor.zustand_630d1a")}</AddButton>}>{uiText("editors.MachineEditor.zustande_f5ef55")}</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {states.map((state) => (
              <div key={state} className="flex items-center gap-1 rounded-[var(--radius-sm)] border border-line-strong bg-sunken pl-1">
                <Input
                  aria-label={uiText("editors.MachineEditor.zustand_v0_871457", { v0: (state) })}
                  defaultValue={state}
                  key={state}
                  className="h-7 w-32 border-0 bg-transparent px-1.5 text-xs"
                  onBlur={(e) => (e.target.value.trim() && e.target.value !== state ? renameState(state, e.target.value.trim()) : (e.target.value = state))}
                />
                <IconButton label={stateUsed(state) ? uiText("editors.MachineEditor.v0_wird_verwendet_e5d823", { v0: (state) }) : uiText("editors.MachineEditor.v0_entfernen_92fea7", { v0: (state) })} tone="danger" disabled={stateUsed(state) || states.length <= 1} onClick={() => set({ states: states.filter((s) => s !== state) })}>
                  <Trash2 size={13} />
                </IconButton>
              </div>
            ))}
          </div>
          <IssuesAt pointer={join(pointer, 'states')} />
        </section>

        <section className="flex flex-col gap-2">
          <SectionTitle aside={<AddButton onClick={() => set({ commands: { ...commands, [uniqueName('befehl', [...Object.keys(commands), ...RESERVED_EVENTS])]: { arguments: {} } } })}>{uiText("editors.MachineEditor.befehl_c8505b")}</AddButton>}>{uiText("editors.MachineEditor.befehle_23a3b7")}</SectionTitle>
          <p className="text-xs text-muted">{uiText("editors.MachineEditor.ein_befehl_fordert_eine_benannte_transition_an_d_878ba3")}</p>
          {Object.entries(commands).map(([name, raw]) => {
            const args = asObject(asObject(raw).arguments) as Record<string, FieldSchema>;
            return (
              <Collapsible
                key={name}
                defaultOpen
                title={<code>{name}</code>}
                aside={
                  <>
                    <IssueCount pointer={join(pointer, 'commands', name)} />
                    <IconButton label={uiText("editors.MachineEditor.befehl_v0_entfernen_54de54", { v0: (name) })} tone="danger" onClick={() => onChange({ ...value, commands: Object.fromEntries(Object.entries(commands).filter(([k]) => k !== name)), transitions: transitions.filter((t) => t.event !== name) })}>
                      <Trash2 size={14} />
                    </IconButton>
                  </>
                }
              >
                <div className="flex flex-col gap-3">
                  <Extras def="CommandDefinition" value={asObject(raw)} pointer={join(pointer, 'commands', name)} onChange={(next) => set({ commands: { ...commands, [name]: next } })} />
                  <Fld label={uiText("editors.MachineEditor.befehlsname_439d2d")} pointer={join(pointer, 'commands', name)}>
                    {(p) => <Input {...p} defaultValue={name} key={name} onBlur={(e) => (e.target.value.trim() !== name ? renameCommand(name, e.target.value.trim()) : undefined)} />}
                  </Fld>
                  <SectionTitle aside={<AddButton onClick={() => set({ commands: { ...commands, [name]: { arguments: { ...args, [uniqueName('arg', Object.keys(args))]: newFieldSchema('bool') } } } })}>{uiText("editors.MachineEditor.argument_481da3")}</AddButton>}>{uiText("editors.MachineEditor.typisierte_argumente_a38ea4")}</SectionTitle>
                  {Object.entries(args).length === 0 && <p className="text-xs text-faint">{uiText("editors.MachineEditor.keine_argumente_98a370")}</p>}
                  {Object.entries(args).map(([argName, schema]) => (
                    <div key={argName} className="flex items-start gap-2 rounded-[var(--radius-md)] border border-line p-2">
                      <div className="flex min-w-0 flex-1 flex-col gap-2">
                        <Fld label={uiText("editors.MachineEditor.argument_481da3")} pointer={join(pointer, 'commands', name, 'arguments', argName)}>
                          {(p) => (
                            <Input
                              {...p}
                              defaultValue={argName}
                              key={argName}
                              onBlur={(e) => {
                                const to = e.target.value.trim();
                                if (to && to !== argName && !(to in args)) {
                                  onChange({
                                    ...value,
                                    commands: { ...commands, [name]: { arguments: renameKey(args as Record<string, Json>, argName, to) } },
                                    transitions: transitions.map((t) => (t.event === name && isKey(t.argument_equals, argName) ? { ...t, argument_equals: renameKey(asObject(t.argument_equals), argName, to) } : t)),
                                  });
                                } else e.target.value = argName;
                              }}
                            />
                          )}
                        </Fld>
                        <FieldSchemaEditor compact pointer={join(pointer, 'commands', name, 'arguments', argName)} value={schema} onChange={(next) => set({ commands: { ...commands, [name]: { arguments: { ...args, [argName]: next } } } })} />
                      </div>
                      <IconButton label={uiText("editors.MachineEditor.argument_entfernen_c60b1a")} tone="danger" onClick={() => set({ commands: { ...commands, [name]: { arguments: Object.fromEntries(Object.entries(args).filter(([k]) => k !== argName)) } } })}>
                        <Trash2 size={14} />
                      </IconButton>
                    </div>
                  ))}
                </div>
              </Collapsible>
            );
          })}
        </section>

        <section className="flex flex-col gap-2">
          <SectionTitle>{uiText("editors.MachineEditor.neu_initialisieren_wenn_dd14f7")}</SectionTitle>
          <p className="text-xs text-muted">{uiText("editors.MachineEditor.zustandsloser_ausdruck_zeitliche_wahrheiten_als__a67920")}</p>
          {value.reinitialize_when ? <RuleEditor value={value.reinitialize_when as Rule} pointer={join(pointer, 'reinitialize_when')} onChange={(next) => set({ reinitialize_when: next })} /> : <Notice tone="bad" title={uiText("editors.MachineEditor.neustart_bedingung_fehlt_f0544a")}><Button size="sm" onClick={() => set({ reinitialize_when: guardRule(false) })}>{uiText("editors.MachineEditor.bedingung_festlegen_0513ca")}</Button></Notice>}
          {asObject(value.reinitialize_when).op === 'literal' && asObject(value.reinitialize_when).value === false && <Notice tone="warn" title={uiText("editors.MachineEditor.dieser_ablauf_kann_sich_nach_einer_anderung_nie__670fc4")}>{uiText("editors.MachineEditor.legen_sie_eine_bedingung_aus_gultigen_eingangen__95fb96")}</Notice>}
          {asArray(definition.inputs).map(asObject).filter((input) => asObject(input.schema).type === 'bool').map((input) => <Button key={str(input.name)} size="sm" onClick={() => set({ reinitialize_when: { op: 'not', args: [{ op: 'input', name: str(input.name) }] } })}>{uiText("editors.MachineEditor.neustart_wenn_ef87e7")}{str(input.name)} {uiText("editors.MachineEditor.nein_ist_79608c")}</Button>)}
        </section>

        <section className="flex flex-col gap-2">
          <SectionTitle
            aside={
              <AddButton
                onClick={() =>
                  setTransitions([
                    ...transitions,
                    { source: states[0] ?? '', target: states[0] ?? '', event: 'input', guard: guardRule(), reason: 'transition' },
                  ])
                }
              >
                {uiText("editors.MachineEditor.transition_2fe704")}</AddButton>
            }
          >
            {uiText("editors.MachineEditor.transitionen_41ef48")}</SectionTitle>
          <p className="text-xs text-muted">{uiText("editors.MachineEditor.pro_quellzustand_und_ereignis_ist_genau_eine_tra_5c73e8")}</p>
          {transitions.map((transition, i) => {
            const tp = join(pointer, 'transitions', i);
            const command = asObject(asObject(commands[str(transition.event)]).arguments) as Record<string, FieldSchema>;
            const isCommand = str(transition.event) in commands;
            const equals = asObject(transition.argument_equals);
            const patch = (change: Record<string, Json | undefined>) => {
              const next: JsonObject = { ...transition };
              for (const [k, v] of Object.entries(change)) {
                if (v === undefined) delete next[k];
                else next[k] = v;
              }
              setTransitions(transitions.map((t, j) => (j === i ? next : t)));
            };
            return (
              <Collapsible
                key={i}
                defaultOpen
                title={
                  <span className="flex items-center gap-2">
                    <code>{str(transition.source)}</code>
                    <span className="text-muted">—{str(transition.event)}→</span>
                    <code>{str(transition.target)}</code>
                  </span>
                }
                aside={
                  <>
                    <IssueCount pointer={tp} />
                    <RowActions
                      index={i}
                      count={transitions.length}
                      onMove={(from, to) => {
                        const copy = transitions.slice();
                        const [moved] = copy.splice(from, 1);
                        copy.splice(to, 0, moved as JsonObject);
                        setTransitions(copy);
                      }}
                      onRemove={() => setTransitions(transitions.filter((_, j) => j !== i))}
                    />
                  </>
                }
              >
                <div className="flex flex-col gap-4">
                  <div className="grid gap-3 sm:grid-cols-4">
                    <Fld label={uiText("editors.MachineEditor.von_640e86")} pointer={join(tp, 'source')}>
                      {(p) => (
                        <Select {...p} value={str(transition.source)} onChange={(e) => patch({ source: e.target.value })}>
                          {!states.includes(str(transition.source)) && <option value={str(transition.source)}>{str(transition.source) || '—'}</option>}
                          {states.map((s) => (
                            <option key={s} value={s}>{s}</option>
                          ))}
                        </Select>
                      )}
                    </Fld>
                    <Fld label={uiText("editors.MachineEditor.nach_0ea777")} pointer={join(tp, 'target')}>
                      {(p) => (
                        <Select {...p} value={str(transition.target)} onChange={(e) => patch({ target: e.target.value })}>
                          {!states.includes(str(transition.target)) && <option value={str(transition.target)}>{str(transition.target) || '—'}</option>}
                          {states.map((s) => (
                            <option key={s} value={s}>{s}</option>
                          ))}
                        </Select>
                      )}
                    </Fld>
                    <Fld label={uiText("editors.MachineEditor.ereignis_af9e77")} pointer={join(tp, 'event')} hint={str(transition.event) === uiText("editors.MachineEditor.deadline_dfc8ae") && !hasDeadline ? uiText("editors.MachineEditor.deadline_transitionen_benotigen_eine_deadline_d8a6d2") : undefined}>
                      {(p) => (
                        <Select {...p} value={str(transition.event)} onChange={(e) => patch({ event: e.target.value, argument_equals: undefined })}>
                          {!events.includes(str(transition.event)) && <option value={str(transition.event)}>{str(transition.event) || '—'}</option>}
                          {events.map((ev) => (
                            <option key={ev} value={ev}>{ev === 'input' ? uiText("editors.MachineEditor.input_eingangsanderung_eec32a") : ev === 'deadline' ? uiText("editors.MachineEditor.deadline_fristablauf_ae4d9c") : uiText("editors.MachineEditor.befehl_v0_fc4317", { v0: (ev) })}</option>
                          ))}
                        </Select>
                      )}
                    </Fld>
                    <Fld label={uiText("editors.MachineEditor.grund_b45f40")} pointer={join(tp, 'reason')}>
                      {(p) => <Input {...p} value={str(transition.reason)} onChange={(e) => patch({ reason: e.target.value })} />}
                    </Fld>
                  </div>
                  <Extras def="TransitionDefinition" value={transition} pointer={tp} onChange={(next) => setTransitions(transitions.map((t, j) => (j === i ? next : t)))} />
                  <div className="flex flex-wrap gap-5">
                    <Checkbox checked={transition.new_episode === true} onCheckedChange={(on) => patch({ new_episode: on || undefined })} label={uiText("editors.MachineEditor.beginnt_neue_episode_aa43cc")} />
                    <Checkbox checked={transition.ends_episode === true} onCheckedChange={(on) => patch({ ends_episode: on || undefined })} label={uiText("editors.MachineEditor.beendet_episode_92986e")} />
                  </div>
                  {extended && <Fld label={uiText('alpha9.deadline.action')} pointer={join(tp, 'deadline_action')}>{props => <Select {...props} value={str(transition.deadline_action) || 'keep'} onChange={event => patch({ deadline_action: event.target.value === 'keep' ? undefined : event.target.value })}><option value="keep">{uiText('alpha9.deadline.keep')}</option><option value="reset">{uiText('alpha9.deadline.reset')}</option><option value="clear">{uiText('alpha9.deadline.clear')}</option></Select>}</Fld>}
                  {isCommand && Object.keys(command).length > 0 && (
                    <div className="flex flex-col gap-2">
                      <SectionTitle>{uiText("editors.MachineEditor.bedingungen_an_die_befehlsargumente_08ced2")}</SectionTitle>
                      {Object.entries(command).map(([argName, schema]) => {
                        const active = argName in equals;
                        return (
                          <div key={argName} className="grid items-start gap-2 sm:grid-cols-[14rem_1fr]">
                            <div className="pt-6">
                              <Checkbox
                                checked={active}
                                onCheckedChange={(on) => {
                                  const next = { ...equals };
                                  if (on) next[argName] = defaultOf(schema);
                                  else delete next[argName];
                                  patch({ argument_equals: Object.keys(next).length ? next : undefined });
                                }}
                                label={<code>{argName} =</code>}
                              />
                            </div>
                            {active && <TypedValueEditor label={uiText("editors.MachineEditor.muss_gleich_sein_4c42df")} schema={schema} value={equals[argName] as Json} pointer={join(tp, 'argument_equals', argName)} onChange={(v) => patch({ argument_equals: { ...equals, [argName]: v } })} />}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <div className="flex flex-col gap-2">
                    <SectionTitle>{uiText("editors.MachineEditor.guard_zustandslos_96238a")}</SectionTitle>
                    <RuleEditor value={(transition.guard ?? guardRule()) as Rule} pointer={join(tp, 'guard')} onChange={(next) => patch({ guard: next })} />
                  </div>
                </div>
              </Collapsible>
            );
          })}
        </section>
      </fieldset>
    </RuleEnvProvider>
  );
}

function isKey(value: Json | undefined, key: string): boolean {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && key in value;
}

function defaultOf(schema: FieldSchema): Json {
  switch (schema.type) {
    case 'bool':
      return true;
    case 'number':
      return 0;
    case 'enum':
      return Array.isArray(schema.value_catalog) ? (schema.value_catalog[0] as Json) : '';
    case 'timestamp':
      return '2026-01-01T00:00:00+00:00';
    case 'object':
      return {};
    default:
      return '';
  }
}
