import { useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { t } from '@/i18n';
import { useLive } from '@/lib/live-context';
import { formatNumber } from '@/lib/format';
import { loadWait } from '@/lib/load-wait';
import { Button } from './ui/button';
import { Dialog } from './ui/dialog';
import { InlineHelp } from './ui/inline-help';

const operations = '/settings/operations';
const units: Record<string, string> = {
  lag_ms: 'ms',
  queue_percent: '%',
  commit_p95_ms: 'ms',
  recovery_s: 's',
};

export function LoadStatus() {
  const { connection, service } = useLive();
  const load = service?.load;
  if (connection !== 'open' || !load) return null;
  const reason = load.state === 'normal' ? t('a2.load.normalReason')
    : load.state === 'geschützt' && load.candidate && load.candidate !== 'geschützt' ? t('b26.load.metric.recovery_s')
    : load.reasons?.[0] ? t(`b26.load.metric.${load.reasons[0]}`)
    : t(load.state === 'geschützt' ? 'a2.load.stillProtected' : 'a2.load.strainedReason');
  return <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
    <span className="font-semibold text-fg">{t('a2.load.status', { state: t(`b26.load.${load.state}`) })}</span>
    <span>{reason}</span>
    <InlineHelp text={t('a2.help.load')} />
    <Link className="text-accent underline" to={operations}>{t('b26.operations.title')}</Link>
  </div>;
}

export function LoadWaitDialog() {
  const state = useSyncExternalStore(loadWait.subscribe, loadWait.snapshot);
  if (!state) return null;
  const waiting = state.kind === 'waiting';
  return <Dialog
    open
    onOpenChange={open => { if (!open) loadWait.close(); }}
    title={t(waiting ? 'a2.load.waitTitle' : 'a2.load.timeoutTitle')}
    description={t(waiting ? 'a2.load.waitMessage' : 'a2.load.timeoutMessage')}
    footer={<Button variant="secondary" onClick={loadWait.close}>{t(waiting ? 'components.ui.dialog.abbrechen_f7ff11' : 'components.ui.dialog.schlie_en_b808f6')}</Button>}
  >
    {state.kind === 'protected' && <div className="space-y-3 text-sm">
      {state.measurements.map(({ metric, value, threshold }) => <div key={metric}>
        <p>{t(`b26.load.metric.${metric}`)}</p>
        <p className="text-muted">{t('a2.load.measurement', { value: formatNumber(value, 1), threshold: formatNumber(threshold, 1), unit: units[metric] ?? '' })}</p>
      </div>)}
      <Link className="text-accent underline" to={operations} onClick={loadWait.close}>{t('a2.load.openOperations')}</Link>
    </div>}
  </Dialog>;
}
