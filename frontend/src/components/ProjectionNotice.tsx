import { Link } from 'react-router';
import { t } from '@/i18n';
import { useLive } from '@/lib/live-context';
import { Notice } from './ui/layout';

export function ProjectionNotice() {
  const { service } = useLive();
  return <>
    {service?.projection_paused && <Notice tone="warn"><p>{t('b26.projection.paused')}</p><Link className="underline" to="/settings/diagnostics/system-state">{t('b26.projection.manage')}</Link></Notice>}
    {service?.load?.state === 'geschützt' && <Notice tone="warn">{t('b26.load.protectedHelp')}</Notice>}
  </>;
}
