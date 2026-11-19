import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { InlineHelp } from '@/components/ui/inline-help';
import { Notice, Panel, PanelHeader } from '@/components/ui/layout';
import { t } from '@/i18n';
import { asArray, asObject, str } from '@/features/authoring/model';
import { bindingHref, contractHref, ObjectReadLink, sourceHref } from '@/features/authoring/ObjectReadLinks';
import { consumersOfSource } from '@/features/authoring/usage';
import { api, describeError } from '@/lib/api';
import { formatDuration, formatTime } from '@/lib/format';
import { isObject } from '@/lib/json';
import { POLL_MS, useActiveRevision } from '@/lib/queries';
import type { Registry } from '@/lib/types';
import { contractTitle } from '@/features/contracts/presentation';

interface CalendarStatus {
  binding_id: string;
  source_id: string;
  fetched_at: string | null;
  expires_at: string | null;
  error_code: string | null;
  coverage?: { covered: boolean; message: string | null; max_gap_months?: number; gap?: { start: string; end: string } | null };
}

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
      <ObjectReadLink to={sourceHref(row.source_id)}>{t('alpha7.problem.source')}</ObjectReadLink>
    </Notice>)}
  </section>;
}

export function CalendarSourceStatus({ sourceId, config, focus, onEdit }: { sourceId: string; config?: Registry | null; focus?: boolean; onEdit?: () => void }) {
  const status = useCalendarStatus();
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!focus) return;
    section.current?.focus({ preventScroll: true });
    section.current?.scrollIntoView?.({ block: 'start' });
  }, [focus, sourceId]);
  const source = asArray(config?.sources).filter(isObject).find(row => row.source_id === sourceId);
  const bindings = asArray(config?.bindings).filter(isObject).filter(row => row.source_id === sourceId && asObject(row.adapter).kind === 'ha_calendar');
  const consumers = consumersOfSource(config, sourceId).contracts;
  const coverage = status.data?.find(row => row.source_id === sourceId)?.coverage;
  const gap = coverage?.gap;
  const months = (count: number) => t(count === 1 ? 'a2.calendar.month' : 'a2.calendar.months', { count });
  const coverageRows = source?.calendar_coverage_months ? [
    { fact: 'horizon', value: months(Number(source.calendar_coverage_months)) },
    { fact: 'limit', value: months(Number(coverage?.max_gap_months ?? source.calendar_max_gap_months ?? 4)) },
    { fact: 'gap', value: gap ? t('a2.calendar.range', { start: formatTime(gap.start), end: formatTime(gap.end) }) : coverage?.covered ? t('a2.calendar.noGap') : t('a2.calendar.unconfirmed') },
    { fact: 'impact', value: t('a2.calendar.impactText') },
  ] : [];
  return <section ref={section} tabIndex={-1} aria-label={t('a2.calendar.quality')} className="scroll-mt-4 rounded-lg focus-visible:outline-2 focus-visible:outline-accent">
    <Panel>
      <PanelHeader title={t('a2.calendar.quality')} aside={onEdit && <Button size="sm" onClick={onEdit}>{t('a2.calendar.edit')}</Button>} />
      <div className="space-y-3 px-4 pb-4">
        {coverageRows.length !== 0 && <dl className="grid gap-2 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]">{coverageRows.map(row => <CalendarFact key={row.fact} fact={row.fact}>{row.value}</CalendarFact>)}</dl>}
        {bindings.map(binding => {
          const adapter = asObject(binding.adapter);
          const window = asObject(adapter.window);
          const days = Number(window.past_days ?? 0) + Number(window.future_days ?? 0);
          const maxAge = Number(adapter.max_age_s);
          return <div className="space-y-2 border-t border-line pt-3" key={str(binding.binding_id)}>
            <dl className="grid gap-2 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]">
              <CalendarFact fact="window">{t(days === 1 ? 'a2.calendar.day' : 'a2.calendar.days', { count: days })}</CalendarFact>
              <CalendarFact fact="freshness">{Number.isFinite(maxAge) ? formatDuration(maxAge) : t('alpha7.time.unknown')}</CalendarFact>
            </dl>
            <ObjectReadLink to={bindingHref(str(binding.binding_id))}>{t('a2.read.viewBinding')}</ObjectReadLink>
          </div>;
        })}
        {source && <dl className="grid gap-2 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]"><CalendarFact fact="consumers">{consumers.length ? consumers.map(id => <p key={id}><ObjectReadLink to={contractHref(id)}>{contractTitle(asArray(config?.contracts).filter(isObject).find(row => row.contract_id === id), id)}</ObjectReadLink></p>) : t('a2.calendar.noConsumers')}</CalendarFact></dl>}
        {status.error && <Notice tone="bad">{describeError(status.error)}</Notice>}
        {status.isPending && <p>{t('b10.calendarLoading')}</p>}
        {status.data?.filter(row => row.source_id === sourceId).map(row => <div key={row.binding_id} className="space-y-2">
          <dl className="grid gap-2 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]"><dt>{t('b10.calendarFetched')}</dt><dd>{formatTime(row.fetched_at)}</dd><dt>{t('b10.calendarExpires')}</dt><dd>{formatTime(row.expires_at)}</dd></dl>
          {row.error_code && <Notice tone="bad">{t('b10.calendarFailed')} · {row.error_code}</Notice>}
          {row.coverage?.message && <Notice tone="warn">{row.coverage.message}</Notice>}
        </div>)}
      </div>
    </Panel>
  </section>;
}

function CalendarFact({ fact, children }: { fact: string; children: React.ReactNode }) {
  return <><dt className="flex items-center gap-1">{t(`a2.calendar.${fact}`)}<InlineHelp text={t(`a2.calendar.${fact}Help`)} /></dt><dd>{children}</dd></>;
}
