import { Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Collapsible, Tip } from '@/components/ui/controls';
import { Input } from '@/components/ui/form';
import { DurationField } from '@/components/SecondsField';
import { Fld } from '@/editors/common';
import { merged, SchemaForm } from '@/editors/SchemaForm';
import type { TypeEntry } from '@/features/authoring/catalog';
import { explain, useIssues } from '@/features/authoring/issues';
import { asArray, asObject, str } from '@/features/authoring/model';
import { definitionHref, ObjectReadLink } from '@/features/authoring/ObjectReadLinks';
import { codeLabel, fieldLabel } from '@/i18n/codes';
import { hasTranslation, t } from '@/i18n';
import { deref } from '@/lib/capabilities';
import { durationText } from '@/lib/duration';
import { formatNumber } from '@/lib/format';
import { deepEqual, isObject, join, setAt, type Json, type JsonObject } from '@/lib/json';

type Display = 'plain' | 'time_of_day' | 'duration' | 'weekday';
interface Parameter {
  name: string;
  label: string;
  description: string;
  unit: string;
  group: string;
  display: Display;
  type: string;
  spec: JsonObject;
  required: boolean;
  minimum?: number;
  maximum?: number;
  standard?: Json;
}

function parameters(root: JsonObject): Parameter[] {
  const required = new Set(asArray(root.required));
  return Object.entries(asObject(root.properties)).map(([name, raw]) => {
    const spec = merged(root, deref(root, raw) ?? {});
    const type = str(spec.type);
    const numeric = type === 'number' || type === 'integer';
    const unit = str(spec.unit);
    const style = spec.display;
    const display = style === 'plain' || style === 'time_of_day' || style === 'duration' || style === 'weekday'
      ? style
      : numeric && unit === 'minute_of_day' ? 'time_of_day'
        : numeric && (unit === 's' || name.endsWith('_s')) ? 'duration' : 'plain';
    return {
      name, spec, display, type, unit,
      label: fieldLabel(str(spec.title) || name),
      description: str(spec.description) || (name === 'grace_s' ? t('a2.help.grace') : ''),
      group: str(spec.group),
      required: required.has(name),
      minimum: typeof spec.minimum === 'number' ? spec.minimum : undefined,
      maximum: typeof spec.maximum === 'number' ? spec.maximum : undefined,
      standard: spec.default ?? undefined,
    };
  });
}

function timeOfDay(value: Json | undefined): string {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value >= 1440) return '';
  const hour = String(Math.floor(value / 60)).padStart(2, '0');
  const minute = String(value % 60).padStart(2, '0');
  return hour + ':' + minute;
}

function parameterValue(parameter: Parameter, value: Json | undefined): string {
  if (value === undefined || value === null) return t('a2.parameters.unset');
  if (typeof value === 'boolean') return t(value ? 'alpha7.device.yes' : 'alpha7.device.no');
  if (typeof value === 'number') {
    if (parameter.display === 'time_of_day') return timeOfDay(value) || formatNumber(value);
    if (parameter.display === 'duration' && value >= 0) {
      return value > 0 && value < 60 ? t('a2.parameters.seconds', { value: formatNumber(value) }) : durationText(value);
    }
    return formatNumber(value);
  }
  if (typeof value === 'string' && parameter.name === 'calendar_conflict_behavior' && value === 'ignore') return t('a2.calendar.ignoreConflict');
  return typeof value === 'string' ? (Array.isArray(parameter.spec.enum) && hasTranslation('codes.' + value) ? codeLabel(value) : value) : t('a2.parameters.configured');
}

function parameterRange(parameter: Parameter): string {
  if (Array.isArray(parameter.spec.enum)) {
    return parameter.spec.enum.map(value => parameterValue(parameter, value)).join(', ');
  }
  const { minimum, maximum, display } = parameter;
  if (minimum !== undefined && maximum !== undefined) return t('a2.parameters.range', {
    min: parameterValue(parameter, minimum), max: parameterValue(parameter, maximum),
  });
  if (minimum !== undefined) return t('a2.parameters.minimum', { value: parameterValue(parameter, minimum) });
  if (maximum !== undefined) return t('a2.parameters.maximum', { value: parameterValue(parameter, maximum) });
  if (display === 'time_of_day') return t('a2.parameters.range', { min: '00:00', max: '23:59' });
  if (parameter.type === 'boolean') return t('a2.parameters.booleanRange');
  return t('a2.parameters.noRange');
}

