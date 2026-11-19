import { Info } from 'lucide-react';
import { useId } from 'react';
import { t } from '@/i18n';

export function InlineHelp({ text }: { text: string }) {
  const id = useId();
  return <span className="group/help relative inline-flex items-center">
    <span tabIndex={0} aria-label={t('a2.read.help')} aria-describedby={id} className="inline-flex rounded-sm text-muted focus-visible:outline-2 focus-visible:outline-accent"><Info size={14} aria-hidden="true" /></span>
    <span id={id} role="tooltip" className="sr-only rounded-md border border-line bg-panel p-2 text-xs text-fg shadow-pop group-hover/help:not-sr-only group-focus-within/help:not-sr-only group-hover/help:absolute group-focus-within/help:absolute left-0 top-full z-20 w-56">{text}</span>
  </span>;
}
