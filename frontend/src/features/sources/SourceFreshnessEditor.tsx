import { Link } from 'react-router';
import { Field, Select } from '@/components/ui/form';
import { Button } from '@/components/ui/button';
import { useOptionalWorkingDocument } from '@/features/authoring/WorkingDocument';
import { asArray, asObject, str } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import { t } from '@/i18n';
import { effectiveRule, freshnessLabel, ownRule, SENSOR_KINDS } from './freshness';
import { FreshnessSettings } from './FreshnessSettings';
import { durationText } from '@/lib/duration';

export function SourceFreshnessEditor({ value, onChange, sources, config: suppliedConfig }: { value: JsonObject; onChange: (source: JsonObject) => void; sources: JsonObject[]; config?: JsonObject }) {
  const doc = useOptionalWorkingDocument();
  const config = suppliedConfig ?? doc?.config ?? {};
  const selection = asObject(value.freshness_selection);
  const templates = asArray(config.freshness_templates).filter(isObject).filter(row => row.sensor_kind === value.sensor_kind);
  const rule = effectiveRule(config, value);
  const comparison = asArray(config.freshness_templates).filter(isObject).find(row => row.template_id === selection.comparison_template_id);
  const saved = asArray(doc?.draft?.config.sources).filter(isObject).find(row => row.source_id === value.source_id);
  const before = saved && doc?.draft ? effectiveRule(doc.draft.config, saved) : null;
  return <section className="space-y-3">
    <h3>{t('b4.title')}</h3><p>{freshnessLabel(config, value)}</p>
    {comparison && <p>{t('b4.sourceRule')}: {durationText(Number(rule.interval_s))} · {t('b4.templateRule')}: {durationText(Number(asObject(comparison.rule).interval_s))}</p>}
    {before && before.interval_s !== rule.interval_s && <p>{t('b4.beforeAfter', { before: durationText(Number(before.interval_s)), after: durationText(Number(rule.interval_s)) })}</p>}
    {value.origin !== 'manual' && <>
      <Field label={t('b4.confirmKind')}>{p => <Select {...p} value={str(value.sensor_kind)} onChange={event => onChange({ ...ownRule(value, rule), sensor_kind: event.target.value || null, sensor_kind_origin: event.target.value ? 'confirmed' : null })}><option value="">{t('b4.unknown')}</option>{SENSOR_KINDS.map(kind => <option key={kind} value={kind}>{t(`b4.${kind}`)}</option>)}</Select>}</Field>
      <Field label={t('b4.shared')}>{p => <Select {...p} value={selection.kind === 'shared' ? str(selection.template_id) : ''} onChange={event => { if (!event.target.value) onChange(ownRule(value, rule)); else onChange({ ...value, sensor_kind_origin: 'confirmed', freshness_selection: { kind: 'shared', template_id: event.target.value } }); }}><option value="">{t('b4.own')}</option>{templates.map(template => <option key={str(template.template_id)} value={str(template.template_id)}>{str(template.display_name)}</option>)}</Select>}</Field>
      <Link className="inline-flex min-h-11 items-center underline" to="/sources/freshness">{t('b4.templateChange')}</Link>
    </>}
    {selection.kind === 'shared' ? <><p>{t('b4.ownHelp')}</p><Button onClick={() => onChange(ownRule(value, rule))}>{t('b4.own')}</Button>{rule.mode === 'liveness_source' && <Field label={t('alpha7.freshness.liveness')} help={t('a2.help.liveness')}>{p => <Select {...p} value={str(selection.liveness_source)} onChange={event => onChange({ ...value, freshness_selection: { ...selection, liveness_source: event.target.value } })}><option value="">{t('b4.choose')}</option>{sources.filter(source => source.source_id !== value.source_id && (source.source_type === 'boolean' || asObject(source.value_schema).type === 'bool')).map(source => <option key={str(source.source_id)} value={str(source.source_id)}>{str(source.display_name) || str(source.source_id)}</option>)}</Select>}</Field>}</> : <FreshnessSettings value={rule} onChange={next => onChange(ownRule(value, next))} livenessSources={sources.filter(source => source.source_id !== value.source_id && (source.source_type === 'boolean' || asObject(source.value_schema).type === 'bool')).map(source => ({ id: str(source.source_id), name: str(source.display_name) || str(source.source_id) }))} />}
  </section>;
}
