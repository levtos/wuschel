import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Field, Input, Select } from '@/components/ui/form';
import { Button } from '@/components/ui/button';
import { t } from '@/i18n';
import { durationSeconds, durationText, type DurationUnit } from '@/lib/duration';
import { formatNumber } from '@/lib/format';

export const invalidDurations = new Set<string>();
export function minuteHint(seconds: number) { return Number.isFinite(seconds) && seconds >= 0 ? t(seconds === 60 ? 'alpha7.freshness.oneMinute' : 'alpha7.freshness.minutes', { minutes: formatNumber(seconds / 60, 2) }) : undefined; }
export function SecondsField({ label, value, onChange, min = 0, max = 86400, disabled }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; disabled?: boolean }) {
  return <Field label={label} hint={minuteHint(value)}>{props => <Input {...props} type="number" disabled={disabled} min={min} max={max} step={1} value={Number.isFinite(value) ? value : ''} onChange={event => onChange(event.target.value === '' ? 0 : event.target.valueAsNumber)} />}</Field>;
}
export function DurationField({ label, value, onChange, min = 0, max = Number.MAX_SAFE_INTEGER / 1000000, disabled, integerSeconds = false, pointer, errors }: { label: ReactNode; value: number; onChange: (value: number) => void; min?: number; max?: number; disabled?: boolean; integerSeconds?: boolean; pointer?: string; errors?: Parameters<typeof Field>[0]['errors'] }) {
  label = typeof label === 'string' ? label.replace(/\s*\((?:s|Sekunden|Minuten|Stunden)\)|\s+in Sekunden/g, '') : label;
  const id = useId();
  const [unit, setUnit] = useState<DurationUnit>(value >= 3600 && value % 3600 === 0 ? 'hours' : 'minutes');
  const initial = (seconds: number, nextUnit: DurationUnit) => Number.isFinite(seconds) ? String(seconds / (nextUnit === 'hours' ? 3600 : 60)).replace('.', ',') : '';
  const [text, setText] = useState(initial(value, unit));
  const [precise, setPrecise] = useState(value > 0 && value < 60);
  const [error, setError] = useState(Number.isFinite(value) ? '' : t('b4.time.required'));
  const lastEmitted = useRef(value);
  useEffect(() => () => { invalidDurations.delete(id); }, [id]);
  useEffect(() => { if (!Number.isFinite(value)) { invalidDurations.add(id); setError(t('b4.time.required')); } else if (!Object.is(value, lastEmitted.current)) { setText(initial(value, unit)); setError(''); invalidDurations.delete(id); lastEmitted.current = value; } }, [value, unit, id]);
  const change = (next: string) => {
    setText(next);
    const seconds = durationSeconds(next, unit);
    const message = next === '' ? t('b4.time.required') : seconds === null || integerSeconds && !Number.isInteger(seconds) ? t('b4.time.precision') : seconds < min || seconds > max ? t('b4.time.bounds', { min: durationText(min), max: durationText(max) }) : '';
    setError(message);
    if (message) invalidDurations.add(id); else { invalidDurations.delete(id); lastEmitted.current = seconds!; onChange(seconds!); }
  };
  return <div className="space-y-2">
    {precise ? <><p>{label} · {durationText(value)}</p><p className="text-xs text-muted">{t('b4.time.preserve')}</p><Button disabled={disabled} onClick={() => setPrecise(false)}>{t('b4.time.change')}</Button><details><summary>{t('b4.technical')}</summary>{value} {t('b4.secondsTechnical')}</details></> : <Field label={label} hint={durationText(value)} pointer={pointer} errors={errors}>{props => <div className="flex flex-wrap gap-2"><Input {...props} className="min-h-11 w-36" inputMode="decimal" value={text} disabled={disabled} aria-invalid={props['aria-invalid'] || !!error} onChange={event => change(event.target.value)} /><Select aria-label={t('b4.time.unit')} className="min-h-11 w-40" disabled={disabled || !!error && text !== ''} value={unit} onChange={event => { const next = event.target.value as DurationUnit; setUnit(next); setText(initial(value, next)); }}>{(['minutes', 'hours'] as const).map(item => <option key={item} value={item}>{t(`b4.time.${item}`)}</option>)}</Select></div>}</Field>}
    {precise && errors?.map(issue => <p key={issue.code} role="alert" className="text-sm text-bad">{issue.message}</p>)}
    {error && <p role="alert" className="text-sm text-bad">{error}</p>}
  </div>;
}
