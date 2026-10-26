import { useQuery } from '@tanstack/react-query';
import { Notice } from '@/components/ui/layout';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { POLL_MS } from '@/lib/queries';

interface CalendarStatus { binding_id: string; source_id: string; fetched_at: string | null; expires_at: string | null; error_code: string | null }
const time = (value: string | null) => value ? new Date(value).toLocaleString('de-DE') : '–';

export function CalendarSourceStatus({ sourceId }: { sourceId: string }) {
  const status = useQuery({ queryKey: ['sources', 'calendar-status'], queryFn: () => api<CalendarStatus[]>('sources/calendar-status'), refetchInterval: POLL_MS });
  return <section aria-label={t('b10.calendarStatus')} className="space-y-2">
    {status.error && <Notice tone="bad">{describeError(status.error)}</Notice>}
    {status.isPending && <p>{t('b10.calendarLoading')}</p>}
    {status.data?.filter(row => row.source_id === sourceId).map(row => <div key={row.binding_id} className="space-y-2">
      <dl><dt>{t('b10.calendarFetched')}</dt><dd>{time(row.fetched_at)}</dd><dt>{t('b10.calendarExpires')}</dt><dd>{time(row.expires_at)}</dd></dl>
      {row.error_code && <Notice tone="bad">{t('b10.calendarFailed')} · {row.error_code}</Notice>}
    </div>)}
  </section>;
}
