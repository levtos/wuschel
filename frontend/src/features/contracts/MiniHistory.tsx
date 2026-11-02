import { useId, useState, type PointerEvent } from 'react';
import { hasTranslation, t } from '@/i18n';
import { reasonLabel } from '@/i18n/codes';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import { formatDuration, formatTime } from '@/lib/format';
import { displayValue, FIELD_STATUS } from '@/lib/status';
import type { FieldValue } from '@/lib/types';
import { contractFieldLabel, contractFieldValue } from './presentation';
import { historyIntervals, nearestPoint, usable, windowPoints, type HistoryInterval, type HistoryPoint } from './historyModel';

const clock = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const weekClock = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
export function historyTime(at: string, week: boolean) { return (week ? weekClock : clock).format(new Date(at)); }
export function gapReason(point: HistoryPoint): string {
  const reasons = point.field?.reasons?.map(reason => reasonLabel(reason.code)) ?? [];
  if (point.reason) reasons.unshift(hasTranslation(`b22.history.reason.${point.reason}`) ? t(`b22.history.reason.${point.reason}`) : reasonLabel(point.reason));
  return reasons.length ? [...new Set(reasons)].join(' · ') : t('b22.history.reason.unknown');
}

/** Interaction is on the entire plot, with a keyboard/touch equivalent. Native
 * SVG titles are not needed to discover times, values, or unknown intervals. */
