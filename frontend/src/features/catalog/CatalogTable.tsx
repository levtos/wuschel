import { Fragment, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Music2, ChevronDown } from 'lucide-react';
import { t } from '@/i18n';
import { EditButton } from '@/components/EditButton';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Notice, Spinner } from '@/components/ui/layout';
import { api, describeError } from '@/lib/api';
import { formatTime, pluralize } from '@/lib/format';
import type { Catalog, Entry } from './CatalogPage';
import { catalogLabel, catalogValue } from './catalogLabels';
import { titleLabel } from './titleLabel';
import { CatalogMediaFacts } from './CatalogMediaFacts';

interface Props {
  catalogs: Catalog[]; rows: Entry[]; selected: Record<string, Entry>;
  setSelected: (next: Record<string, Entry>) => void;
  onEdit: (entry: Entry, catalog: Catalog) => void; onDetails: (entry: Entry, catalog: Catalog) => void;
  parameters: string; all: boolean; expand: boolean; onClassSort: () => void;
}
export const entryKey = (entry: Entry) => JSON.stringify([entry.catalog_id ?? '', entry.definition_version ?? 0, entry.identity]);
function Family({ entry, catalog, props }: { entry: Entry; catalog: Catalog; props: Props }) {
  const automatic = entry.matches_filter === false && (entry.matching_variant_count ?? 0) > 0;
  const [open, setOpen] = useState(props.expand || automatic); const [after, setAfter] = useState('');
  useEffect(() => { if (automatic || props.expand) setOpen(true); }, [automatic, props.expand]);
  const params = new URLSearchParams(props.parameters);
  for (const [key, value] of Object.entries({ catalog_id: catalog.catalog_id, definition_version: String(catalog.version), identity: entry.identity, after })) params.set(key, value);
  const variants = useQuery({ queryKey: ['catalog-details', catalog.catalog_id, catalog.version, entry.identity, after, props.parameters], enabled: open && !!entry.variant_count,
    queryFn: () => api<{ variants: Entry[]; next_cursor: string | null }>(`catalogs/care/detail?${params}`) });
  const columns = props.all ? 10 : 9;
  const row = (value: Entry, nested = false) => {
    const enriched = { ...value, catalog_id: catalog.catalog_id, definition_version: catalog.version, content_revision: entry.content_revision };
    const key = entryKey(enriched);
    return <tr key={key} data-catalog-role={nested ? 'variant' : 'root'} className={nested ? 'border-b border-line' : entry.variant_count ? 'border-b border-line bg-accent/10' : 'border-b border-line'}>
      <td className="p-3"><input type="checkbox" aria-label={t('b17.selectTitle', { title: titleLabel(value) })} checked={!!props.selected[key]} onChange={event => { const next = { ...props.selected }; if (event.target.checked) next[key] = enriched; else delete next[key]; props.setSelected(next); }} /></td>
      <td className={nested ? 'border-l-2 border-accent pl-8 py-3' : 'p-3'}><div className="flex items-center gap-2">{!nested && <Music2 size={16} aria-hidden="true" />}<Button variant="ghost" className="justify-start text-left" onClick={() => props.onDetails(enriched, catalog)}>{titleLabel(value)}</Button></div>
        {nested && <p className="text-xs text-muted">{t('b22.catalog.inheritedClass')}</p>}{value.candidate && <p className="text-xs text-muted">{t('b17.candidate')}</p>}
        {value.matches_filter === false && <p className="text-xs text-muted">{t('b22.catalog.variantMatch', { n: value.matching_variant_count ?? 0 })}</p>}
        {value.display_metadata?.artist && <p className="text-sm text-muted">{t('b22.stash.performers')}: {value.display_metadata.artist}</p>}
        <CatalogMediaFacts metadata={value.catalog_metadata} /><div className="flex gap-1"><Badge>{catalogValue('media_type', value.media_type)}</Badge><Badge>{catalogValue('signal_type', value.signal_type)}</Badge></div>
        {!nested && !!entry.variant_count && <Button variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}><ChevronDown size={14} aria-hidden="true" />{t('b17.variants')} · {entry.variant_count}</Button>}
      </td>
      <td className="p-3"><Badge>{t(nested ? 'b22.catalog.variantRole' : entry.variant_count ? 'alpha10.catalog.master' : 'alpha10.catalog.independent')}</Badge></td>
      <td className="p-3"><Badge tone={value.review_status === 'reviewed' ? 'ok' : value.review_status === 'hidden' ? 'neutral' : 'warn'}>{t(`b17.status.${value.review_status ?? 'open'}`)}</Badge>{!value.complete && <Badge>{t('b21.withoutClass')}</Badge>}</td>
      <td className="p-3">{catalogValue('scope', value.scope)}</td>
      {props.all && <td className="p-3">{catalogLabel(catalog)}</td>}
      <td className="p-3">{value.complete ? catalog.classification_schema.legend?.[value.effective_classification]?.display_name ?? value.effective_classification : t('alpha10.catalog.unassigned')}</td>
      <td className="p-3 tabular-nums">{pluralize(nested ? value.seen_count ?? 0 : value.family_seen_count ?? value.seen_count ?? 0, t('b26.catalog.playOne'), t('b26.catalog.playMany'))}</td>
      <td className="p-3 whitespace-nowrap">{formatTime(nested ? value.last_seen : value.family_last_seen ?? value.last_seen)}</td>
      <td className="p-3"><EditButton onClick={() => props.onEdit(enriched, catalog)} /></td>
    </tr>;
  };
  return <tbody>{row(entry)}{open && !!entry.variant_count && (variants.isPending ? <tr><td colSpan={columns + 1}><Spinner /></td></tr> : variants.isError ? <tr><td colSpan={columns + 1}><Notice tone="bad">{describeError(variants.error)}</Notice></td></tr> : <>{variants.data.variants.map(value => row(value, true))}{variants.data.next_cursor && <tr><td colSpan={columns + 1}><Button onClick={() => setAfter(variants.data.next_cursor ?? '')}>{t('alpha10.catalog.next')}</Button></td></tr>}</>)}</tbody>;
}
export function CatalogTable(props: Props) {
  return <div className="overflow-x-auto"><table className="w-full text-left text-sm" aria-label={t('b22.catalog.entryList')}><thead><tr>
    {[t('b26.catalog.selection'), t('b26.catalog.titleVersion'), t('b26.catalog.role'), t('b26.catalog.status'), t('b26.catalog.output'), ...(props.all ? [t('alpha10.catalog.catalog')] : [])].map(label => <th key={label} scope="col" className="p-3">{label}</th>)}
    <th scope="col" className="p-3"><Button variant="ghost" onClick={props.onClassSort}>{t('alpha10.catalog.class')}</Button></th>
    {[t('b26.catalog.plays'), t('b26.catalog.lastPlay'), t('b22.object.actions')].map(label => <th key={label} scope="col" className="p-3">{label}</th>)}
  </tr></thead>{props.rows.map(entry => { const catalog = props.catalogs.find(item => item.catalog_id === entry.catalog_id && item.version === entry.definition_version) ?? props.catalogs[0]; return catalog ? <Family key={entryKey(entry)} entry={entry} catalog={catalog} props={props} /> : <Fragment key={entryKey(entry)} />; })}</table></div>;
}
