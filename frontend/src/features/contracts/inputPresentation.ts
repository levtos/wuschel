import { t } from '@/i18n';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import type { Envelope, FieldValue, Registry } from '@/lib/types';
import { contractFieldValue, contractSources } from './presentation';
import { resolveRef } from '@/features/authoring/usage';

export function contractDefinition(envelope: Envelope, config: Registry | null): JsonObject {
  const type = envelope.type.id ?? envelope.type.type_id;
  const version = envelope.type.version ?? envelope.type.type_version;
  return asArray(config?.definitions).filter(isObject).find(definition => definition.type_id === type && definition.type_version === version) ?? {};
}
export function inputSchema(name: string, envelope: Envelope, config: Registry | null): FieldSchema | undefined {
  const declared = asArray(contractDefinition(envelope, config).inputs).filter(isObject).find(input => input.name === name)?.schema;
  if (isObject(declared)) return declared as FieldSchema;
  const contract = asArray(config?.contracts).filter(isObject).find(item => item.contract_id === envelope.contract_id);
  const ref = str(asArray(contract?.inputs).filter(isObject).find(input => input.name === name)?.ref);
  const target = resolveRef(ref, config);
  if (target?.kind === 'source') return asArray(config?.sources).filter(isObject).find(item => item.source_id === target.id)?.value_schema as FieldSchema | undefined;
  return undefined;
}
const known = (field?: FieldValue) => field && ['valid', 'held'].includes(field.status);
function guard(rule: JsonObject, inputs: Record<string, FieldValue>): { matches: boolean; name: string } | null {
  if (rule.op === 'input' && known(inputs[str(rule.name)]) && typeof inputs[str(rule.name)]?.value === 'boolean') return { matches: inputs[str(rule.name)]!.value === true, name: str(rule.name) };
  if (rule.op === 'not') { const nested = guard(asObject(asArray(rule.args)[0]), inputs); return nested ? { ...nested, matches: !nested.matches } : null; }
  return null;
}
function isNotApplicable(rule: JsonObject): boolean {
  if (rule.op === 'not_applicable') return asObject(rule.arg).op === 'literal' && asObject(rule.arg).value === true;
  return ['map', 'unit_scale'].includes(str(rule.op)) && isNotApplicable(asObject(rule.arg));
}
/** Explains an already server-confirmed status using only an explicit, proven
 * input guard. Unsupported or ambiguous rules never create a guessed cause. */
export function notApplicableValue(envelope: Envelope, name: string, config: Registry | null, inputs: Record<string, FieldValue>, schema?: FieldSchema, names: Record<string, string> = {}): string {
  if (typeof schema?.not_applicable_text === 'string' && schema.not_applicable_text.trim()) return schema.not_applicable_text;
  let rule = asObject(asObject(contractDefinition(envelope, config).outputs)[name]);
  for (let depth = 0; depth < 16 && rule.op === 'map'; depth++) rule = asObject(rule.arg);
  let trigger = '';
  if (rule.op === 'not_applicable' && asObject(rule.arg).op === 'input') {
    const candidate = str(asObject(rule.arg).name);
    if (known(inputs[candidate]) && inputs[candidate]?.value === true) trigger = candidate;
  } else if (rule.op === 'first_match') {
    for (const branch of asArray(rule.cases).filter(isObject)) {
      const condition = guard(asObject(branch.when), inputs);
      if (!condition) break;
      if (!condition.matches) continue;
      if (isNotApplicable(asObject(branch.then))) trigger = condition.name;
      break;
    }
  }
  if (!trigger) {
    const references = envelope.fields[name]?.reasons?.map(reason => reason.input) ?? [];
    const candidates = Object.keys(inputs).filter(input => references.includes(input));
    if (candidates.length === 1) trigger = candidates[0]!;
  }
  if (!trigger || !inputs[trigger]) return t('b22.na.noProof');
  const input = contractSources(envelope.contract_id, config, []).find(item => item.inputName === trigger);
  return t('b22.na.input', { name: names[trigger] || input?.name || trigger, value: contractFieldValue(undefined, trigger, inputs[trigger]!, inputSchema(trigger, envelope, config)) });
}