export function MiniHistory({ points: input, schema, name, current, now = Date.now(), since, until, contractTypeId, stale = false }: {
  points: HistoryPoint[]; schema?: FieldSchema; name: string; current?: FieldValue; now?: number; since?: number; until?: number; contractTypeId?: string; stale?: boolean;
}) {
  const tooltipId = useId(); const hatch = useId().replace(/:/g, '');
  const [selection, setSelection] = useState<{ index: number; x: number; y: number; axis?: string } | null>(null);
  const points = windowPoints(input, since, until);
  const lastKnown = points.at(-1);
  if (stale && lastKnown && usable(lastKnown.field) && (until ?? now) > Date.parse(lastKnown.at)) points.push({ at: lastKnown.at, field: null, reason: 'connection_not_confirmed' });
  const tail = points.at(-1);
  const extend = current?.status === 'valid' && typeof current.value === 'boolean' && tail?.field?.status === 'valid' && tail.field.value === current.value && Date.parse(tail.at) < now;
  const drawn = extend ? [...points, { at: new Date(now).toISOString(), field: current }] : points;
  const first = since ?? Date.parse(drawn[0]?.at ?? ''); const last = until ?? Math.max(Date.parse(drawn.at(-1)?.at ?? ''), current ? now : -Infinity);
  const week = last - first > 24 * 3_600_000;
  const categories = schema?.type === 'timestamp' ? [] : schema?.type === 'enum' ? asArray(schema.value_catalog).filter((value): value is string => typeof value === 'string') : [...new Set(points.map(point => point.field?.value).filter((value): value is string => typeof value === 'string'))].sort();
  const boolean = schema?.type === 'bool' || points.some(point => typeof point.field?.value === 'boolean');
  const state = boolean || categories.length > 0;
  const ordinate = (field: FieldValue | null) => !usable(field) ? null : typeof field!.value === 'number' && Number.isFinite(field!.value) ? field!.value : typeof field!.value === 'boolean' ? Number(field!.value) : typeof field!.value === 'string' && categories.includes(field!.value) ? categories.indexOf(field!.value) : null;
  const values = points.map(point => ordinate(point.field)).filter((value): value is number => value !== null);
  const limits = values.reduce<[number, number]>(([min, max], value) => [Math.min(min, value), Math.max(max, value)], [Infinity, -Infinity]);
  const low = boolean || categories.length || !values.length ? 0 : limits[0]!; const high = boolean ? 1 : categories.length ? categories.length - 1 : !values.length ? 1 : limits[1] === low ? low + 1 : limits[1]!; const range = high - low || 1;
  const height = Math.max(100, (categories.length - 1) * 28); const gapY = height + 66;
  const xFor = (at: number) => 110 + (at - first) / (last - first || 1) * 870;
  const yFor = (value: number) => categories.length ? 32 + value / range * height : height + 32 - (value - low) / range * height;
  const formatted = (field: FieldValue) => schema?.type === 'enum' && usable(field) && str(asObject(asObject(schema.legend)[String(field.value)]).display_name) || contractFieldValue(contractTypeId, name, field, schema);
  const label = (value: number) => boolean ? t(value ? 'lib.status.ja_cde9e5' : 'lib.status.nein_90ebc1') : categories.length ? formatted({ status: 'valid', value: categories[value]!, quality: 'healthy', evidence: [], reasons: [] }) : displayValue({ status: 'valid', value, quality: 'healthy', evidence: [], reasons: [] }, typeof schema?.unit === 'string' ? schema.unit : undefined);
  const intervals = historyIntervals(points, last);
  const selected = selection ? points[Math.min(selection.index, points.length - 1)] : undefined;
  const interval = selected ? intervals.find(part => Date.parse(selected.at) >= part.start && Date.parse(selected.at) < part.end) ?? intervals.find(part => part.start === Date.parse(selected.at)) : undefined;
  const segments: { x: number; y: number }[][] = [[]];
  for (const point of drawn) {
    const value = ordinate(point.field);
    if (value === null) { if (segments.at(-1)!.length) segments.push([]); continue; }
    segments.at(-1)!.push({ x: xFor(Date.parse(point.at)), y: yFor(value) });
  }
  const path = (segment: { x: number; y: number }[]) => segment.flatMap((point, index) => state && index > 0 ? [`${point.x},${segment[index - 1]!.y}`, `${point.x},${point.y}`] : [`${point.x},${point.y}`]).join(' ');
  const pointText = (point: HistoryPoint) => point.field ? formatted(point.field) : t('b22.history.gap');
  const locate = (event: PointerEvent<SVGSVGElement>) => {
    const axis = (event.target as Element).closest?.('[data-axis-label]')?.getAttribute('data-axis-label');
    if (axis) { setSelection({ index: 0, x: event.clientX, y: event.clientY, axis }); return; }
    const rect = event.currentTarget.getBoundingClientRect();
    const at = first + Math.max(0, Math.min(1, ((event.clientX - rect.left) / (rect.width || 1000) * 1000 - 110) / 870)) * (last - first);
    const gap = intervals.find(part => ordinate(part.point.field) === null && at >= part.start && at < part.end);
    const index = gap ? points.indexOf(gap.point) : nearestPoint(points, at);
    if (index >= 0) setSelection({ index, x: event.clientX, y: event.clientY });
  };
  const spanText = (part: HistoryInterval) => t('b22.history.interval', { start: historyTime(part.point.sampleAt ?? part.point.at, week), end: historyTime(new Date(part.end).toISOString(), week) });
  const timestamp = schema?.type === 'timestamp';
  return <div className="relative space-y-2">
    <h3 className="text-sm font-medium">{contractFieldLabel(contractTypeId, name)}</h3>
    {timestamp ? <ol aria-label={t('b22.history.timeline', { name: contractFieldLabel(contractTypeId, name) })} className="space-y-2 border-l border-line pl-4 text-sm">
      {intervals.map((part, index) => <li key={index} className="break-words"><time dateTime={part.point.at}>{formatTime(part.point.sampleAt ?? part.point.at)}</time><p>{pointText(part.point)}</p>{ordinate(part.point.field) === null && !usable(part.point.field) && <p>{gapReason(part.point)}</p>}<p className="text-xs text-muted">{spanText(part)}</p></li>)}
    </ol> : drawn.length > 0 ? <svg viewBox={`0 0 1000 ${height + 115}`} role="img" tabIndex={0} aria-label={t('alpha7.contract.chart', { name: contractFieldLabel(contractTypeId, name) })} aria-describedby={selection ? tooltipId : undefined} className="block h-auto w-full touch-pan-y text-muted"
      onPointerMove={locate} onPointerDown={locate} onPointerLeave={event => { if (event.pointerType !== 'touch') setSelection(null); }} onBlur={() => setSelection(null)} onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const index = event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : Math.max(0, Math.min(points.length - 1, (selection?.index ?? (event.key === 'ArrowRight' ? -1 : points.length)) + (event.key === 'ArrowRight' ? 1 : -1)));
        const rect = event.currentTarget.getBoundingClientRect(); setSelection({ index, x: rect.left + xFor(Date.parse(points[index]!.at)) / 1000 * rect.width, y: rect.top + 40 });
      }}>
      <defs><pattern id={hatch} width="8" height="8" patternUnits="userSpaceOnUse"><path d="M-2 2L2-2M0 8L8 0M6 10L10 6" stroke="var(--color-warn)" strokeWidth="2" /></pattern></defs>
      <path d={`M110 24V${gapY + 14}H980`} fill="none" stroke="currentColor" opacity="0.5" />
      {(categories.length ? categories.map((_, index) => index) : boolean ? [0, 1] : values.length ? [low, (low + high) / 2, high] : []).map(value => <g key={value}><text x="98" y={yFor(value) + 4} textAnchor="end" fontSize="13" fill="currentColor" textLength={Math.min(94, label(value).length * 7)} lengthAdjust="spacingAndGlyphs" data-axis-label={label(value)} aria-label={label(value)}>{label(value).length > 12 ? t('b22.history.shortLabel', { value: label(value).slice(0, 11) }) : label(value)}</text><path d={`M110 ${yFor(value)}H980`} fill="none" stroke="currentColor" opacity="0.15" /></g>)}
      <text x="98" y={gapY + 4} textAnchor="end" fontSize="11" textLength="94" lengthAdjust="spacingAndGlyphs" fill="currentColor">{t('b22.history.gap')}</text>
      {intervals.map((part, index) => { const value = ordinate(part.point.field); return value === null ? <rect key={index} data-gap="true" x={xFor(part.start)} y={gapY - 10} width={Math.max(0, xFor(part.end) - xFor(part.start))} height="20" fill={`url(#${hatch})`} stroke="var(--color-warn)" /> : state ? <rect key={index} data-state-band="true" x={xFor(part.start)} y={yFor(value) - 9} width={Math.max(0, xFor(part.end) - xFor(part.start))} height="18" fill={boolean ? value ? 'var(--color-ok)' : 'var(--color-info)' : ['var(--color-accent)', 'var(--color-info)', 'var(--color-ok)', 'var(--color-warn)'][value % 4]} opacity="0.25" /> : null; })}
      {segments.filter(segment => segment.length).map((segment, index) => <g key={index}><polyline data-interpolation={state ? 'step' : 'linear'} points={path(segment)} fill="none" stroke="currentColor" strokeWidth="2" />{segment.map((point, n) => <circle key={n} cx={point.x} cy={point.y} r="3" fill="currentColor" />)}</g>)}
      {selected && !selection?.axis && <path data-cursor="true" d={`M${xFor(Date.parse(selected.at))} 24V${gapY + 14}`} stroke="var(--color-accent)" strokeWidth="1.5" />}
    </svg> : <p className="text-sm text-muted">{t('alpha7.contract.noHistoryValue')}</p>}
    {selection && selected && <div id={tooltipId} role="tooltip" className="pointer-events-none fixed z-50 w-72 max-w-[calc(100vw-1rem)] rounded-lg border border-line-strong bg-panel p-3 text-sm shadow-xl" style={{ left: Math.max(8, Math.min(selection.x + 12, globalThis.innerWidth - 300)), top: Math.max(8, Math.min(selection.y + 12, globalThis.innerHeight - 190)) }}>
      {selection.axis ? <p>{selection.axis}</p> : <><p>{historyTime(selected.sampleAt ?? selected.at, week)}</p><p className="font-medium">{pointText(selected)}</p><p>{selected.field ? FIELD_STATUS[selected.field.status].label : t('b22.history.gap')}</p>
      {interval && (state || !usable(selected.field)) && <><p>{spanText(interval)}</p><p>{t('b22.history.duration', { value: formatDuration((interval.end - interval.start) / 1000) })}</p>{!interval.closed && <p>{t('b22.history.openEnd')}</p>}</>}
      {!usable(selected.field) && <><p>{gapReason(selected)}</p>{selected.reason && ['process_restart', 'ha_disconnect', 'db_outage', 'mqtt_disconnect'].includes(selected.reason) && <p>{t('b22.history.markerOnly')}</p>}</>}</>}
    </div>}
    {drawn.length > 0 && Number.isFinite(first) && Number.isFinite(last) && <div className="flex justify-between gap-4 text-xs text-muted"><time dateTime={new Date(first).toISOString()}>{formatTime(new Date(first).toISOString())}</time><time dateTime={new Date(last).toISOString()}>{formatTime(new Date(last).toISOString())}</time></div>}
    <details className="text-xs"><summary className="cursor-pointer text-muted">{t('alpha7.contract.historyTable')}</summary><table className="mt-2 w-full text-left"><caption className="sr-only">{t('alpha7.contract.historyTable')}</caption><thead><tr><th>{t('alpha7.contract.time')}</th><th>{t('alpha7.contract.value')}</th></tr></thead><tbody>{points.slice(-8).reverse().map((point, index) => <tr key={index}><td className="py-1">{formatTime(point.sampleAt ?? point.at)}</td><td>{pointText(point)}</td></tr>)}</tbody></table></details>
  </div>;
}
