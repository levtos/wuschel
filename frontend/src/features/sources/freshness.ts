import { asArray, asObject, str } from '@/features/authoring/model';
import { clone, isObject, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { durationText } from '@/lib/duration';
import { t } from '@/i18n';

export const SENSOR_KINDS = ['contact', 'temperature', 'motion', 'counter'] as const;

export function importFreshnessSources(config: Registry, target: Registry): JsonObject[] {
  const existing = new Map(asArray(target.sources).filter(isObject).map(source => [source.source_id, source]));
  const signature = (registry: Registry, source: JsonObject) => {
    const selection = asObject(source.freshness_selection);
    const rule = effectiveRule(registry, source);
    return JSON.stringify([selection.kind ?? 'own', selection.template_id ?? null,
      ...['mode', 'interval_s', 'future_tolerance_s', 'liveness_source'].map(key => rule[key] ?? null)]);
  };
  return asArray(config.sources).filter(isObject).filter(source => {
    if (source.origin === 'manual') return false;
    const before = existing.get(source.source_id);
    return !before || signature(config, source) !== signature(target, before);
  });
}
export function freshnessChoiceLabel(config: Registry, value: unknown): string | null {
  if (!isObject(value) || !['own', 'shared'].includes(str(value.kind))) return null;
  const template = asArray(config.freshness_templates).filter(isObject).find(row => row.template_id === value.template_id);
  const rule = value.kind === 'own' ? asObject(value.rule) : asObject(template?.rule);
  const origin = value.kind === 'own' ? t('b4.ownRule') : `${t('b4.sharedRule')}: ${str(template?.display_name) || t('b4.unavailable')}`;
  const mode = str(rule.mode);
  return `${origin} · ${mode ? t(`alpha7.freshness.${mode}`) : t('b4.noRule')}${mode && mode !== 'event_stateful' ? ` · ${durationText(Number(rule.interval_s))}` : ''}`;
}
export function upgradeFreshness(config: Registry): Registry {
  for (const key of ['sources', 'freshness_templates']) if (config[key] !== undefined && (!Array.isArray(config[key]) || !asArray(config[key]).every(isObject))) throw new Error(t('alpha7.device.invalidConfig'));
  const next = clone(config);
  next.config_schema_version = 4; next.fingerprint_version = 4;
  next.freshness_templates ??= [];
  next.sources = asArray(next.sources).filter(isObject).map(source => {
    if (!source.freshness) return source;
    const selection = asObject(source.freshness_selection);
    const result: JsonObject = { ...source, freshness_selection: { kind: 'own', rule: source.freshness, ...(selection.comparison_template_id ? { comparison_template_id: selection.comparison_template_id } : {}) } };
    delete result.freshness;
    return result;
  });
  return next;
}
export function effectiveRule(config: Registry, source: JsonObject): JsonObject {
  const selection = asObject(source.freshness_selection);
  if (selection.kind === 'own') return asObject(selection.rule);
  if (selection.kind === 'shared') {
    const template = asArray(config.freshness_templates).filter(isObject).find(item => item.template_id === selection.template_id);
    return { ...asObject(template?.rule), liveness_source: selection.liveness_source ?? null };
  }
  return asObject(source.freshness);
}
export function ownRule(source: JsonObject, rule: JsonObject): JsonObject {
  if (!source.freshness_selection) return { ...source, freshness: rule };
  const selection = asObject(source.freshness_selection);
  return { ...source, freshness_selection: { kind: 'own', rule, ...(selection.template_id || selection.comparison_template_id ? { comparison_template_id: selection.template_id || selection.comparison_template_id } : {}) } };
}
export function freshnessLabel(config: Registry, source: JsonObject): string {
  const selection = asObject(source.freshness_selection);
  const templateId = selection.template_id ?? selection.comparison_template_id;
  const template = asArray(config.freshness_templates).filter(isObject).find(item => item.template_id === templateId);
  const rule = effectiveRule(config, source);
  const text = rule.mode === 'event_stateful' ? t('b4.noInterval') : durationText(Number(rule.interval_s));
  if (selection.kind === 'shared') return `${t('b4.uses')} ${str(template?.display_name)} · ${text}`;
  if (!template) return `${t('b4.unassigned')} · ${text}`;
  const comparison: JsonObject = { ...asObject(template.rule), liveness_source: rule.liveness_source ?? null };
  const equal = ['mode', 'interval_s', 'future_tolerance_s', 'liveness_source'].every(key => (rule[key] ?? null) === (comparison[key] ?? null));
  return `${t(equal ? 'b4.sameOwn' : 'b4.differs')} · ${text}`;
}
