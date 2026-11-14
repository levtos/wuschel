import { Checkbox as BaseCheckbox } from '@base-ui/react/checkbox';
import { Collapsible as BaseCollapsible } from '@base-ui/react/collapsible';
import { Switch as BaseSwitch } from '@base-ui/react/switch';
import { Tabs as BaseTabs } from '@base-ui/react/tabs';
import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import { Check, ChevronRight } from 'lucide-react';
import { useId, useState, type ReactElement, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function Switch({ checked, onCheckedChange, label, disabled }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className={cn('inline-flex items-center gap-2 text-sm', disabled && 'opacity-50')}>
      <BaseSwitch.Root
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        className="relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-line-strong bg-sunken p-0.5 transition-colors data-[checked]:border-fg data-[checked]:bg-raised"
      >
        <BaseSwitch.Thumb className="block size-3.5 rounded-full bg-muted transition-transform data-[checked]:translate-x-4 data-[checked]:bg-fg" />
      </BaseSwitch.Root>
      <span>{label}</span>
    </label>
  );
}

export function Checkbox({ checked, onCheckedChange, label, disabled }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: ReactNode; disabled?: boolean }) {
  return (
    <label className={cn('inline-flex cursor-pointer items-center gap-2 text-sm', disabled && 'opacity-50')}>
      <BaseCheckbox.Root
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        className="flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-line-strong bg-sunken data-[checked]:border-fg data-[checked]:bg-fg"
      >
        <BaseCheckbox.Indicator className="text-canvas">
          <Check size={12} strokeWidth={3} />
        </BaseCheckbox.Indicator>
      </BaseCheckbox.Root>
      <span>{label}</span>
    </label>
  );
}

export interface TabItem {
  value: string;
  label: ReactNode;
  content: ReactNode;
}

export function Tabs({ items, value, onValueChange, defaultValue, className }: { items: TabItem[]; value?: string; onValueChange?: (v: string) => void; defaultValue?: string; className?: string }) {
  return (
    <BaseTabs.Root
      value={value}
      defaultValue={defaultValue ?? items[0]?.value}
      onValueChange={onValueChange ? (v) => onValueChange(String(v)) : undefined}
      className={cn('flex min-h-0 flex-col', className)}
    >
      <BaseTabs.List className="relative flex shrink-0 gap-1 overflow-x-auto border-b border-line">
        {items.map((item) => (
          <BaseTabs.Tab
            key={item.value}
            value={item.value}
            className="cursor-pointer whitespace-nowrap rounded-t-[var(--radius-sm)] px-3.5 py-2 text-sm font-medium text-muted transition-colors hover:text-fg data-[active]:text-accent"
          >
            {item.label}
          </BaseTabs.Tab>
        ))}
        <BaseTabs.Indicator className="absolute bottom-0 left-[var(--active-tab-left)] h-0.5 w-[var(--active-tab-width)] bg-accent transition-all" />
      </BaseTabs.List>
      {items.map((item) => (
        <BaseTabs.Panel key={item.value} value={item.value} className="min-h-0 flex-1 pt-4">
          {item.content}
        </BaseTabs.Panel>
      ))}
    </BaseTabs.Root>
  );
}

export function Tip({ label, children }: { label: ReactNode; children: ReactElement }) {
  return (
    <BaseTooltip.Provider delay={300}>
      <BaseTooltip.Root>
        <BaseTooltip.Trigger render={children} />
        <BaseTooltip.Portal>
          <BaseTooltip.Positioner sideOffset={6}>
            <BaseTooltip.Popup role="tooltip" className="popup-anim z-[60] max-w-xs rounded-[var(--radius-sm)] bg-raised px-2.5 py-1.5 text-xs text-fg shadow-pop">
              {label}
            </BaseTooltip.Popup>
          </BaseTooltip.Positioner>
        </BaseTooltip.Portal>
      </BaseTooltip.Root>
    </BaseTooltip.Provider>
  );
}

export function Collapsible({ title, children, defaultOpen = false, aside, className }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; aside?: ReactNode; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  return (
    <BaseCollapsible.Root open={open} onOpenChange={setOpen} className={cn('rounded-[var(--radius-md)] border border-line bg-panel', className)}>
      <div className="flex items-center gap-2 px-3 py-2">
        <div role="button" tabIndex={0} aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => setOpen(value => !value)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setOpen(value => !value); } }} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left text-sm font-medium">
          <ChevronRight size={14} className={cn('shrink-0 text-muted transition-transform', open && 'rotate-90')} />
          <span className="min-w-0 truncate">{title}</span>
        </div>
        {aside}
      </div>
      <BaseCollapsible.Panel id={panelId} className="border-t border-line px-3 py-3">{children}</BaseCollapsible.Panel>
    </BaseCollapsible.Root>
  );
}
