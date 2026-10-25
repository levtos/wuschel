import { useState } from 'react';
import { Field, Select } from '@/components/ui/form';
import { asArray, asObject, str } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { durationText } from '@/lib/duration';
import { t } from '@/i18n';
import { effectiveRule, ownRule, SENSOR_KINDS, upgradeFreshness } from './freshness';

export type FreshnessChoices = Record<string, { kind: string; template_id?: string; sensor_kind?: string; liveness_source?: string }>;
export function assignFreshness(config: Registry, choices: FreshnessChoices): Registry {
  const next = upgradeFreshness(config);
  next.sources = asArray(next.sources).filter(isObject).map((source): JsonObject => {
    const choice = choices[str(source.source_id)];
    if (!choice) return source;
    if (choice.kind === 'own') return { ...ownRule(source, effectiveRule(next, source)), ...(choice.sensor_kind ? { sensor_kind: choice.sensor_kind, sensor_kind_origin: 'confirmed' } : {}) };
    return { ...source, sensor_kind: choice.sensor_kind || source.sensor_kind || null, sensor_kind_origin: 'confirmed', freshness_selection: { kind: 'shared', template_id: choice.template_id || '', ...(choice.liveness_source ? { liveness_source: choice.liveness_source } : {}) } };
  });
  return next;
}
export function FreshnessAssignment({ config, target = config, sourceIds, choices, onChange }: { config: Registry; target?: Registry; sourceIds?: string[]; choices: FreshnessChoices; onChange: (choices: FreshnessChoices) => void }) {
  const [confirmedKind, setConfirmedKind] = useState<Record<string, string>>({});
  const sources = asArray(config.sources).filter(isObject).filter(source => source.origin !== 'manual' && (!sourceIds || sourceIds.includes(str(source.source_id))));
  const templates = asArray(target.freshness_templates).filter(isObject);
  const groups = [...SENSOR_KINDS, 'unknown'];
  return <div className="space-y-5">{groups.map(group => {
    const rows = sources.filter(source => (source.sensor_kind && source.sensor_kind_origin ? source.sensor_kind : 'unknown') === group);
    if (!rows.length) return null;
    const kind = group === 'unknown' ? confirmedKind[group] ?? '' : group;
    const matching = templates.filter(template => template.sensor_kind === kind);
    const choose = (value: string) => {
      const next = { ...choices };
      rows.forEach(source => { const id = str(source.source_id); if (!value) delete next[id]; else next[id] = value === 'own' ? { kind: 'own', ...(kind ? { sensor_kind: kind } : {}) } : { kind: 'shared', template_id: value, sensor_kind: kind }; });
      onChange(next);
    };
    const selected = rows.every(source => choices[str(source.source_id)]) ? choices[str(rows[0]!.source_id)] : null;
    return <section key={group} className="space-y-2 rounded-lg border border-line p-3"><h3>{t(`b4.${group}`)} · {rows.length === 1 ? t('b4.sourceOne') : t('b4.sourceCount', { count: rows.length })}</h3>
      <ul>{rows.map(source => <li key={str(source.source_id)}>{str(source.display_name) || str(source.source_id)} · {effectiveRule(config, source).mode === 'event_stateful' ? t('b4.noInterval') : durationText(Number(effectiveRule(config, source).interval_s))}</li>)}</ul>
      {kind && matching.length === 1 && !selected && <p>{t('b4.sharedProposal', { name: str(matching[0]!.display_name) })}</p>}
      {group === 'unknown' && <Field label={t('b4.confirmKind')}>{p => <Select {...p} value={kind} onChange={event => { setConfirmedKind({ ...confirmedKind, [group]: event.target.value }); const next = { ...choices }; rows.forEach(source => delete next[str(source.source_id)]); onChange(next); }}><option value="">{t('b4.choose')}</option>{SENSOR_KINDS.map(item => <option key={item} value={item}>{t(`b4.${item}`)}</option>)}</Select>}</Field>}
      <Field label={t('b4.assignImport')}>{p => <Select {...p} value={selected?.kind === 'own' ? 'own' : selected?.template_id ?? ''} onChange={event => choose(event.target.value)}><option value="">{t('b10.keepFileRules')}</option><option value="own">{t('b4.ownFile')}</option>{matching.map(template => <option key={str(template.template_id)} value={str(template.template_id)}>{t('b4.shared')} · {str(template.display_name)} · {durationText(Number(asObject(template.rule).interval_s))}</option>)}</Select>}</Field>
      {selected?.kind === 'shared' && asObject(matching.find(template => template.template_id === selected.template_id)?.rule).mode === 'liveness_source' && rows.map(source => <Field key={str(source.source_id)} label={t('b4.livenessFor', { name: str(source.display_name) || str(source.source_id) })}>{p => <Select {...p} value={choices[str(source.source_id)]?.liveness_source ?? ''} onChange={event => onChange({ ...choices, [str(source.source_id)]: { ...choices[str(source.source_id)]!, liveness_source: event.target.value } })}><option value="">{t('b4.choose')}</option>{asArray(config.sources).filter(isObject).filter(candidate => candidate.source_id !== source.source_id && (candidate.source_type === 'boolean' || asObject(candidate.value_schema).type === 'bool')).map(candidate => <option key={str(candidate.source_id)} value={str(candidate.source_id)}>{str(candidate.display_name) || str(candidate.source_id)}</option>)}</Select>}</Field>)}
    </section>;
  })}</div>;
}
