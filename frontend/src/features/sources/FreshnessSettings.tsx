import { t } from '@/i18n';
import { Field, Select } from '@/components/ui/form';
import { str } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';
import { FRESHNESS_MODES } from './devicePlan';
import { DurationField as SecondsField } from '@/components/SecondsField';

export function FreshnessSettings({ value, onChange, livenessSources = [], template = false }: { value: JsonObject; onChange: (value: JsonObject) => void; livenessSources?: { id: string; name: string }[]; template?: boolean }) {
  const mode = str(value.mode);
  return <div className="space-y-3">
    <Field label={t('alpha7.freshness.mode')} help={t('a2.help.liveness')}>{props => <Select {...props} value={mode} onChange={event => { const next = event.target.value; onChange({ ...value, future_tolerance_s: value.future_tolerance_s ?? 1, mode: next, ...(!template && next === 'liveness_source' ? { liveness_source: value.liveness_source ?? '' } : {}) }); }}><option value="" disabled>{t('b4.choose')}</option>{FRESHNESS_MODES.map(item => <option key={item} value={item}>{t(`alpha7.freshness.${item}`)}</option>)}</Select>}</Field>
    {mode && <p className="text-xs text-muted">{t(`alpha7.freshness.${mode}Help`)}</p>}
    {mode && mode !== 'event_stateful' && <SecondsField label={t('b4.interval')} min={0.000001} value={Number(value.interval_s)} onChange={interval_s => onChange({ ...value, interval_s })} />}
    {mode && <SecondsField label={t('editors.RegistryObjects.zukunftstoleranz_s_a11f48')} value={Number(value.future_tolerance_s ?? 1)} onChange={future_tolerance_s => onChange({ ...value, future_tolerance_s })} />}
    {!template && mode === 'liveness_source' && <Field label={t('alpha7.freshness.liveness')} help={t('a2.help.liveness')}>{props => <Select {...props} value={str(value.liveness_source)} onChange={event => onChange({ ...value, liveness_source: event.target.value })}><option value="">{t('alpha7.device.choose')}</option>{livenessSources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</Select>}</Field>}
  </div>;
}
