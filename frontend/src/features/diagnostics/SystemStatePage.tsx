import { useQuery } from '@tanstack/react-query';
import { t, hasTranslation } from '@/i18n';
import { codeLabel } from '@/i18n/codes';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { KeyValue, Notice, PageHeader, Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { api, describeError } from '@/lib/api';
import { formatNumber, formatTime } from '@/lib/format';
import { connectionLabel } from '@/lib/status';
import type { SystemState } from '@/features/admin/operations';
import { ProjectionControl } from '@/features/admin/ProjectionControl';

const measurement = (value: number | null | undefined) => value == null ? t('b26.system.unmeasured') : `${formatNumber(value)} ms`;
const name = (kind: string, key: string) => hasTranslation(`b26.system.${kind}.${key}`) ? t(`b26.system.${kind}.${key}`) : key;

export default function SystemStatePage() {
  const query = useQuery({ queryKey: ['system-state'], queryFn: ({ signal }) => api<SystemState>('system/state', { signal }), refetchInterval: 5000, retry: false });
  const state = query.data;
  return <>
    <PageHeader title={t('b26.system.title')} description={t('b26.system.description')} actions={<Button variant="primary" disabled={query.isFetching} onClick={() => void query.refetch()}>{t('b26.system.refresh')}</Button>} />
    {query.isPending && <Spinner />}
    {query.isError && <Notice tone="bad">{t(state ? 'b26.system.stale' : 'b26.system.unavailable')} {describeError(query.error)}</Notice>}
    {state && <div className="space-y-4">
      <p className="text-sm text-muted">{t('b26.system.updated', { at: formatTime(new Date(query.dataUpdatedAt).toISOString()) })}</p>
      {state.operations_notice && <Notice tone="warn">{t(state.operations_notice === 'operations_missing' ? 'b26.operations.missing' : 'b26.operations.invalid')}</Notice>}
      <Panel><PanelHeader title={t('b26.load.title')} /><div className="space-y-3 p-4">
        <Badge tone={state.load.state === 'normal' ? 'ok' : 'warn'}>{t(`b26.load.${state.load.state}`)}</Badge>
        {state.load.state !== 'normal' && <p>{t(state.load.state === 'geschützt' ? 'b26.load.protectedHelp' : 'b26.load.strainedHelp')}</p>}
        {state.load.reasons.map(reason => <p key={reason} className="text-sm text-muted">{t(`b26.load.metric.${reason}`)}</p>)}
        <ProjectionControl paused={state.projection_paused} disabled={query.isError || query.isFetching} onChanged={() => { void query.refetch(); }} />
      </div></Panel>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel><PanelHeader title={t('b26.system.connections')} /><div className="space-y-3 p-4">
          <Badge tone={state.ready ? 'ok' : 'warn'}>{t(state.ready ? 'b26.system.ready' : 'b26.system.notReady')}</Badge>
          <KeyValue items={[
            { label: t('b26.system.database'), value: connectionLabel(state.persistence) },
            { label: t('b26.system.ha'), value: connectionLabel(state.ha) },
            { label: t('b26.system.traber'), value: connectionLabel(state.bridge) },
            { label: t('b26.system.mqtt'), value: connectionLabel(state.mqtt) },
            { label: t('b26.system.version'), value: state.version },
            { label: t('b26.system.traberVersion'), value: state.traber_version ?? t('b26.system.unknown') },
            { label: t('b26.system.sequence'), value: formatNumber(state.publication_seq) },
            { label: t('b26.system.confirmed'), value: t(state.publication_confirmed ? 'b26.system.yes' : 'b26.system.no') },
          ]} />
          {state.reasons.map(reason => <p key={reason} className="text-sm text-muted">{codeLabel(reason)}</p>)}
        </div></Panel>
        <Panel><PanelHeader title={t('b26.system.timing')} /><div className="space-y-3 p-4"><KeyValue items={[
          { label: t('b26.system.lag'), value: measurement(state.event_loop.lag_ms) },
          { label: t('b26.system.maxLag'), value: measurement(state.event_loop.max_lag_ms) },
          { label: t('b26.system.median'), value: measurement(state.commits.median_ms) },
          { label: t('b26.system.p95'), value: measurement(state.commits.p95_ms) },
          { label: t('b26.system.samples'), value: t('b26.system.ratio', { count: state.commits.count, total: state.commits.window }) },
        ]} /><p className="text-xs text-muted">{t('b26.system.timingHelp')}</p></div></Panel>
        <Panel><PanelHeader title={t('b26.system.queues')} /><div className="space-y-4 p-4">{Object.entries(state.queues).map(([key, queue]) => <div key={key}><h3 className="mb-2 text-sm font-semibold">{name('queue', key)}</h3><KeyValue items={[
          { label: t('b26.system.depth'), value: queue.depth == null ? t('b26.system.unmeasured') : t('b26.system.ratio', { count: queue.depth, total: queue.capacity }) },
          { label: t('b26.system.overflows'), value: formatNumber(queue.overflows) },
        ]} /></div>)}</div></Panel>
        <Panel><PanelHeader title={t('b26.system.startup')} /><div className="space-y-3 p-4"><KeyValue items={[
          { label: t('b26.system.attempts'), value: state.startup_attempts },
          ...Object.entries(state.startup).map(([key, value]) => ({ label: name('phase', key), value: measurement(value) })),
        ]} />{!Object.keys(state.startup).length && <p className="text-sm text-muted">{t('b26.system.unmeasured')}</p>}</div></Panel>
      </div>
      <Panel><PanelHeader title={t('b26.system.retries')} /><div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(state.retries).map(([key, retry]) => <div key={key} className="space-y-2"><h3 className="text-sm font-semibold">{name('retry', key)}</h3><KeyValue items={[
        { label: t('b26.system.total'), value: retry.count },
        { label: t('b26.system.consecutive'), value: retry.consecutive },
        { label: t('b26.system.lastError'), value: retry.last_error ?? t('b26.system.noError') },
        { label: t('b26.system.lastErrorAt'), value: retry.last_error_at ? formatTime(retry.last_error_at) : t('b26.system.noError') },
        { label: t('b26.system.retryStarted'), value: retry.started_at ? formatTime(retry.started_at) : t('b26.system.noError') },
      ]} />{retry.warning && <Notice tone="warn">{t('b26.system.retryWarning')}</Notice>}</div>)}</div></Panel>
    </div>}
  </>;
}
