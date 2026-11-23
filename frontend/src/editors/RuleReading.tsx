import { asArray, asObject, str } from '@/features/authoring/model';
import { t, hasTranslation } from '@/i18n';
import { fieldLabel } from '@/i18n/codes';
import { formatDuration, formatNumber } from '@/lib/format';
import { isObject, type Json, type JsonObject } from '@/lib/json';

const code = (value: Json | undefined) => {
  const key = 'codes.' + String(value);
  return hasTranslation(key) ? t(key) : String(value ?? '–');
};
const label = (key: string, value: Json | undefined, fallback = '–') => {
  const full = key + String(value);
  return hasTranslation(full) ? t(full) : fallback;
};

export function ruleSentence(value: Json | undefined, definition: JsonObject, depth = 0): string {
  if (depth >= 12) return t('a2.rules.detail');
  const rule = asObject(value);
  const child = (value: Json | undefined) => ruleSentence(value, definition, depth + 1);
  const args = asArray(rule.args);
  const name = str(rule.name);

  switch (rule.op) {
    case 'input':
      return t('a2.rules.input', { name: fieldLabel(name) });
    case 'parameter': {
      const label = asObject(asObject(definition.parameter_schema)[name]).label;
      return t('a2.rules.parameter', { name: str(label) || fieldLabel(name) });
    }
    case 'ref': {
      const target = asObject(definition.rules)[name];
      return target === undefined || depth > 4 ? t('a2.rules.reference', { name }) : child(target);
    }
    case 'literal':
      return readingValue(rule.value, asObject(rule.schema));
    case 'compare':
      return t('a2.rules.compare', { left: child(rule.left), relation: label('a2.rules.compare.', rule.comparison), right: child(rule.right) });
    case 'and':
    case 'or':
      return args.map(arg => '(' + child(arg) + ')').join(t(rule.op === 'and' ? 'a2.rules.and' : 'a2.rules.or'));
    case 'not':
    case 'known_automatic':
      return t('a2.rules.' + rule.op, { value: child(rule.op === 'not' ? args[0] : rule.arg) });
    case 'first_match':
      return t('a2.rules.first_match', { cases: asArray(rule.cases).map(item => {
        const entry = asObject(item);
        return t('a2.rules.case', { condition: child(entry.when), result: child(entry.then) });
      }).join(' ') });
    case 'event_match':
      return t('a2.rules.event_match', { value: child(rule.arg), mode: label('a2.rules.event.', rule.mode ?? 'changed'), after: rule.after_state_entry ? t('a2.rules.afterEntry') : '' });
    case 'calendar_at':
      return t('a2.rules.calendar_at', { field: label('a2.calendar.field.', rule.field), date: child(rule.at), offset: Number(rule.offset_days ?? 0) });
    case 'calendar_active':
      return t('a2.rules.calendar_active', { events: child(rule.arg), pattern: child(rule.pattern), date: rule.on_date ? child(rule.on_date) : t('a2.rules.currentTime'), allDay: rule.all_day_only ? t('a2.rules.allDay') : '' });
    case 'daily_window':
      return t('a2.rules.daily_window', { date: child(rule.at), end: code(rule.end_phase) });
    case 'wake_plan':
      return t('a2.rules.wake_plan', { field: fieldLabel(str(rule.field)), calendar: fieldLabel(str(rule.calendar_input)), days: fieldLabel(str(rule.date_input)) });
    case 'machine':
      return t('a2.rules.machine');
    case 'machine_memory':
    case 'machine_field':
      return fieldLabel(str(rule.slot ?? rule.field));
    case 'annual_curve':
      return t('a2.rules.annual_curve', { date: child(rule.at) });
    case 'map':
      return t('a2.rules.map', { value: child(rule.arg) });
    case 'formula':
      return t('a2.rules.application', { operation: name, inputs: args.map(child).join(', ') });
    case 'fusion':
      return t('a2.rules.application', { operation: code(rule.strategy), inputs: args.map(child).join(', ') });
    case 'temporal':
      return t('a2.rules.temporal', { operation: code(rule.operation), inputs: args.map(child).join(', '), duration: ruleDuration(rule.duration_s, definition) });
    case 'opening_contact':
      return t('a2.rules.opening_contact', { inputs: args.map(child).join(', '), duration: ruleDuration(rule.duration_s, definition) });
    case 'opening_zone':
      return t('a2.rules.opening_zone', { field: fieldLabel(str(rule.field)) });
    default: {
      const key = 'a2.rules.' + str(rule.op);
      return hasTranslation(key) ? t(key, { inputs: args.map(child).join(', ') }) : t('a2.rules.detail');
    }
  }
}

