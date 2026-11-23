import { t as uiText } from '@/i18n';
import { AlertTriangle, CircleAlert, Info, Loader2 } from 'lucide-react';
import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { HerdFigure } from '@/components/Herd';

export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={cn('rounded-[var(--radius-lg)] border border-line bg-panel', className)} {...props} />;
}

export function PanelHeader({ title, aside, description }: { title: ReactNode; aside?: ReactNode; description?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
      </div>
      {aside && <div className="flex flex-wrap items-center gap-2">{aside}</div>}
    </header>
  );
}

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header data-page-header className="mb-5 flex flex-wrap items-end justify-between gap-3 pr-12">
      <div className="flex min-w-0 items-center gap-4">
        <HerdFigure />
        <div>
        <h1 className="text-xl font-semibold tracking-tight [overflow-wrap:anywhere]">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

const noticeTone = {
  info: { icon: Info, cls: 'border-info/40 bg-info/10 text-info' },
  warn: { icon: AlertTriangle, cls: 'text-warn' },
  bad: { icon: CircleAlert, cls: 'border-bad/40 bg-bad/10 text-bad' },
  ok: { icon: Info, cls: 'border-ok/40 bg-ok/10 text-ok' },
};

export function Notice({ tone = 'info', title, children, className, role }: { tone?: keyof typeof noticeTone; title?: ReactNode; children?: ReactNode; className?: string; role?: 'alert' | 'status' }) {
  const { icon: Icon, cls } = noticeTone[tone];
  return (
    <div role={role ?? (tone === 'bad' ? 'alert' : 'status')} className={cn('flex gap-2.5 text-sm', tone !== 'warn' && 'rounded-[var(--radius-md)] border px-3 py-2.5', cls, className)}>
      <Icon size={16} className="mt-0.5 shrink-0" />
      <div className="min-w-0 text-fg">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={cn('text-muted', title && 'mt-0.5')}>{children}</div>}
      </div>
    </div>
  );
}

export function Spinner({ label = uiText("components.ui.layout.ladt_c8ebc9") }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted">
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  );
}

export function Empty({ title, children, action, icon }: { title: ReactNode; children?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <p className="font-medium">{title}</p>
      {children && <p className="max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function KeyValue({ items }: { items: { label: ReactNode; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,max-content)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map((item, i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{item.label}</dt>
          <dd className="min-w-0 break-words">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
