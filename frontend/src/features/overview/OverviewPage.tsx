import { Link, useNavigate } from 'react-router';
import { Plus, Upload } from 'lucide-react';
import { t } from '@/i18n';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Button } from '@/components/ui/button';
import { PageHeader, Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { asArray, str } from '@/features/authoring/model';
import { ObjectReadLink } from '@/features/authoring/ObjectReadLinks';
import { CalendarCoverageWarnings } from '@/features/sources/CalendarSourceStatus';
import { isObject } from '@/lib/json';
import { useLive } from '@/lib/live-context';
import { useActiveRevision, useProblems } from '@/lib/queries';
import type { Reason } from '@/lib/types';
import { EventFeed } from './EventFeed';
import { contractCounts, contractName, problemAdvice } from './overviewModel';

export function OverviewPage() {
  const live = useLive();
  const active = useActiveRevision();
  const problems = useProblems();
  const doc = useWorkingDocument();
  const navigate = useNavigate();
  const config = active.data?.config;
  const current = !live.stale && live.registryRevision === (active.data?.revision ?? 0) ? live.contracts : null;
  const counts = contractCounts(config, current);
  const grouped = new Map<string, Reason[]>();
  for (const problem of problems.data?.problems ?? []) grouped.set(problem.contract_id, [...(grouped.get(problem.contract_id) ?? []), ...problem.reasons]);
  const enabledIds = new Set(asArray(config?.contracts).filter(isObject).filter(contract => contract.enabled !== false).map(contract => str(contract.contract_id)));
  for (const envelope of Object.values(current ?? {})) {
    if (!enabledIds.has(envelope.contract_id)) continue;
    // Counts and problem details must describe the same current server publication.
    if (envelope.contract_health === 'degraded') grouped.set(envelope.contract_id, Object.values(envelope.fields).flatMap(field => field.reasons));
    else grouped.delete(envelope.contract_id);
  }
  const entries = [...grouped].filter(([id]) => enabledIds.has(id));
  const incomplete = Object.values(current ?? {}).filter(envelope => enabledIds.has(envelope.contract_id)).flatMap(envelope => Object.entries(envelope.fields).filter(([, field]) => field.configuration?.complete === false).map(([name, field]) => ({ envelope, name, field })));
  const signalCount = current !== null || (active.isSuccess && problems.isSuccess) ? entries.length : '–';
  const incompleteCount = current === null ? '–' : incomplete.length;
  const metrics = [
    { key: 'active', filter: 'active', color: 'text-fg' }, { key: 'healthy', filter: 'healthy', color: 'text-ok' },
    { key: 'degraded', filter: 'degraded', color: 'text-warn' }, { key: 'off', filter: 'off', color: 'text-muted' },
  ] as const;
  return <>
    <PageHeader title={t('alpha7.nav.overview')} description={t('alpha9.pages.overview')} actions={<Button variant="primary" onClick={() => navigate('/contracts/list/all?new=1')}><Plus size={16} />{t('alpha7.overview.newContract')}</Button>} />
    <CalendarCoverageWarnings />
    {!live.stale && live.service?.ha_disconnected_since && <p role="status" className="mb-4 rounded-xl border border-warn/40 p-4 text-sm text-warn">{t(live.service.ha_unbounded_values === 1 ? 'alpha9.ha.disconnectedOne' : 'alpha8.ha.disconnected', { since: new Date(live.service.ha_disconnected_since).toLocaleString('de-DE', { timeZone: 'Europe/Berlin' }), count: live.service.ha_unbounded_values ?? 0 })}</p>}
    <PanelLayout view="overview.alpha7">
      <LayoutPanel id="actions" title={t('alpha7.overview.actions')}><Panel>
        <PanelHeader title={t('alpha7.overview.actions')} />
        <div className="flex flex-wrap gap-3 p-5">
          <Button onClick={() => navigate('/import-export')}><Upload size={16} />{t('alpha10.transfer.title')}</Button>
        </div>
      </Panel></LayoutPanel>
      <LayoutPanel id="contracts" title={t('alpha7.nav.contracts')}><Panel>
        <PanelHeader title={t('alpha7.nav.contracts')} description={t('alpha7.overview.activeOnly')} />
        <div className="space-y-5 p-5">
          {active.isPending ? <Spinner /> : active.isError ? <p role="alert" className="text-sm text-bad">{t('alpha7.overview.contractsUnavailable')}</p> : <div className="grid gap-3 sm:grid-cols-4">
            {metrics.map(metric => <Link key={metric.key} to={`/contracts/list/all?filter=${metric.filter}`} onClick={() => doc.setEditing(false)} className="rounded-xl border border-line px-4 py-3 text-sm hover:border-faint focus-visible:outline-2 focus-visible:outline-accent">
              <span className={`block text-2xl font-semibold tabular-nums ${counts[metric.key] === null ? 'text-muted' : metric.color}`}>{counts[metric.key] ?? '–'}</span>{' '}<span className="text-muted">{t(`alpha7.overview.${metric.key}`)}</span>
            </Link>)}
          </div>}
          {current === null && <p className="text-xs text-muted">{t('alpha7.overview.waitingValues')}</p>}
          <h3 className="font-medium">{t('a2.overview.signalCount', { count: signalCount })}</h3>
          {signalCount === '–' && (active.isPending || problems.isPending) ? <Spinner /> : signalCount === '–' ? <p role="alert" className="text-sm text-bad">{t('alpha7.overview.problemsUnavailable')}</p> : entries.length === 0 ? <p className="text-sm font-medium text-ok">{t('a2.overview.allGood')}</p> : <ul className="divide-y divide-line">
            {entries.slice(0, 8).map(([id, reasons]) => {
              const advice = problemAdvice(reasons, config, id);
              return <li key={id} className="py-4 first:pt-0">
                <Link to={`/contracts/view/${encodeURIComponent(id)}`} onClick={() => doc.setEditing(false)} className="font-medium hover:text-fg">{contractName(config, id)}</Link>
                <p className="mt-1 text-sm text-muted">{advice.text}</p>
                <details className="mt-2 text-sm"><summary className="cursor-pointer text-fg">{t('alpha7.overview.whatCanIDo')}</summary><div className="mt-2 space-y-2 border-l border-line pl-3"><p>{advice.advice}</p><ObjectReadLink to={advice.to} className="inline-block text-fg underline underline-offset-4">{advice.action}</ObjectReadLink></div></details>
              </li>;
            })}
          </ul>}
          {entries.length > 8 && <Link to="/contracts/list/all?filter=degraded" className="text-sm text-fg underline">{t('alpha7.overview.moreProblems', { count: entries.length - 8 })}</Link>}
          <h3 className="font-medium">{t('a2.overview.incompleteCount', { count: incompleteCount })}</h3>
          {current === null ? <p className="text-sm text-muted">{t('alpha7.overview.waitingValues')}</p> : incomplete.length === 0 ? <p className="text-sm font-medium text-ok">{t('a2.overview.allGood')}</p> : <ul className="space-y-3">{incomplete.map(({ envelope, name, field }) => <li key={`${envelope.contract_id}:${name}`} className="rounded-xl border border-line p-3 text-sm"><Link to={`/contracts/view/${encodeURIComponent(envelope.contract_id)}`} className="font-medium underline">{contractName(config, envelope.contract_id)}</Link><p>{field.configuration?.hints.map(hint => hint.message || t('alpha8.configuration.unassigned')).join(' · ')}</p><p className="text-muted">{t('alpha8.configuration.validHint')}</p></li>)}</ul>}
        </div>
      </Panel></LayoutPanel>
      <LayoutPanel id="events" title={t('alpha7.overview.events')}><Panel>
        <PanelHeader title={t('alpha7.overview.events')} /><div className="p-5"><EventFeed /></div>
      </Panel></LayoutPanel>
    </PanelLayout>
  </>;
}