export function ruleDuration(value: Json | undefined, definition: JsonObject): string {
  return typeof value === 'number' ? formatDuration(value) : value ? ruleSentence(value, definition) : '–';
}

function readingValue(value: Json | undefined, schema: JsonObject): string {
  if (typeof value === 'boolean') return t(value ? 'alpha7.device.yes' : 'alpha7.device.no');
  if (typeof value === 'number') {
    if (schema.unit === 'minute_of_day' && Number.isInteger(value) && value >= 0 && value < 1440) {
      return String(Math.floor(value / 60)).padStart(2, '0') + ':' + String(value % 60).padStart(2, '0');
    }
    return formatNumber(value) + (schema.unit ? ' ' + String(schema.unit) : '');
  }
  if (typeof value === 'string') return schema.type === 'enum' ? code(value) : value;
  return value == null ? '–' : t('alpha7.copy.structuredValue');
}

function mapKey(key: string): Json {
  try {
    return JSON.parse(key) as Json;
  } catch {
    return key;
  }
}

export function RuleReading({ value, definition, output }: { value: Json | undefined; definition: JsonObject; output?: string }) {
  const rule = asObject(value);
  if (!rule.op) return null;
  const points = asArray(rule.points).map(asObject);
  const pointParameter = points.map(point => asObject(point.value)).find(value => value.op === 'parameter');
  const declaration = asObject(asObject(definition.parameter_schema)[str(pointParameter?.name)]);
  const outputSchema = asObject(asObject(asObject(definition.schema).fields)[output ?? '']);
  const schema = { ...asObject(declaration.schema), ...outputSchema, ...asObject(rule.schema) };
  const map = rule.op === 'map';
  const curve = rule.op === 'annual_curve';
  const result = (entry: Json | undefined, resultSchema: JsonObject) => isObject(entry) && entry.op === 'parameter' && typeof entry.name === 'string' && Object.keys(entry).length === 2
    ? ruleSentence(entry, definition) : readingValue(entry, resultSchema);
  const rows = map
    ? Object.entries(asObject(rule.entries)).map(([key, entry]) => [readingValue(mapKey(key), {}), result(entry, schema)])
    : curve ? points.map(point => {
      const [month, day] = str(point.day).split('-');
      return [[day, month].filter(Boolean).join('.'), result(point.value, schema)];
    }) : [];
  return <div className="space-y-2 text-sm">
    <p>{ruleSentence(value, definition)}</p>
    {(map || curve) && <div className="overflow-x-auto">
      <table className="w-full text-left">
        <caption className="sr-only">{t(map ? 'a2.rules.mapTable' : 'a2.rules.curveTable')}</caption>
        <thead><tr>
          <th scope="col" className="border-b border-line p-2">{t(map ? 'a2.rules.raw' : 'a2.rules.day')}</th>
          <th scope="col" className="border-b border-line p-2">{t(curve && schema.unit === 'minute_of_day' ? 'a2.rules.time' : 'a2.rules.result')}</th>
        </tr></thead>
        <tbody>{rows.map(([key, result], index) => <tr key={index} className="border-b border-line">
          <th scope="row" className="p-2 font-normal">{key}</th><td className="p-2">{result}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </div>;
}
