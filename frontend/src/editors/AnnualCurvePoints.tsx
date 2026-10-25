import { t } from '@/i18n';
import { Input, Select } from '@/components/ui/form';
import { asArray, asObject, str, type Rule } from '@/features/authoring/model';
import { isObject, join, type Json, type JsonObject } from '@/lib/json';
import { AddButton, Fld, NumberField, RowActions } from './common';

export function AnnualCurvePoints({ value, onChange, pointer }: { value: Rule; onChange: (value: Rule) => void; pointer: string }) {
  const points = asArray(value.points).map(asObject);
  const set = (next: JsonObject[]) => onChange({ ...value, points: next });
  const patch = (i: number, update: JsonObject) => set(points.map((p, j) => i === j ? { ...p, ...update } : p));
  return <div className="flex flex-col gap-3">
    <Fld label={t('beta3.leapDay')} pointer={join(pointer, 'leap_day')}>{p => <Select {...p} value={str(value.leap_day)} onChange={e => {
      const next = { ...value };
      if (e.target.value) next.leap_day = e.target.value; else delete next.leap_day;
      onChange(next);
    }}>
      {['', 'skip', 'feb28', 'mar01'].map(key => <option key={key} value={key}>{t(`beta3.leapDay.${key || 'unset'}`)}</option>)}
    </Select>}</Fld>
    {points.map((point, i) => {
      const pp = join(pointer, 'points', i);
      const parameter = isObject(point.value);
      const day = str(point.day);
      const stamp = new Date(`2000-${day}T00:00:00Z`);
      const validDay = /^\d{2}-\d{2}$/.test(day) && !Number.isNaN(stamp.getTime()) && stamp.toISOString().slice(5, 10) === day;
      const duplicate = points.some((other, j) => j !== i && other.day === day);
      return <div key={i} className="grid gap-2 rounded border border-line p-2 sm:grid-cols-2">
        <Fld label={t('beta3.anchorDay')} pointer={join(pp, 'day')}>{p => <Input {...p} value={str(point.day)} maxLength={5} placeholder={t('beta3.dayPlaceholder')} onChange={e => patch(i, { day: e.target.value })} />}</Fld>
        <Fld label={t('beta3.anchorKind')} pointer={join(pp, 'kind')}>{p => <Select {...p} value={parameter ? 'parameter' : 'number'} onChange={e => patch(i, { value: e.target.value === 'parameter' ? { op: 'parameter', name: '' } : 0 })}>
          <option value="number">{t('beta3.number')}</option><option value="parameter">{t('beta3.parameter')}</option>
        </Select>}</Fld>
        {parameter ? <Fld label={t('beta3.parameterName')} pointer={join(pp, 'value', 'name')}>{p => <Input {...p} value={str(asObject(point.value).name)} onChange={e => patch(i, { value: { ...asObject(point.value), name: e.target.value } })} />}</Fld>
          : <NumberField label={t('beta3.anchorValue')} pointer={join(pp, 'value')} value={Number(point.value)} onChange={n => patch(i, { value: n })} />}
        <RowActions index={i} count={points.length} removeDisabled={points.length <= 2} onRemove={() => set(points.filter((_, j) => i !== j))} onMove={(from, to) => {
          const next = [...points]; const [moved] = next.splice(from, 1); next.splice(to, 0, moved!); set(next);
        }} />
        {(!validDay || duplicate) && <p role="alert" className="text-sm text-bad">{t(validDay ? 'beta3.duplicateDay' : 'beta3.invalidDay')}</p>}
      </div>;
    })}
    {points.length < 64 && <AddButton onClick={() => set([...points, { day: '12-31', value: 0 as Json }])}>{t('beta3.addAnchor')}</AddButton>}
  </div>;
}
