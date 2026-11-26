import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { t } from '@/i18n';
import { Spinner } from '@/components/ui/layout';
import { formatTime, relativeTime } from '@/lib/format';
import { useLive } from '@/lib/live-context';
import { POLL_MS, useActiveRevision, useHistory } from '@/lib/queries';
import { eventText, overviewEvents } from './overviewModel';

export function EventFeed({ limit = 8, logLink = true, includeActivations = false }: { limit?: number; logLink?: boolean; includeActivations?: boolean }) {
  const active = useActiveRevision();
  const live = useLive();
  const activations = useHistory('activations', { limit: 50 }, includeActivations, POLL_MS);
  const history = useHistory('contracts', { limit: 200 }, true, POLL_MS);
  const observations = useHistory('observations', { limit: 100, compact: true }, true, POLL_MS);
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer); }, []);
  const events = overviewEvents(
    includeActivations ? activations.data?.pages.flatMap(page => page.items) ?? [] : [],
    history.data?.pages.flatMap(page => page.items) ?? [],
    live.connectionEvents,
    observations.data?.pages.flatMap(page => page.items) ?? [],
  ).slice(0, limit);
  const pending = history.isPending && observations.isPending && (!includeActivations || activations.isPending);
  return <div className="space-y-3">
    {((includeActivations && activations.isError) || history.isError || observations.isError) && <p role="alert" className="text-sm text-bad">{t('alpha7.overview.eventsUnavailable')}</p>}
    {pending ? <Spinner /> : events.length === 0 ? <p className="text-sm text-muted">{t('alpha7.overview.noEvents')}</p> : <ol className="divide-y divide-line">
      {events.map(event => <li key={event.id} className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-3 text-sm">
        <span>{eventText(event, active.data?.config)}{event.session && <span className="ml-2 text-xs text-faint">{t('alpha7.overview.thisSession')}</span>}</span>
        <time dateTime={event.at} title={formatTime(event.at)} className="shrink-0 text-xs text-muted">{relativeTime(event.at, now)}</time>
      </li>)}
    </ol>}
    <p className="text-xs text-faint">{t('alpha7.overview.connectionScope')}</p>
    {logLink && <Link to="/settings/log" className="inline-block text-sm text-fg underline underline-offset-4">{t('alpha7.overview.allLog')}</Link>}
  </div>;
}
