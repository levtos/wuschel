import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { Notice } from '@/components/ui/layout';
import { t } from '@/i18n';
import { asArray } from '@/features/authoring/model';
import { api, describeError } from '@/lib/api';
import { isObject } from '@/lib/json';
import { POLL_MS, useActiveRevision } from '@/lib/queries';

interface CalendarStatus { binding_id: string; source_id: string; fetched_at: string | null; expires_at: string | null; error_code: string | null; coverage?: { covered: boolean; message: string | null; gap?: { start: string; end: string } | null } }
const time = (value: string | null) => value ? new Date(value).toLocaleString('de-DE') : '–';

function useCalendarStatus(enabled = true) {
  return useQuery({ queryKey: ['sources', 'calendar-status'], queryFn: () => api<CalendarStatus[]>('sources/calendar-status'), enabled, refetchInterval: POLL_MS });
}

export function CalendarCoverageWarnings() {
  const active = useActiveRevision();
  const enabled = asArray(active.data?.config.sources).some(source => isObject(source) && !!source.calendar_coverage_months);
  const status = useCalendarStatus(enabled);
  const gaps = enabled ? status.data?.filter(row => row.coverage?.gap && row.coverage.message) : undefined;
  if (!gaps?.length) return null;
  return <section aria-label={t('b10.calendarStatus')} className="mb-4 space-y-2">
    {gaps.map(row => <Notice key={row.binding_id} tone="warn">
      <p>{row.coverage?.message}</p>
      <Link className="underline" to={`/sources/devices?source_id=${encodeURIComponent(row.source_id)}`}>{t('alpha7.problem.source')}</Link>
    </Notice>)}
  </section>;
}

export function CalendarSourceStatus({ sourceId }: { sourceId: string }) {
  const status = useCalendarStatus();
  return <section aria-label={t('b10.calendarStatus')} className="space-y-2">
    {status.error && <Notice tone="bad">{describeError(status.error)}</Notice>}
    {status.isPending && <p>{t('b10.calendarLoading')}</p>}
    {status.data?.filter(row => row.source_id === sourceId).map(row => <div key={row.binding_id} className="space-y-2">
      <dl><dt>{t('b10.calendarFetched')}</dt><dd>{time(row.fetched_at)}</dd><dt>{t('b10.calendarExpires')}</dt><dd>{time(row.expires_at)}</dd></dl>
      {row.error_code && <Notice tone="bad">{t('b10.calendarFailed')} · {row.error_code}</Notice>}
      {row.coverage?.message && <Notice tone="warn">{row.coverage.message}</Notice>}
    </div>)}
  </section>;
}
