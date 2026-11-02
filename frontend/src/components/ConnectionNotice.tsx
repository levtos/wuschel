import { useSyncExternalStore } from 'react';
import { t } from '@/i18n';
import { browserSession } from '@/lib/browser-session';
import { Button } from './ui/button';
import { Notice } from './ui/layout';

export function ConnectionNotice() {
  const session = useSyncExternalStore(browserSession.subscribe, browserSession.getSnapshot);
  return session.blocked ? <Notice tone="warn"><p>{t('b22.connection.expired')}</p><Button onClick={() => location.reload()}>{t('b22.connection.reload')}</Button></Notice> : null;
}
