import { hasTranslation, t } from '@/i18n';
import { fieldLabel } from '@/i18n/codes';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import type { TypeEntry } from '@/features/authoring/catalog';
import { resolveRef } from '@/features/authoring/usage';
import { isObject, type JsonObject } from '@/lib/json';
import { displayValue, FIELD_STATUS } from '@/lib/status';
import type { Envelope, FieldValue, HaEntity, Reason, Registry } from '@/lib/types';
import type { Tone } from '@/components/ui/badge';

export function fieldsForContract(contract: JsonObject, config: Registry | null, types: TypeEntry[]) {
  const type = types.find(item => item.type_id === contract.type_id && item.type_version === contract.type_version);
  const fields = { ...type?.fields };
  const definition = asArray(config?.definitions).filter(isObject).find(item => item.type_id === contract.type_id && item.type_version === contract.type_version);
  for (const [name, rule] of Object.entries(asObject(definition?.outputs))) if (isObject(rule) && rule.op === 'temporal' && rule.operation === 'age' && fields[name]) fields[name] = { ...fields[name], unit: 's' };
  if (contract.type_id === 'test.echo' && fields.value) {
    const input = asArray(contract.inputs).filter(isObject)[0];
    const source = asArray(config?.sources).filter(isObject).find(item => item.source_id === input?.ref);
    const unit = asObject(source?.value_schema).unit;
    if (typeof unit === 'string') fields.value = { ...fields.value, unit };
  }
  return fields;
}

const SLEEP_TYPES = new Set(['sleep.flow', 'sleep.state_view', 'wake.plan_view']);

export function contractTypeId(envelope: Envelope): string {
  return str(envelope.type?.id ?? envelope.type?.type_id);
}

export function contractTitle(contract: JsonObject | undefined, fallback = ''): string {
  return contract?.type_id === 'sleep.flow' ? t('b14.sleep.internal') : str(contract?.display_name) || fallback;
}

export function contractFieldLabel(typeId: string | undefined, name: string): string {
  if (typeId && SLEEP_TYPES.has(typeId) && name === 'value') return fieldLabel('sleep_state');
  if (typeId === 'wake.plan_view' && name === 'status') return fieldLabel('wake_status');
  return fieldLabel(name);
}

/** Translate only the known sleep/wake contract vocabulary, never arbitrary text. */
export function contractFieldValue(typeId: string | undefined, name: string, field: FieldValue, schema?: FieldSchema): string {
  if (typeId && SLEEP_TYPES.has(typeId) && ['valid', 'held'].includes(field.status) && typeof field.value === 'string') {
    const namespace = typeId === 'wake.plan_view' || name.startsWith('wake_') ? 'wake' : 'sleep';
    const codeField = ['value', 'entry_origin', 'last_action_origin', 'status', 'decision', 'wake_status', 'wake_decision'].includes(name);
    const key = `codes.${namespace}.${field.value}`;
    if (codeField && hasTranslation(key)) return t(key);
  }
  return displayValue(field, str(schema?.unit), schema);
}

export function contractValue(envelope: Envelope | null, fields: Record<string, FieldSchema>): string {
  if (!envelope) return t('alpha7.contract.noValue');
  const typeId = contractTypeId(envelope);
  const value = (name: string) => envelope.fields[name] ? contractFieldValue(typeId, name, envelope.fields[name]!, fields[name]) : t('alpha7.contract.noValue');
  if (typeId === 'sleep.flow') return t('b14.sleep.internalValue');
  if (typeId === 'sleep.state_view') {
    const deadline = envelope.fields.deadline_at;
    return t('b14.sleep.state', { value: value('value') }) + (deadline && deadline.value != null && ['valid', 'held'].includes(deadline.status) ? ` · ${t('b14.sleep.deadline', { value: value('deadline_at') })}` : '');
  }
  if (typeId === 'wake.plan_view') return `${t('b14.wake.next', { value: value('next_wake_start') })} · ${t('b14.wake.status', { value: value('status') })}`;
  return Object.entries(envelope.fields).map(([name, field]) => `${Object.keys(envelope.fields).length > 1 ? `${fieldLabel(name)}: ` : ''}${contractFieldValue(typeId, name, field, fields[name])}`).join(' · ');
}

export const CONTRACT_CATEGORIES = ['healthy', 'degraded', 'unknown', 'off', 'draft'] as const;
export type ContractCategory = typeof CONTRACT_CATEGORIES[number];

