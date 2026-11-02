import { asObject } from '@/features/authoring/model';
import { isObject } from '@/lib/json';
import type { Envelope, FieldValue, HistoryItem } from '@/lib/types';

export interface HistoryPoint { at: string; field: FieldValue | null; sampleAt?: string; reason?: string }
export interface HistoryInterval { start: number; end: number; point: HistoryPoint; closed: boolean }
export const usable = (field: FieldValue | null) => !!field && ['valid', 'held'].includes(field.status) && field.value !== null;

/** Publications remain ordered by server sequence, including equal-time states.
 * Gap markers precede publications at the same instant: a confirmed state can
 * close the interruption, but no line is drawn across the marker. */
export function historyPoints(rows: HistoryItem[], contractId: string, field: string, revision?: number, gaps: HistoryItem[] = []): HistoryPoint[] {
  const points = rows.filter(row => row.contract_id === contractId && (revision === undefined || row.registry_revision === revision) && typeof row.published_at === 'string' && Number.isFinite(Date.parse(row.published_at)))
    .sort((a, b) => Date.parse(String(a.published_at)) - Date.parse(String(b.published_at)) || Number(a.publication_seq ?? 0) - Number(b.publication_seq ?? 0))
    .map(row => ({ at: String(row.published_at), field: isObject(asObject(row.fields)[field]) ? asObject(row.fields)[field] as unknown as FieldValue : null }));
  const combined: HistoryPoint[] = [...gaps.filter(row => (!row.contract_id || row.contract_id === contractId) && typeof row.at === 'string' && Number.isFinite(Date.parse(row.at))).map(row => ({ at: String(row.at), field: null, reason: typeof row.reason === 'string' ? row.reason : 'history_gap' })), ...points]
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  let cause: string | undefined;
  return combined.map(point => { if (usable(point.field)) { cause = undefined; return point; } cause = point.reason ?? cause; return cause ? { ...point, reason: cause } : point; });
}

export function publicationKey(row: HistoryItem): string {
  const owner = `${row.contract_id}:${row.registry_revision}`;
  return row.epoch_id && Number.isSafeInteger(row.publication_seq) ? `${owner}:${row.epoch_id}:${row.publication_seq}` : `${owner}:${row.published_at}:${row.publication_seq ?? row.id}`;
}
export function mergePublications(rows: HistoryItem[], updates: HistoryItem[]): HistoryItem[] {
  const merged = new Map(rows.map(row => [publicationKey(row), row]));
  for (const row of updates) merged.set(publicationKey(row), row);
  return [...merged.values()].sort((a, b) => Date.parse(String(a.published_at)) - Date.parse(String(b.published_at)) || Number(a.publication_seq ?? 0) - Number(b.publication_seq ?? 0));
}
export function publication(envelope: Envelope): HistoryItem {
  return { ...envelope, id: `${envelope.epoch_id}:${envelope.publication_seq}` } as unknown as HistoryItem;
}

export function windowPoints(points: HistoryPoint[], since?: number, until?: number): HistoryPoint[] {
  const sorted = points.filter(point => Number.isFinite(Date.parse(point.at))).slice().sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  if (since === undefined || until === undefined) return sorted;
  const boundary = sorted.filter(point => Date.parse(point.at) < since).at(-1);
  const visible = sorted.filter(point => Date.parse(point.at) >= since && Date.parse(point.at) <= until);
  const first = boundary ? { ...boundary, sampleAt: boundary.sampleAt ?? boundary.at, at: new Date(since).toISOString() } : { at: new Date(since).toISOString(), field: null, reason: 'no_recording' };
  return visible[0]?.at && Date.parse(visible[0].at) === since ? visible : [first, ...visible];
}

export function historyIntervals(points: HistoryPoint[], until: number): HistoryInterval[] {
  const result: HistoryInterval[] = [];
  const signature = (point: HistoryPoint) => JSON.stringify([point.field?.status, point.field?.value, point.reason, point.field?.reasons]);
  points.forEach((point, index) => {
    const start = Date.parse(point.at); const next = Date.parse(points[index + 1]?.at ?? ''); const end = Number.isFinite(next) ? next : until;
    if (end < start) return;
    const previous = result.at(-1);
    if (previous && previous.end === start && signature(previous.point) === signature(point)) { previous.end = end; previous.closed = index < points.length - 1; }
    else result.push({ start, end, point, closed: index < points.length - 1 });
  });
  return result;
}

/** Equidistant times prefer the earlier instant; at the same instant the latest
 * server publication wins. No small SVG dot has to be hit. */
export function nearestPoint(points: HistoryPoint[], at: number): number {
  let best = -1; let distance = Infinity;
  points.forEach((point, index) => { const next = Math.abs(Date.parse(point.at) - at); if (next < distance || next === distance && point.at === points[best]?.at) { best = index; distance = next; } });
  return best;
}
