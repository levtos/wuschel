import { t as uiText } from '@/i18n';
import { clone, isObject, type Json, type JsonObject } from '@/lib/json';

/**
 * Light typed views and pure builders for the registry document. The document
 * itself stays plain JSON so that unknown (but schema-valid) members survive
 * every edit untouched; nothing here validates semantics — the server does.
 */

export type FieldType = 'bool' | 'number' | 'enum' | 'text' | 'timestamp' | 'object';
export const FIELD_TYPES: FieldType[] = ['bool', 'number', 'enum', 'text', 'timestamp', 'object'];

export interface FieldSchema extends JsonObject {
  type: FieldType;
  required: boolean;
  physical_state: boolean;
}

export type Rule = JsonObject & { op: string };

export const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  bool: uiText("features.authoring.model.wahrheitswert_7dc5d8"),
  number: uiText("features.authoring.model.zahl_654eb4"),
  enum: uiText("features.authoring.model.auswahl_enum_ae155a"),
  text: uiText("features.authoring.model.text_71988c"),
  timestamp: uiText("features.authoring.model.zeitpunkt_9d91e4"),
  object: uiText("features.authoring.model.objekt_fe41f9"),
};

export const emptyRegistry = (): JsonObject => ({
  config_schema_version: 1,
  fingerprint_version: 2,
  dependencies: [],
  sources: [],
  bindings: [],
  contracts: [],
  catalogs: [],
  definitions: [],
});

export const newFieldSchema = (type: FieldType = 'bool'): FieldSchema => ({
  type,
  required: true,
  physical_state: false,
  ...(type === 'enum' ? { value_catalog: ['a', 'b'] } : {}),
});

export const asArray = (value: Json | undefined): Json[] => (Array.isArray(value) ? value : []);
export const asObject = (value: Json | undefined): JsonObject => (isObject(value) ? value : {});
export const str = (value: Json | undefined): string => (typeof value === 'string' ? value : '');

export function defaultLiteralValue(schema: FieldSchema): Json {
  switch (schema.type) {
    case 'bool':
      return false;
    case 'number':
      return 0;
    case 'enum':
      return (Array.isArray(schema.value_catalog) ? (schema.value_catalog[0] as string | undefined) : undefined) ?? '';
    case 'text':
      return '';
    case 'timestamp':
      return '2026-01-01T00:00:00+00:00';
    case 'object':
      return {};
  }
}

export interface RuleContext {
  inputs: { name: string; schema: FieldSchema }[];
  ruleNames: string[];
  /** Stateless mode (guards, reinitialization): no bucket/temporal/machine. */
  stateless: boolean;
  hasMachine: boolean;
  usedIds: Set<string>;
}

export function uniqueName(base: string, used: Iterable<string>): string {
  const taken = new Set(used);
  if (!taken.has(base)) return base;
  for (let i = 2; ; i += 1) if (!taken.has(`${base}${i}`)) return `${base}${i}`;
}

export interface OperatorDefaults {
  formulas: { name: string; version: number }[];
  fusion: string[];
  temporal: { operation: string; arguments: number }[];
}

export const STATEFUL_OPS = new Set(['bucket', 'temporal', 'machine']);

export function newRule(op: string, ctx: RuleContext, defaults: OperatorDefaults): Rule {
  const firstInput = ctx.inputs[0]?.name ?? '';
  const inputRule = (): Rule => ({ op: 'input', name: firstInput });
  const literal = (type: FieldType, value?: Json): Rule => {
    const schema = newFieldSchema(type);
    return { op: 'literal', schema, value: value ?? defaultLiteralValue(schema) };
  };
  switch (op) {
    case 'input':
      return { op, name: firstInput };
    case 'ref':
      return { op, name: ctx.ruleNames[0] ?? '' };
    case 'literal':
      return literal('bool');
    case 'annual_curve':
      return { op, at: inputRule(), points: [{ day: '01-01', value: 0 }, { day: '07-01', value: 1 }] };
    case 'calendar_active':
      return { op, arg: inputRule(), pattern: literal('text', '*'), all_day_only: false };
    case 'and':
    case 'or':
      return { op, args: [inputRule(), inputRule()] };
    case 'not':
      return { op, args: [inputRule()] };
    case 'compare':
      return { op, comparison: 'eq', left: literal('number', 0), right: literal('number', 0) };
    case 'managed_catalog_lookup':
      return { op, catalog_id: '', definition_version: 1, scope: literal('text', 'default'), media_type: literal('text', ''), signal_type: literal('text', 'title'), key: inputRule(), schema: { ...newFieldSchema('enum'), value_catalog: ['other'] } };
    case 'map':
      return { op, arg: inputRule(), schema: newFieldSchema('text'), entries: { true: 'ja' } };
    case 'first_match':
      return { op, cases: [{ when: inputRule(), then: literal('bool', true) }] };
    case 'bucket':
      return { op, id: uniqueName('bucket', ctx.usedIds), arg: inputRule(), initial: false, lower: 0, upper: 1 };
    case 'formula': {
      const formula = defaults.formulas[0];
      return { op, name: formula?.name ?? '', version: formula?.version ?? 1, args: [literal('number', 0)] };
    }
    case 'fusion':
      return { op, strategy: defaults.fusion[0] ?? '', args: [inputRule(), inputRule()] };
    case 'temporal':
      return { op, id: uniqueName('temporal', ctx.usedIds), operation: 'stable_for', duration_s: 1, args: [inputRule()] };
    case 'machine':
      return { op };
    case 'not_applicable':
    case 'known_automatic':
      return { op, arg: inputRule() };
    default:
      return { op };
  }
}

