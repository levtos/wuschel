import { t as uiText } from '@/i18n';
import {
  Activity,
  Cable,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  FileCode2,
  FlaskConical,
  LayoutDashboard,
  Settings2,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { NAVIGATION } from '@/lib/navigation';
import { LayoutPreferencesProvider, useCollapsedNavigation } from './PanelLayout';
import { Link, Outlet, useLocation } from 'react-router';
import { ReadinessBadge } from '@/components/ReadinessBadge';
import { Button } from '@/components/ui/button';
import { DraftBar } from '@/features/authoring/DraftBar';
import { useLive } from '@/lib/live-context';
import { useActiveRevision, useInfo } from '@/lib/queries';
import { classifySystem } from '@/lib/status';
import { cn } from '@/lib/utils';
import { IssueFocus } from './IssueFocus';
import { HerdIcon } from './Herd';
import { ConnectionNotice } from './ConnectionNotice';
import { RouteScrollRestoration } from './RouteScrollRestoration';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useEditorial } from '@/features/editorial/Editorial';
import { CATEGORIES, categoryKeys } from '@/features/editorial/model';
import { asArray, str } from '@/features/authoring/model';
import { api } from '@/lib/api';
import { useQuery } from '@tanstack/react-query';
import { isObject } from '@/lib/json';
import { catalogLabel, type NamedCatalog } from '@/features/catalog/catalogLabels';

const MODULE_ICONS = { contracts: FileCode2, 'catalog-care': FileCode2, sources: Cable, changes: FlaskConical, settings: Settings2, diagnosis: Activity };

/** Top-bar chips: readiness and connection are separate facts. */
export function StatusChips({ extra }: { extra?: ReactNode }) {
  const live = useLive();
  const active = useActiveRevision();
  const info = useInfo();
  const conditions = classifySystem({
    service: live.service,
    connection: live.connection,
    hasActiveRegistry: active.data === undefined ? undefined : active.data !== null,
    apiFailure: info.isError ? 'network' : null,
  });
  return <ReadinessBadge conditions={conditions} ready={live.service?.ready === true && live.connection === 'open'} revision={active.data?.revision}>{extra}</ReadinessBadge>;
}

