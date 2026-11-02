import { t } from '@/i18n';
import { Badge } from '@/components/ui/badge';

export interface CatalogMediaMetadata { app_id: string; app_name?: string; live_tv?: boolean; sender?: string }

export function CatalogMediaFacts({ metadata }: { metadata?: CatalogMediaMetadata }) {
  if (!metadata) return null;
  return <div className="flex flex-wrap gap-2 text-sm text-muted">
    <span>{t('b22.apple.app')}: {metadata.app_name || t('b22.apple.unknownApp')}</span>
    {metadata.live_tv && <Badge>{t('b22.apple.live')}</Badge>}
    {metadata.sender && <span>{t('b22.apple.sender')}: {metadata.sender}</span>}
  </div>;
}