/** Keeps child expressions where the shapes are compatible; otherwise builds a fresh node. */
export function convertRule(old: Rule, op: string, ctx: RuleContext, defaults: OperatorDefaults): Rule {
  const booleanFamily = ['and', 'or', 'not'];
  if (booleanFamily.includes(old.op) && booleanFamily.includes(op) && Array.isArray(old.args)) {
    const args = op === 'not' ? old.args.slice(0, 1) : old.args.length >= 1 ? old.args : newRule(op, ctx, defaults).args!;
    return { ...old, op, args };
  }
  const known = new Set(['op', 'name', 'schema', 'value', 'args', 'comparison', 'left', 'right', 'arg', 'entries', 'cases', 'id', 'initial', 'lower', 'upper', 'version', 'strategy', 'operation', 'duration_s', 'accepts_held', 'at', 'points', 'leap_day', 'pattern', 'all_day_only']);
  const extra = Object.fromEntries(Object.entries(old).filter(([key]) => !known.has(key)));
  const next = newRule(op, ctx, defaults);
  return { ...extra, ...next, ...(next.id !== undefined && old.id !== undefined ? { id: old.id } : {}) };
}

/** Direct sub-expressions with their pointer segments relative to the node. */
export function ruleChildren(rule: Rule): { key: (string | number)[]; rule: Rule }[] {
  const out: { key: (string | number)[]; rule: Rule }[] = [];
  const add = (key: (string | number)[], value: Json | undefined) => {
    if (isObject(value) && typeof value.op === 'string') out.push({ key, rule: value as Rule });
  };
  for (const name of ['arg', 'left', 'right', 'at', 'when', 'duration_s', 'pattern', 'on_date', 'sleep_since', 'before_workday', 'current_workday', 'start_kind', 'start_phase', 'start_time', 'otherwise_kind', 'otherwise_phase', 'otherwise_time']) add([name], rule[name]);
  if (Array.isArray(rule.points)) rule.points.forEach((point, i) => { if (isObject(point)) add(['points', i, 'value'], point.value); });
  if (Array.isArray(rule.args)) rule.args.forEach((arg, i) => add(['args', i], arg));
  else if (isObject(rule.args)) Object.entries(rule.args).forEach(([name, arg]) => add(['args', name], arg));
  if (Array.isArray(rule.boundaries)) rule.boundaries.forEach((boundary, i) => {
    if (isObject(boundary)) {
      add(['boundaries', i, 'at'], boundary.at);
      if (isObject(boundary.hysteresis)) for (const name of ['lower', 'upper']) add(['boundaries', i, 'hysteresis', name], boundary.hysteresis[name]);
    }
  });
  if (Array.isArray(rule.cases)) {
    rule.cases.forEach((item, i) => {
      if (isObject(item)) {
        add(['cases', i, 'when'], item.when);
        add(['cases', i, 'then'], item.then);
      }
    });
  }
  return out;
}

export function walkRule(rule: Rule, visit: (rule: Rule, path: (string | number)[]) => void, path: (string | number)[] = []): void {
  visit(rule, path);
  for (const child of ruleChildren(rule)) walkRule(child.rule, visit, [...path, ...child.key]);
}

/** All rule roots of a definition with their pointer relative to the definition. */
export function definitionRoots(definition: JsonObject): { path: (string | number)[]; rule: Rule }[] {
  const roots: { path: (string | number)[]; rule: Rule }[] = [];
  const add = (path: (string | number)[], value: Json | undefined) => {
    if (isObject(value) && typeof value.op === 'string') roots.push({ path, rule: value as Rule });
  };
  for (const [name, rule] of Object.entries(asObject(definition.rules))) add(['rules', name], rule);
  for (const [name, rule] of Object.entries(asObject(definition.outputs))) add(['outputs', name], rule);
  const machine = asObject(definition.machine);
  add(['machine', 'reinitialize_when'], machine.reinitialize_when);
  add(['machine', 'absolute_transition', 'at_rule'], asObject(machine.absolute_transition).at_rule);
  asArray(machine.transitions).forEach((t, i) => add(['machine', 'transitions', i, 'guard'], asObject(t).guard));
  return roots;
}

