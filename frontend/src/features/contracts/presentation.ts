import { hasTranslation, t } from '@/i18n';
import { fieldLabel } from '@/i18n/codes';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import type { TypeEntry } from '@/features/authoring/catalog';
import { resolveRef } from '@/features/authoring/usage';
import { isObject, type JsonObject } from '@/lib/json';
import { displayValue, FIELD_STATUS } from '@/lib/status';
import type { Envelope, FieldValue, HaEntity, Registry } from '@/lib/types';
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
  const typeId = envelope.type?.type_id;
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

export interface ContractSource { id: string; name: string; entityId?: string; sourceIndex: number }
/** Walk declared inputs only, including upstream contracts, with cycle protection. */
export function contractSources(contractId: string, config: Registry | null, entities: HaEntity[]): ContractSource[] {
  const seen = new Set<string>(); const result = new Map<string, ContractSource>();
  const sources = asArray(config?.sources).filter(isObject); const contracts = asArray(config?.contracts).filter(isObject);
  const bindings = asArray(config?.bindings).filter(isObject);
  const visit = (id: string) => {
    if (seen.has(id)) return; seen.add(id);
    const contract = contracts.find(item => item.contract_id === id);
    for (const input of asArray(contract?.inputs).filter(isObject)) {
      const target = resolveRef(str(input.ref), config);
      if (target?.kind === 'contract') visit(target.id);
      if (target?.kind !== 'source') continue;
      const sourceIndex = sources.findIndex(item => item.source_id === target.id); const source = sources[sourceIndex];
      const attached = bindings.filter(item => item.source_id === target.id);
      if (!attached.length) result.set(target.id, { id: target.id, name: str(source?.display_name) || target.id, sourceIndex });
      for (const binding of attached) {
        const entityId = str(asObject(binding.adapter).ha_entity_id) || undefined;
        const name = entities.find(item => item.entity_id === entityId)?.display_name || str(binding.display_name) || str(source?.display_name) || entityId || target.id;
        result.set(`${target.id}:${entityId ?? str(binding.binding_id)}`, { id: target.id, name, entityId, sourceIndex });
      }
    }
  };
  visit(contractId); return [...result.values()];
}
