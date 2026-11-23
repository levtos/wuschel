import { Fragment } from 'react';
import { t } from '@/i18n';
import { asObject, str, type FieldSchema } from '@/features/authoring/model';
import { formatTime } from '@/lib/format';
import type { Envelope, FieldValue, Registry } from '@/lib/types';
import { orderedFields } from './outputPresentation';
import { contractFieldLabel, contractFieldValue, contractTypeId } from './presentation';
import { notApplicableValue } from './inputPresentation';
import { InlineHelp } from '@/components/ui/inline-help';
import { FIELD_STATUS, QUALITY } from '@/lib/status';
import { isObject } from '@/lib/json';
import { CalendarReading, isCalendarCache } from '@/components/CalendarReading';

export function CurrentValues({ envelope, fields, config, inputs = {}, names = {}, attribution }: { envelope: Envelope; fields: Record<string, FieldSchema>; config: Registry | null; inputs?: Record<string, FieldValue>; names?: Record<string, string>; attribution: boolean }) {
  const type = contractTypeId(envelope); const rows = orderedFields(fields, envelope.fields);
  const common = rows[0]?.field.attribution?.kind ?? 'unknown';
  const attributionText = (field?: FieldValue) => t(field?.attribution?.kind === 'known_user' ? 'b22.output.byHand' : field?.attribution?.kind === 'known_automatic' ? 'alpha9.context.automatic' : 'alpha9.context.unknown');
  const present = (field: FieldValue) => field.status === 'valid' || field.status === 'held';
  const empty = (field: FieldValue) => present(field) && (field.value === '' || (Array.isArray(field.value) && field.value.length === 0) || (isObject(field.value) && Object.keys(field.value).length === 0));
  const value = (name: string, field: FieldValue, schema?: FieldSchema) => field.status === 'not_applicable' ? notApplicableValue(envelope, name, config, inputs, schema, names) : empty(field) ? t('a2.read.empty') : contractFieldValue(type, name, field, schema);
  const body = (name: string, field: FieldValue, schema?: FieldSchema) => present(field) && isCalendarCache(field.value) ? <CalendarReading cache={field.value} /> : value(name, field, schema);
  const extra = (field: FieldValue, schema?: FieldSchema) => <>
    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      <span className="inline-flex items-center gap-1">{t('a2.read.fieldStatus', { status: (FIELD_STATUS[field.status] ?? FIELD_STATUS.unknown).label })}<InlineHelp text={(FIELD_STATUS[field.status] ?? FIELD_STATUS.unknown).hint} /></span>
      <span className="inline-flex items-center gap-1">{t('a2.read.quality', { quality: (QUALITY[field.quality] ?? QUALITY.degraded).label })}<InlineHelp text={t('a2.help.quality')} /></span>
      <span>{t(field.status === 'not_applicable' ? 'a2.read.notApplicable' : present(field) ? 'a2.read.applicable' : 'a2.read.applicabilityUnknown')}</span>
      {empty(field) && <InlineHelp text={t('a2.read.emptyHelp')} />}
    </div>
    {str(asObject(asObject(schema?.legend)[String(field.value)]).description) && <p className="mt-1 text-sm text-muted">{str(asObject(asObject(schema?.legend)[String(field.value)]).description)}</p>}
    {field.attribution?.kind !== common && field.attribution && <p className="text-xs text-muted">{attributionText(field)}</p>}
    {field.value_origin && <p className="flex items-center gap-1 text-xs text-muted">{t('alpha9.valueOrigin.label')} {t(`alpha9.valueOrigin.${field.value_origin.kind}`)}<InlineHelp text={t('a2.help.valueOrigin')} /></p>}
    {!!field.input_origins?.length && <p className="text-xs text-muted">{t('alpha9.valueOrigin.inputs', { kinds: [...new Set(field.input_origins.map(origin => t(`alpha9.valueOrigin.${origin.kind}`)))].join(', ') })}</p>}
    {field.plausibility === 'suspicious' && <p className="text-sm text-warn">{t('alpha9.sensor.suspicious')}</p>}
    {field.valid_until && <p className="text-xs text-muted">{t('alpha9.manual.validUntil')}: {formatTime(field.valid_until)}</p>}
  </>;
  const groups = [...new Set(rows.map(row => str(row.schema?.display_group)))];
  return <>
    {rows.length === 0 && <p className="p-5 text-sm text-muted">{t('b22.output.technical')}</p>}
    {Object.keys(envelope.fields).length >= 4 ? <div className="overflow-x-auto p-5"><table aria-label={t('b22.output.table')} className="w-full text-left text-sm"><thead><tr><th className="p-2">{t('b22.output.field')}</th><th className="p-2">{t('alpha7.contract.value')}</th><th className="p-2">{t('b22.output.since')}</th></tr></thead>
      {groups.map(group => <tbody key={group}>{group && <tr><th scope="rowgroup" colSpan={3} className="border-b border-line p-2 font-medium">{group}</th></tr>}{rows.filter(row => str(row.schema?.display_group) === group).map(row => <tr key={row.name} className="border-b border-line/50 align-top"><th scope="row" className="p-2 font-normal">{contractFieldLabel(type, row.name)}</th><td className="break-words p-2"><div data-testid={`value-${row.name}`}>{body(row.name, row.field, row.schema)}</div>{extra(row.field, row.schema)}</td><td className="p-2">{formatTime(row.field.since_at || row.field.measured_at || envelope.published_at)}</td></tr>)}</tbody>)}
    </table></div> : <div className="flex flex-wrap gap-x-12 gap-y-5 p-6">{rows.map(row => <Fragment key={row.name}><div>{rows.length > 1 && <p className="mb-1 text-sm text-muted">{contractFieldLabel(type, row.name)}</p>}<div className="break-words text-4xl font-semibold tabular-nums" data-testid={`value-${row.name}`}>{body(row.name, row.field, row.schema)}</div>{row.field.since_at && <p className="mt-1 text-sm text-muted">{t('b22.fact.since', { time: formatTime(row.field.since_at) })}</p>}{extra(row.field, row.schema)}</div></Fragment>)}</div>}
    {(attribution || rows.some(row => row.field.attribution)) && <p className="px-6 pb-4 text-xs text-muted" data-testid="contract-attribution">{attributionText(rows[0]?.field)}</p>}
  </>;
}
