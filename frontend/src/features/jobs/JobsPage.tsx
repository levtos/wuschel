import { useMutation } from '@tanstack/react-query';
import { api, describeError } from '@/lib/api';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { PageHeader, Panel } from '@/components/ui/layout';
import { useOperatingState } from './state';

const jobNames: Array<[string, string]> = [
  ['portable/export', 'work.export'],
  ['portable/preview', 'work.importPreview'],
  ['title-import/', 'work.titleImport'],
  ['title-sync/', 'work.titleSync'],
  ['catalogs/classes/', 'work.catalogClasses'],
  ['catalogs/', 'work.catalogCare'],
  ['history/retention/', 'work.retention'],
  ['history/', 'work.history'],
  ['registry/freshness', 'work.freshness'],
  ['registry/', 'work.draft'],
  ['diagnostics/', 'work.diagnostics'],
];

function jobName(path: string): string {
  return t(jobNames.find(([prefix]) => path.startsWith(prefix))?.[1] ?? 'work.job');
}

export default function JobsPage() {
  const query = useOperatingState();
  const work = query.data?.work;
  const cancel = useMutation({
    mutationFn: (id: string) => api(`work/jobs/${id}`, { method: 'DELETE' }),
    onSettled: () => void query.refetch(),
  });
  return (
    <div className="space-y-4">
      <PageHeader title={t('work.title')} description={t('work.description')} actions={
        <Button variant="primary" disabled={query.isFetching} onClick={() => void query.refetch()}>{t('work.refresh')}</Button>
      } />
      {(query.isError || work?.available === false) && <p role="status">{t('work.unavailable')}</p>}
      {cancel.isError && <p role="alert">{describeError(cancel.error)}</p>}
      {work?.available && work.jobs.length === 0 && <p>{t('work.empty')}</p>}
      {work?.jobs.length ? <Panel>
        <ul className="divide-y divide-line">
          {work.jobs.map(job => (
            <li key={job.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <div>
                <p className="font-semibold">{jobName(job.path)}</p>
                <p className="text-sm text-muted">{t(`work.phase.${job.phase}`)} · {new Date(job.created_at).toLocaleString('de-DE')}</p>
                {job.phase === 'failed' && <p className="text-sm">{t('work.jobFailed')}</p>}
              </div>
              {(job.phase === 'waiting' || job.phase === 'running') && (
                <Button variant="secondary" disabled={cancel.isPending || !work.available} onClick={() => cancel.mutate(job.id)}>
                  {t('work.cancel')}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Panel> : null}
    </div>
  );
}
