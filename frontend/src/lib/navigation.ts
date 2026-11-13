import { t } from '@/i18n';

export interface NavigationModule { id: string; label: string; children: { to: string; label: string }[] }
export const NAVIGATION: NavigationModule[] = [
  { id: 'contracts', label: t('alpha7.nav.contracts'), children: [
    { to: '/contracts/list/all', label: t('alpha7.nav.mine') },
    { to: '/contracts/relations/graph', label: t('alpha7.nav.relations') },
    { to: '/templates/own', label: t('alpha7.nav.ownTemplates') },
    { to: '/templates/built-in', label: t('alpha7.nav.builtinTemplates') },
  ] },
  { id: 'catalog-care', label: t('alpha10.catalog.navigation'), children: [{ to: '/catalog-care', label: t('b22.nav.allCatalogs') }] },
  { id: 'sources', label: t('alpha7.nav.sources'), children: [
      { to: '/sources/devices', label: t('alpha7.nav.devices') },
      { to: '/sources/freshness', label: t('b4.title') },
    { to: '/sources/bindings', label: t('alpha7.nav.bindings') },
    { to: '/sources/catalogs', label: t('alpha7.nav.catalogs') },
    { to: '/sources/dependencies', label: t('alpha7.nav.dependencies') },
  ] },
  { id: 'changes', label: t('alpha7.nav.changes'), children: [
    { to: '/changes/drafts', label: t('alpha7.nav.drafts') },
    { to: '/changes/versions', label: t('alpha7.nav.versions') },
    { to: '/import-export', label: t('alpha10.transfer.title') },
  ] },
  { id: 'settings', label: t('alpha7.nav.settings'), children: [
    { to: '/settings/system', label: t('alpha7.nav.system') },
    { to: '/settings/installation', label: t('alpha7.nav.installation') },
    { to: '/settings/connections', label: t('alpha7.nav.connections') },
    { to: '/settings/notifications', label: t('alpha9.notifications.title') },
    { to: '/settings/retention', label: t('alpha9.retention.title') },
    { to: '/settings/operations', label: t('b26.operations.title') },
    { to: '/settings/keys', label: t('alpha7.nav.keys') },
    { to: '/settings/log', label: t('alpha7.nav.log') },
    { to: '/settings/diagnostics', label: t('alpha7.nav.diagnostics') },
    { to: '/settings/diagnostics/system-state', label: t('b26.system.title') },
  ] },
];

/** Keep saved links and validation pointers usable after the module split. */
export function legacyTarget(path: string, search = ''): string {
  const query = new URLSearchParams(search);
  let target = path;
  if (path === '/contracts/import/json' || path === '/changes/transfer') return '/import-export' + search;
  if (['/contracts/list/all', '/contracts/relations/graph'].includes(path)) return path + search;
  if (path === '/contracts' || path === '/contracts/select/contract') target = '/contracts/list/all';
  else if (/^\/contracts\/[^/]+$/.test(path)) target = path.replace('/contracts/', '/contracts/view/');
  else if (path === '/definitions') target = '/templates/own';
  else if (path.startsWith('/definitions/')) target = path.replace('/definitions/', '/templates/details/');
  else if (path === '/sources') target = `/sources/${['bindings', 'catalogs', 'dependencies'].includes(query.get('tab') ?? '') ? query.get('tab') : 'devices'}`;
  else if (path === '/registry') target = query.get('tab') === 'revisions' ? '/changes/versions' : '/changes/drafts';
  else if (path.startsWith('/registry/drafts/')) target = path.replace('/registry/drafts/', '/changes/drafts/');
  else if (path === '/graph') target = '/contracts/relations/graph';
  else if (path === '/diagnostics') target = '/settings/diagnostics';
  else if (path === '/admin') target = '/settings/system';
  return target + search;
}
