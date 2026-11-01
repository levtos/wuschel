import { hasTranslation, t } from '@/i18n';

export interface NamedCatalog { catalog_id: string; version: number; device_scope?: string | null; display_name?: string }

export function catalogLabel(catalog: NamedCatalog): string {
  if (catalog.display_name) return catalog.display_name;
  const key = `b21.catalog.${catalog.catalog_id}`;
  return hasTranslation(key) ? t(key) : t('b21.catalog.custom');
}

export function catalogValue(kind: string, value: string): string {
  const key = `b21.${kind}.${value}`;
  return hasTranslation(key) ? t(key) : t(`b21.${kind}.other`);
}

export function contextLabel(value: string): string {
  try {
    const parts: unknown = JSON.parse(value);
    if (Array.isArray(parts)) return parts.filter(part => typeof part === 'string' && part).map(part => catalogValue('scope', part)).join(' · ') || t('b21.scope.default');
  } catch { /* Legacy context identifiers have the same display mapping. */ }
  return catalogValue('scope', value);
}
