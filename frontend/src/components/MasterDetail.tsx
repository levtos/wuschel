import { t as uiText } from '@/i18n';
import { Plus, Search } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/form';
import { cn } from '@/lib/utils';
import { LayoutPanel, PanelLayout } from './PanelLayout';

export interface ListItem {
  key: string;
  label: string;
  sub?: string;
  badges?: ReactNode;
  errors?: number;
}

/** List on the left, editor on the right; the list scrolls independently. */
export function MasterDetail({
  items,
  selected,
  onSelect,
  onAdd,
  addLabel,
  addDisabled,
  title,
  empty,
  children, search: controlledSearch, onSearchChange,
}: {
  items: ListItem[];
  selected: string | null;
  onSelect: (key: string) => void;
  onAdd?: () => void;
  addLabel?: string;
  addDisabled?: boolean;
  title: string;
  empty: ReactNode;
  children: ReactNode; search?: string; onSearchChange?: (value: string) => void;
}) {
  const [localSearch, setLocalSearch] = useState('');
  const search = controlledSearch ?? localSearch; const setSearch = onSearchChange ?? setLocalSearch;
  const needle = search.trim().toLowerCase();
  const shown = needle ? items.filter((i) => `${i.label} ${i.sub ?? ''}`.toLowerCase().includes(needle)) : items;
  return (
    <PanelLayout view={`collection.${title.toLowerCase()}`}>
      <LayoutPanel id="collection" title={uiText("components.MasterDetail.v0_liste_4816c1", { v0: (title) })} span={1}>
      <aside className="flex min-h-0 flex-col rounded-[var(--radius-lg)] border border-line bg-panel" aria-label={title}>
        <div className="flex items-center gap-2 border-b border-line p-2">
          <div className="relative min-w-0 flex-1">
            <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
            <Input aria-label={uiText("components.MasterDetail.v0_suchen_d98fc3", { v0: (title) })} className="h-8 pl-8 text-xs" placeholder={uiText("components.MasterDetail.suchen_51942c")} value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          {onAdd && (
            <Button size="sm" variant="primary" onClick={onAdd} disabled={addDisabled} aria-label={addLabel ?? uiText("components.MasterDetail.hinzufugen_cbb95c")} title={addLabel}>
              <Plus size={14} />
            </Button>
          )}
        </div>
        <ul className="max-h-[60vh] min-h-0 flex-1 overflow-y-auto p-1.5">
          {shown.map((item) => (
            <li key={item.key}>
              <button
                type="button"
                aria-current={item.key === selected ? 'true' : undefined}
                onClick={() => onSelect(item.key)}
                className={cn('flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-[var(--radius-sm)] px-2.5 py-2 text-left transition-colors', item.key === selected ? 'bg-raised text-fg' : 'hover:bg-raised')}
              >
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="min-w-0 truncate font-mono text-xs font-medium">{item.label || uiText("components.MasterDetail.ohne_id_3527bd")}</span>
                  {item.errors ? <Badge tone="bad">{item.errors}</Badge> : null}
                </span>
                {(item.sub || item.badges) && (
                  <span className="flex w-full flex-wrap items-center gap-1.5 text-[11px] text-muted">
                    {item.sub}
                    {item.badges}
                  </span>
                )}
              </button>
            </li>
          ))}
          {shown.length === 0 && <li className="px-3 py-6 text-center text-xs text-muted">{items.length === 0 ? uiText("components.MasterDetail.noch_keine_eintrage_cce9ab") : uiText("components.MasterDetail.keine_treffer_5fa5cc")}</li>}
        </ul>
      </aside>
      </LayoutPanel>
      <LayoutPanel id="editor" title={uiText("components.MasterDetail.v0_editor_fe0bb3", { v0: (title) })} span={2}>
      <div className="min-w-0 rounded-[var(--radius-lg)] border border-line bg-panel p-5">{selected === null ? empty : children}</div>
      </LayoutPanel>
    </PanelLayout>
  );
}
