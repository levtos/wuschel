import { t } from '@/i18n';
import { formatTime } from '@/lib/format';

export function defaultDraftName(date = new Date()): string {
  return t('alpha7.draft.defaultName', { date: formatTime(date.toISOString()) });
}
