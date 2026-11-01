import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { uuid } from '@/lib/uuid';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { Notice, Spinner } from '@/components/ui/layout';
import { titleLabel } from './titleLabel';
import type { Catalog } from './CatalogPage';

interface Row { identity: string; key: string; kind: string; protected: boolean; first_seen: string | null; seen_count: number; source: string }
interface Plan { plan_id: string; plan_digest: string; counts: Record<string, number>; items: Row[]; total: number; filtered: number; selected: string[]; reset_counters: boolean; next_offset: number | null; counters_before: number; catalog_count: number; sources: string[] }
interface Filters { kind: string; since: string; until: string; one: boolean; unmaintained: boolean; source: string }
export function CatalogCleanup({ catalog }: { catalog: Catalog }) {
  const cache = useQueryClient();
  const [open, setOpen] = useState(false); const [plan, setPlan] = useState<Plan | null>(null);
  const [filters, setFilters] = useState<Filters>({ kind: '', since: '', until: '', one: false, unmaintained: true, source: '' });
  const [selected, setSelected] = useState<string[]>([]); const [reset, setReset] = useState(true);
  const [checked, setChecked] = useState(''); const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [result, setResult] = useState('');
  const [requestId, setRequestId] = useState(''); const sequence = useRef(0);
  const key = JSON.stringify([selected, reset]);
  const inspect = async (fresh = false, offset = 0, selectFiltered = false) => {
    const seq = ++sequence.current; setBusy(true); setError(''); setConfirmed(false); setChecked('');
    try {
      const answer = await api<Plan>('catalogs/cleanup/preview', { method: 'POST', body: {
        catalog_id: catalog.catalog_id, definition_version: catalog.version,
        ...(!fresh && plan ? { plan_id: plan.plan_id, selected } : {}),
        filters: { ...filters, since: filters.since ? new Date(filters.since).toISOString() : '', until: filters.until ? new Date(filters.until).toISOString() : '' },
        offset, select_filtered: selectFiltered, reset_counters: reset,
      } });
      if (seq !== sequence.current) return;
      setPlan(answer); setSelected(answer.selected); setChecked(JSON.stringify([answer.selected, answer.reset_counters])); setRequestId(uuid());
    } catch (failure) { if (seq === sequence.current) setError(describeError(failure)); }
    finally { if (seq === sequence.current) setBusy(false); }
  };
  const apply = async () => {
    if (!plan || !confirmed || checked !== key || busy) return;
    setBusy(true); setError('');
    try {
      const answer = await api<{ removed: number; reset_counters: boolean }>('catalogs/cleanup/apply', { method: 'POST', body: { plan_id: plan.plan_id, plan_digest: plan.plan_digest, request_id: requestId, confirmed: true } });
      setResult(t(answer.reset_counters ? 'b20.cleanup.done' : 'b20.cleanup.removed', { n: answer.removed })); setPlan(null); setConfirmed(false);
      await Promise.all(['catalog-entries', 'catalog-history', 'catalog-masters', 'catalog-details', 'catalog-detail'].map(name => cache.invalidateQueries({ queryKey: [name] })));
    } catch (failure) { setError(describeError(failure)); setChecked(''); }
    finally { setBusy(false); }
  };
  return <>
    <Button onClick={() => { setOpen(true); setResult(''); setPlan(null); setSelected([]); void inspect(true); }}>{t('b20.cleanup.title')}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) { setOpen(value); sequence.current++; } }} title={t('b20.cleanup.title')} footer={<>
      <Button disabled={busy} onClick={() => void inspect(true)}>{t('b20.cleanup.reload')}</Button>
      <Button disabled={busy || !plan} onClick={() => void inspect()}>{t('b20.cleanup.preview')}</Button>
      <Button variant="danger" disabled={busy || !plan || !confirmed || checked !== key || !!error || (!selected.length && !reset)} onClick={() => void apply()}>{t('b20.cleanup.apply')}</Button>
    </>}>
      <div className="space-y-4">
        <Notice tone="warn">{t('b20.cleanup.explain')}</Notice>
        {busy && <Spinner />}{error && <Notice tone="bad">{error}</Notice>}{result && <Notice tone="info">{result}</Notice>}
        {plan && <>
          <p>{t('b20.cleanup.counts', { total: plan.total, transition: plan.counts.transition ?? 0, unclear: plan.counts.unclear ?? 0, protected: plan.counts.protected ?? 0 })}</p>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={t('b20.cleanup.kind')}>{p => <Select {...p} value={filters.kind} onChange={e => setFilters({ ...filters, kind: e.target.value })}><option value="">{t('b20.cleanup.all')}</option>{['transition', 'unclear', 'protected'].map(kind => <option key={kind} value={kind}>{t(`b20.cleanup.${kind}`)}</option>)}</Select>}</Field>
            <Field label={t('b20.cleanup.source')}>{p => <Select {...p} value={filters.source} onChange={e => setFilters({ ...filters, source: e.target.value })}><option value="">{t('b20.cleanup.all')}</option>{plan.sources.map((source, index) => <option key={source} value={source}>{source === 'historical' ? t('b20.cleanup.unknownSource') : t('b20.cleanup.sourceNumber', { n: index + 1 })}</option>)}</Select>}</Field>
            <Field label={t('b20.cleanup.since')}>{p => <Input {...p} type="datetime-local" value={filters.since} onChange={e => setFilters({ ...filters, since: e.target.value })} />}</Field>
            <Field label={t('b20.cleanup.until')}>{p => <Input {...p} type="datetime-local" value={filters.until} onChange={e => setFilters({ ...filters, until: e.target.value })} />}</Field>
          </div>
          <label className="block"><input type="checkbox" checked={filters.one} onChange={e => setFilters({ ...filters, one: e.target.checked })} /> {t('b20.cleanup.one')}</label>
          <label className="block"><input type="checkbox" checked={filters.unmaintained} onChange={e => setFilters({ ...filters, unmaintained: e.target.checked })} /> {t('b20.cleanup.unmaintained')}</label>
          <Button disabled={busy} onClick={() => void inspect()}>{t('b20.cleanup.filter')}</Button>
          <Button disabled={busy} onClick={() => void inspect(false, 0, true)}>{t('b20.cleanup.selectFiltered')}</Button>
          <Button disabled={busy} onClick={() => { setSelected([]); setConfirmed(false); }}>{t('b20.cleanup.clear')}</Button>
          <p>{t('b20.cleanup.selection', { n: selected.length, filtered: plan.filtered })}</p>
          <ul className="max-h-80 space-y-2 overflow-auto">{plan.items.map(row => <li key={row.identity}><label><input type="checkbox" disabled={busy || row.protected} checked={selected.includes(row.identity)} onChange={e => { setSelected(e.target.checked ? [...selected, row.identity] : selected.filter(id => id !== row.identity)); setConfirmed(false); }} /> {titleLabel({ key: row.key })} · {t(`b20.cleanup.${row.kind}`)} · {t('b20.cleanup.observations', { n: row.seen_count })}</label></li>)}</ul>
          <Button disabled={busy} onClick={() => void inspect(false, 0)}>{t('alpha10.catalog.first')}</Button>
          <Button disabled={busy || plan.next_offset === null} onClick={() => void inspect(false, plan.next_offset ?? 0)}>{t('alpha10.catalog.next')}</Button>
          <label className="block"><input type="checkbox" checked={reset} disabled={busy} onChange={e => { setReset(e.target.checked); setConfirmed(false); }} /> {t('b20.cleanup.reset', { n: plan.catalog_count, before: plan.counters_before })}</label>
          <label className="block"><input type="checkbox" checked={confirmed} disabled={busy || checked !== key || !!error} onChange={e => setConfirmed(e.target.checked)} /> {t('b20.cleanup.confirm', { n: selected.length })}</label>
        </>}
      </div>
    </Dialog>
  </>;
}