function ParameterControl({ parameter, root, value, pointer, change }: {
  parameter: Parameter;
  root: JsonObject;
  value: Json | undefined;
  pointer: string;
  change?: (value: Json | undefined) => void;
}) {
  const issues = useIssues(pointer);
  const { display, label, minimum, maximum, required, spec } = parameter;
  if (!change) {
    if (isObject(value) || Array.isArray(value)) return <Collapsible title={label}>
      <fieldset disabled><SchemaForm root={root} node={spec} label={label} value={value} pointer={pointer} onChange={() => undefined} /></fieldset>
    </Collapsible>;
    return <div><p className="text-sm font-medium">{label}</p><p className="text-sm">{parameterValue(parameter, value)}</p></div>;
  }
  if (display === 'time_of_day' && ['number', 'integer'].includes(parameter.type)) {
    return <Fld label={label} pointer={pointer}>{props => <Input {...props} type="time" step={60} min={timeOfDay(minimum)} max={timeOfDay(maximum)} value={timeOfDay(value)} onChange={event => {
      const time = event.target.value;
      const [hour = 0, minute = 0] = time.split(':').map(Number);
      change(time ? hour * 60 + minute : undefined);
    }} />}</Fld>;
  }
  if (display === 'duration' && ['number', 'integer'].includes(parameter.type)) {
    if (value === undefined && !required) return <div className="space-y-1">
      <p className="text-sm font-medium">{label}</p>
      <Button size="sm" onClick={() => change(minimum ?? 0)}>{t('editors.SchemaForm.wert_festlegen_55b559')}</Button>
    </div>;
    return <DurationField label={label} pointer={pointer} errors={issues.map(issue => ({ code: issue.code, message: explain(issue) }))} value={typeof value === 'number' ? value : NaN} min={minimum} max={maximum} integerSeconds={parameter.type === 'integer'} onChange={change} />;
  }
  if (parameter.type === 'boolean' && !spec.enum) {
    return <Fld label={label} pointer={pointer}>{props => <input {...props} type="checkbox" className="h-5 w-5 accent-accent" checked={value === true} onChange={event => change(event.target.checked)} />}</Fld>;
  }
  const caption = parameter.unit && display === 'plain' ? t('alpha8.parameters.unitHint', { description: label, unit: parameter.unit }) : label;
  return <SchemaForm root={root} node={spec} label={caption} required={required} value={value} pointer={pointer} onChange={change} />;
}

export function ContractParameters({ entry, value, pointer, onChange }: {
  entry?: TypeEntry;
  value: JsonObject;
  pointer: string;
  onChange?: (value: JsonObject) => void;
}) {
  const root = entry?.parameterSchema ?? {};
  const items = parameters(root);
  if (!items.length) return <div className="space-y-2 text-sm">
    <p>{entry ? t('a2.parameters.noSettings', { name: entry.displayName || entry.type_id }) : t('editors.RegistryObjects.keine_parameter_bekannt_272f5e')}</p>
    {entry && <ObjectReadLink to={definitionHref(entry.type_id, entry.type_version) + '?tab=rules'}>{t('a2.parameters.openRule')}</ObjectReadLink>}
  </div>;

  const groups = new Map<string, Parameter[]>();
  for (const item of items) groups.set(item.group, [...(groups.get(item.group) ?? []), item]);
  return <div className="space-y-4">
    {onChange && <p className="text-xs text-muted">{t('alpha8.parameters.draftHint')}</p>}
    {[...groups].map(([group, rows]) => <section key={group}>
      {group && <h4 className="mb-2 font-medium">{group}</h4>}
      <ul className="divide-y divide-line rounded-md border border-line">
        {rows.map(row => {
          const fieldPointer = join(pointer, row.name);
          const stored = value[row.name];
          const current = stored === undefined ? row.standard : stored;
          const change = onChange ? (next: Json | undefined) => onChange(setAt(value, join('', row.name), next)) : undefined;
          const unit = row.unit === 'minute_of_day' ? t('a2.parameters.clockUnit') : row.unit;
          return <li key={row.name} className="space-y-2 p-3">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1"><ParameterControl parameter={row} root={root} value={current} pointer={fieldPointer} change={change} /></div>
              {row.description && <Tip label={row.description}><span tabIndex={0} aria-label={t('a2.parameters.help', { name: row.label })} className="inline-flex rounded p-1"><Info size={16} aria-hidden="true" /></span></Tip>}
            </div>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              {unit && <div><dt className="inline">{t('a2.parameters.unit')}</dt>{' '}<dd className="inline">{unit}</dd></div>}
              <div><dt className="inline">{t('a2.parameters.default')}</dt>{' '}<dd className="inline">{row.standard === undefined ? t('a2.parameters.noDefault') : parameterValue(row, row.standard)}</dd></div>
              <div><dt className="inline">{t('a2.parameters.allowed')}</dt>{' '}<dd className="inline">{parameterRange(row)}</dd></div>
            </dl>
            {change && row.standard !== undefined && <Button size="sm" disabled={stored === undefined || deepEqual(stored, row.standard)} onClick={() => change(undefined)}>{t('a2.parameters.reset')}</Button>}
          </li>;
        })}
      </ul>
    </section>)}
  </div>;
}

const tableColumns = ['name', 'description', 'type', 'unit', 'default', 'range', 'group', 'display'] as const;

export function TemplateParameters({ root }: { root: JsonObject }) {
  const items = parameters(root);
  if (!items.length) return null;
  return <div className="space-y-3">
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="sr-only">{t('a2.parameters.templateTitle')}</caption>
        <thead><tr>{tableColumns.map(column => <th key={column} scope="col" className="border-b border-line p-2">{t('a2.parameters.column.' + column)}</th>)}</tr></thead>
        <tbody>{items.map(item => <tr key={item.name} className="border-b border-line">
          <th scope="row" className="p-2 font-medium">{item.label}</th>
          <td className="p-2">{item.description || '–'}</td>
          <td className="p-2">{codeLabel(Array.isArray(item.spec.enum) ? 'enum' : item.spec.format === 'date-time' ? 'timestamp' : item.type === 'integer' ? 'number' : item.type)}</td>
          <td className="p-2">{item.unit === 'minute_of_day' ? t('a2.parameters.clockUnit') : item.unit || '–'}</td>
          <td className="p-2">{item.standard === undefined ? '–' : parameterValue(item, item.standard)}</td>
          <td className="p-2">{parameterRange(item)}</td>
          <td className="p-2">{item.group || '–'}</td>
          <td className="p-2">{t('a2.parameters.display.' + item.display)}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <Collapsible title={t('alpha10.technical')}><pre className="max-h-80 overflow-auto rounded border border-line p-3 text-xs">{JSON.stringify(root, null, 2)}</pre></Collapsible>
  </div>;
}