/** The server owns health. Field reasons and transport age only explain it. */
export function contractState(envelope: Envelope | null, enabled: boolean, draftOnly = false, stale = false): { category: ContractCategory; label: string; tone: Tone; hint: string } {
  const category: ContractCategory = draftOnly ? 'draft' : !enabled ? 'off' : envelope?.contract_health ?? 'unknown';
  const key = category === 'degraded' ? 'limited' : category;
  const tone: Tone = category === 'healthy' ? 'ok' : category === 'degraded' ? 'warn' : category === 'draft' ? 'info' : 'neutral';
  const details: string[] = [];
  if (stale) details.push(t('alpha7.contract.state.oldConnectionHelp'));
  const codes = Object.values(envelope?.fields ?? {}).flatMap(field => field.reasons.map(reason => reason.code));
  if (codes.includes('input_stale')) details.push(t('post1.state.staleExtra'));
  for (const [name, field] of Object.entries(envelope?.fields ?? {})) if (field.status !== 'valid') details.push(t('post1.state.fieldExtra', { name: fieldLabel(name), state: FIELD_STATUS[field.status].label }));
  if (envelope?.state_machine?.manual_restart || codes.includes('manual_restart')) details.push(t('alpha7.contract.state.manualHelp'));
  return { category, label: t(`alpha7.contract.state.${key}`), tone, hint: [t(`alpha7.contract.state.${key}Help`), ...details].join(' ') };
}

export interface ContractSource { id: string; name: string; entityId?: string; sourceIndex: number; kind: 'source' | 'contract'; inputName: string; ref: string; href: string; role: string }
/** Normal views show exactly the declared direct inputs, including contracts. */
export function contractSources(contractId: string, config: Registry | null, entities: HaEntity[]): ContractSource[] {
  const sources = asArray(config?.sources).filter(isObject); const contracts = asArray(config?.contracts).filter(isObject);
  const bindings = asArray(config?.bindings).filter(isObject);
  const contract = contracts.find(item => item.contract_id === contractId);
  return asArray(contract?.inputs).filter(isObject).flatMap<ContractSource>(input => {
    const ref = str(input.ref); const inputName = str(input.name); const target = resolveRef(ref, config);
    if (!target || target.id === contractId) return [];
    const typeId = str(contract?.type_id);
    const roleKey = typeId === 'opening_zone.facts' && inputName.startsWith('member_')
      ? 'inputs.opening_zone.facts.member' : ['sleep.state_view', 'wake.plan_view'].includes(typeId)
        ? `inputs.${typeId}.flow` : `inputs.${typeId}.${inputName}`;
    const role = hasTranslation(roleKey) ? t(roleKey) : t('beta16.inputs.role', { name: fieldLabel(inputName) });
    if (target.kind === 'contract') {
      const parent = contracts.find(item => item.contract_id === target.id);
      return [{ id: target.id, name: contractTitle(parent, target.id), sourceIndex: -1, kind: 'contract' as const,
        inputName, ref, role, href: `/contracts/view/${encodeURIComponent(target.id)}` }];
    }
    if (target.kind !== 'source') return [];
    const sourceIndex = sources.findIndex(item => item.source_id === target.id); const source = sources[sourceIndex];
    const binding = bindings.find(item => item.source_id === target.id);
    const entityId = str(asObject(binding?.adapter).ha_entity_id) || undefined;
    const name = entities.find(item => item.entity_id === entityId)?.display_name || str(binding?.display_name) || str(source?.display_name) || entityId || target.id;
    return [{ id: target.id, name, entityId, sourceIndex, kind: 'source' as const, inputName, ref, role,
      href: `/sources/devices?item=${sourceIndex}` }];
  });
}

export interface ContractSourcePath { source: ContractSource; owner: string; path: string[]; ids: string[] }
/** Technical details keep every declared path; repeated sources on different paths stay visible. */
export function contractSourcePaths(contractId: string, config: Registry | null, entities: HaEntity[]): ContractSourcePath[] {
  const contracts = asArray(config?.contracts).filter(isObject);
  const result: ContractSourcePath[] = [];
  const walk = (id: string, names: string[], ids: string[]) => {
    if (ids.includes(id) || ids.length >= 64) return;
    const path = [...names, contractTitle(contracts.find(c => c.contract_id === id), id)];
    const nextIds = [...ids, id];
    for (const input of contractSources(id, config, entities)) {
      if (input.kind === 'contract') walk(input.id, path, nextIds);
      else result.push({ source: input, owner: id, path: [...path, input.name], ids: [...nextIds, input.id] });
    }
  };
  walk(contractId, [], []);
  return [...new Map(result.map(item => [item.ids.join('\u0000'), item])).values()];
}

export function affectedContractInputs(envelope: Envelope, config: Registry | null, entities: HaEntity[]): (ContractSource & { reasons: Reason[] })[] {
  const reasons = Object.values(envelope.fields).flatMap(field => field.reasons);
  return contractSources(envelope.contract_id, config, entities).map(input => {
    const descendants = input.kind === 'contract' ? contractSourcePaths(input.id, config, entities).flatMap(path => path.ids) : [];
    const references = new Set([input.id, input.ref, input.inputName, ...descendants]);
    return { ...input, reasons: reasons.filter(reason => references.has(reason.input) || (!!reason.source_id && references.has(reason.source_id))) };
  }).filter(input => input.reasons.length > 0);
}