/** An explicit edit opts into new capabilities; simply loading an old definition changes nothing. */
export function withMinimumLanguage(definition: JsonObject): JsonObject {
  let version = Number(definition.language_version ?? 1);
  const machine = asObject(definition.machine);
  if (machine.transition_selection || Object.keys(asObject(machine.captures)).length || Object.keys(asObject(machine.required_inputs_by_state)).length || asObject(machine.absolute_transition).at_rule || asArray(machine.transitions).some(raw => asObject(raw).entry_label || asObject(raw).reset_trigger) || Object.values(asObject(definition.parameter_schema)).some(raw => asObject(raw).structured_schema)) version = Math.max(version, 6);
  if (Object.keys(asObject(definition.output_origins)).length || machine.unknown_deadline || machine.absolute_transition || Object.keys(asObject(machine.deadline_by_state)).length) version = Math.max(version, 3);
  for (const { rule } of definitionRoots(definition)) walkRule(rule, node => {
    if (node.op === 'known_automatic') version = Math.max(version, 3);
    if (node.op === 'annual_curve' || node.op === 'calendar_active') version = Math.max(version, 5);
    if (['calendar_at', 'daily_window', 'event_match', 'machine_memory', 'machine_field', 'wake_plan'].includes(node.op) || node.on_date) version = Math.max(version, 6);
  });
  return version > Number(definition.language_version ?? 1) ? { ...definition, language_version: version } : definition;
}

export function collectStatefulIds(definition: JsonObject): Set<string> {
  const ids = new Set<string>();
  for (const { rule } of definitionRoots(definition)) {
    walkRule(rule, (node) => {
      if ((node.op === 'bucket' || node.op === 'temporal') && typeof node.id === 'string') ids.add(node.id);
    });
  }
  return ids;
}

export function ruleContext(definition: JsonObject, stateless: boolean, defaults?: { usedIds?: Set<string> }): RuleContext {
  return {
    inputs: asArray(definition.inputs)
      .filter(isObject)
      .map((input) => ({ name: str(input.name), schema: asObject(input.schema) as FieldSchema })),
    ruleNames: Object.keys(asObject(definition.rules)),
    stateless,
    hasMachine: isObject(definition.machine),
    usedIds: defaults?.usedIds ?? collectStatefulIds(definition),
  };
}

export const definitionKey = (definition: JsonObject): string => `${str(definition.type_id)}@${String(definition.type_version ?? '')}`;

export function newDefinition(typeId: string, version: number): JsonObject {
  return {
    type_id: typeId,
    type_version: version,
    inputs: [{ name: 'a', schema: newFieldSchema('bool') }],
    schema: { fields: { value: newFieldSchema('bool') }, available_projection: ['value'], ...(typeId.startsWith('test.') ? {} : { fixture: false }) },
    outputs: { value: { op: 'input', name: 'a' } },
  };
}

/** New immutable version: same semantics as a starting point, version bumped. */
export function nextVersionOf(definition: JsonObject, all: JsonObject[]): JsonObject {
  const sameType = all.filter((d) => d.type_id === definition.type_id).map((d) => Number(d.type_version) || 0);
  const copy = clone(definition);
  copy.type_version = Math.max(0, ...sameType) + 1;
  return copy;
}

export function renameKey<T extends Json>(object: { [key: string]: T }, from: string, to: string): { [key: string]: T } {
  return Object.fromEntries(Object.entries(object).map(([key, value]) => [key === from ? to : key, value]));
}

/** Rewrite every reference to a renamed rule/input name within a definition. */
export function renameReferences(definition: JsonObject, kind: 'ref' | 'input' | 'parameter', from: string, to: string): JsonObject {
  const next = clone(definition);
  for (const { rule } of definitionRoots(next)) {
    walkRule(rule, (node) => {
      if (node.op === kind && node.name === from) node.name = to;
    });
  }
  return next;
}

export function referencedNames(definition: JsonObject, kind: 'ref' | 'input' | 'parameter'): Map<string, number> {
  const count = new Map<string, number>();
  for (const { rule } of definitionRoots(definition)) {
    walkRule(rule, (node) => {
      if (node.op === kind && typeof node.name === 'string') count.set(node.name, (count.get(node.name) ?? 0) + 1);
    });
  }
  return count;
}

export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list;
  const copy = list.slice();
  const [item] = copy.splice(from, 1);
  copy.splice(to, 0, item as T);
  return copy;
}
