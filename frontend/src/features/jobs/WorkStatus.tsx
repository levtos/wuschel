import { Link } from 'react-router';
import { t } from '@/i18n';
import { useOperatingState } from './state';

export function WorkStatus() {
  const query = useOperatingState();
  const data = query.isError ? undefined : query.data;
  const count = data?.work?.jobs.filter(job => job.phase === 'waiting' || job.phase === 'running').length ?? 0;
  return (
    <div className="flex flex-wrap items-center gap-x-3 text-xs">
      {data?.truth_delayed && <span role="status" className="text-warning">{t('work.truthDelayed')}</span>}
      <Link to="/jobs" className="text-accent underline">
        {count ? t('work.activeCount', { count }) : t('work.title')}
      </Link>
    </div>
  );
}