export function Shell(_props: { onSetup: () => void }) {
  const info = useInfo();
  const [collapsed, setCollapsed] = useCollapsedNavigation(info.data?.installation_id ?? null);
  const { pathname, search } = useLocation();
  const moduleId = pathname.startsWith('/templates') ? 'contracts' : pathname === '/import-export' ? 'changes' : pathname.split('/')[1] ?? '';
  const source = useAuthoringSource(); const editorial = useEditorial();
  const catalogs = useQuery({ queryKey: ['managed-catalogs'], queryFn: () => api<NamedCatalog[]>('catalogs') });
  const contracts = asArray(source.config?.contracts).filter(isObject);
  const categories = CATEGORIES.map(category => ({ to: `/contracts/list/all?category=${category}`, label: uiText('b22.nav.categoryCount', { name: uiText(`alpha9.category.${categoryKeys[category]}`), count: editorial.isPending || source.loading ? '…' : contracts.filter(row => editorial.item({ kind: 'contract', id: str(row.contract_id) }).category === category).length }) }));
  const navigation = NAVIGATION.map(module => module.id === 'contracts' ? { ...module, children: [module.children[0]!, ...categories, ...module.children.slice(1)] } : module.id === 'catalog-care' ? { ...module, children: [...module.children, ...(catalogs.data ?? []).map(catalog => ({ to: `/catalog-care?catalog=${encodeURIComponent(`${catalog.catalog_id}@${catalog.version}`)}`, label: catalogLabel(catalog) }))] } : module);
  const [expanded, setExpanded] = useState<string[]>([]);
  useEffect(() => { if (moduleId) setExpanded(previous => previous.includes(moduleId) ? previous : [...previous, moduleId]); }, [moduleId]);

  return (
    <div className="flex h-full min-h-0">
      <aside
        className={cn(
          'flex shrink-0 flex-col border-r border-line bg-panel transition-[width] duration-150',
          collapsed ? 'w-14' : 'w-64',
        )}
      >
        <div className="flex h-14 items-center gap-2.5 border-b border-line px-3.5">
          <HerdIcon />
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <p className="truncate text-sm font-semibold">{uiText("components.Shell.terra_workbench_7ad523")}</p>
              <p className="truncate text-[11px] text-faint">{info.data?.installation_label || uiText("components.Shell.core_contracts_57c8c1")}</p>
            </div>
          )}
        </div>
        <nav aria-label={uiText("components.Shell.hauptnavigation_e8fc66")} className="flex-1 space-y-0.5 overflow-y-auto p-2">
          <Link to="/" aria-current={pathname === '/' ? 'page' : undefined} title={collapsed ? uiText('alpha7.nav.overview') : undefined} className={cn('flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm', pathname === '/' ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-raised', collapsed && 'justify-center px-0')}>
            <LayoutDashboard size={17} /><span className={collapsed ? 'sr-only' : ''}>{uiText('alpha7.nav.overview')}</span>
          </Link>
          {navigation.map(module => {
            const Icon = MODULE_ICONS[module.id as keyof typeof MODULE_ICONS];
            const open = expanded.includes(module.id);
            return <div key={module.id}>
              <button type="button" aria-current={moduleId === module.id ? 'true' : undefined} aria-expanded={!collapsed && open} aria-controls={`nav-${module.id}`} title={collapsed ? module.label : undefined} className={cn('flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm font-medium hover:bg-raised', moduleId === module.id ? 'text-accent' : 'text-muted', collapsed && 'justify-center px-0')} onClick={() => {
                if (collapsed) { setCollapsed(false); setExpanded(previous => previous.includes(module.id) ? previous : [...previous, module.id]); }
                else setExpanded(previous => open ? previous.filter(id => id !== module.id) : [...previous, module.id]);
              }}>
                <Icon size={17} /><span className={cn('flex-1', collapsed && 'sr-only')}>{module.label}</span>{!collapsed && <ChevronDown size={14} className={open ? 'rotate-180' : ''} />}
              </button>
              {!collapsed && open && <ul id={`nav-${module.id}`} className="mb-2 ml-5 border-l border-line pl-2">{module.children.map(item => {
                const [itemPath, itemSearch = ''] = item.to.split('?');
                const expected = new URLSearchParams(itemSearch); const current = new URLSearchParams(search);
                const selected = pathname === itemPath && [...expected].every(([key, value]) => current.get(key) === value) && (expected.size > 0 || !current.get(itemPath === '/catalog-care' ? 'catalog' : 'category')) || (item.to === '/contracts/list/all' && pathname.startsWith('/contracts/view/')) || (item.to === '/templates/own' && pathname.startsWith('/templates/details/'));
                return <li key={item.to}><Link to={item.to} aria-current={selected ? 'page' : undefined} className={cn('block rounded-md px-3 py-2 text-sm leading-snug', selected ? 'bg-accent/10 font-medium text-accent' : 'text-muted hover:bg-raised hover:text-fg')}>{item.label}</Link></li>;
              })}</ul>}
            </div>;
          })}
        </nav>
        <div className="flex items-center justify-between gap-1 border-t border-line p-2">
          <Button variant="ghost" size="icon" aria-expanded={!collapsed} aria-label={collapsed ? uiText("components.Shell.navigation_ausklappen_9adc95") : uiText("components.Shell.navigation_einklappen_6b094c")} onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          </Button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line bg-panel/70 px-5 py-2 backdrop-blur">
          <StatusChips />
          <DraftBar />
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto px-5 py-5 md:px-8">
          <ConnectionNotice />
          <RouteScrollRestoration />
          <div id="route-content"><LayoutPreferencesProvider value={info.data?.installation_id ?? null}><Outlet /></LayoutPreferencesProvider></div>
          <IssueFocus />
        </main>
      </div>
    </div>
  );
}
