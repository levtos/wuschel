import { useEffect, useMemo, useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import type { FieldSchema } from '@/features/authoring/model';
import type { Envelope, HistoryItem } from '@/lib/types';
import { MiniHistory } from './MiniHistory';
import { contractFieldLabel, contractTypeId } from './presentation';
import { historyPoints, mergePublications, publication } from './historyModel';
import { useHistoryWindow, useWindowHistory } from './historyWindow';

function remembered(contractId: string): string[] {
  try { const value: unknown = JSON.parse(localStorage.getItem(`wuschel.history.open:${contractId}`) ?? '[]'); return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []; }
  catch { return []; }
}

export function ContractHistory({ envelope, fields, stale }: { envelope: Envelope; fields: Record<string, FieldSchema>; stale: boolean }) {
  const [open, setOpen] = useState(() => remembered(envelope.contract_id));
  const enabled = Object.keys(envelope.fields).some(name => open.includes(name));
  const window = useHistoryWindow();
  const history = useWindowHistory('contracts', envelope.contract_id, window.since, window.until, enabled);
  const gaps = useWindowHistory('gaps', undefined, window.since, window.until, enabled);
  const [updates, setUpdates] = useState<{ owner: string; rows: HistoryItem[] }>({ owner: '', rows: [] });
  const owner = `${envelope.contract_id}:${envelope.registry_revision}`;
  useEffect(() => {
    if (!enabled || stale) return;
    const row = publication(envelope);
    setUpdates(previous => ({ owner, rows: mergePublications(previous.owner === owner ? previous.rows : [], [row]).filter(item => Date.parse(String(item.published_at)) >= window.since) }));
  }, [enabled, stale, envelope, owner, window.since]);
  const rows = useMemo(() => mergePublications(history.data ?? [], updates.owner === owner ? updates.rows : []), [history.data, updates, owner]);
  const until = rows.reduce((latest, row) => { const at = Date.parse(String(row.published_at)); return Number.isFinite(at) ? Math.max(latest, at) : latest; }, window.until);
  const since = Math.max(window.since, until - window.hours * 3_600_000);
  const toggle = (name: string) => {
    if (!enabled && !open.includes(name)) window.refresh();
    const next = open.includes(name) ? open.filter(item => item !== name) : [...open, name];
    setOpen(next);
    try { localStorage.setItem(`wuschel.history.open:${envelope.contract_id}`, JSON.stringify(next)); } catch { /* Optional preference, no history or values are stored. */ }
  };
  return <Panel><PanelHeader title={t('alpha7.contract.history')} description={t('alpha7.contract.historyScope')} /><div className="space-y-5 p-5">
    <label className="text-sm">{t('beta5.history.window')} <select value={window.hours} onChange={event => window.select(Number(event.target.value) === 168 ? 168 : 24)}><option value={24}>{t('beta5.history.day')}</option><option value={168}>{t('beta5.history.week')}</option></select></label>
    {Object.keys(envelope.fields).map(name => <section key={name} className="space-y-3">
      <h3><Button aria-expanded={open.includes(name)} onClick={() => toggle(name)}>{t('b22.history.toggle', { name: contractFieldLabel(contractTypeId(envelope), name) })}</Button></h3>
      {open.includes(name) && <><Button size="sm" disabled={history.isFetching || gaps.isFetching} onClick={() => { if (!window.refresh()) { void history.refetch(); void gaps.refetch(); } }}>{t('b22.history.reload')}</Button>
        {history.isPending || gaps.isPending ? <Spinner /> : history.isError || gaps.isError ? <p role="alert" className="text-sm text-bad">{t('alpha7.contract.historyUnavailable')}</p> : <MiniHistory name={name} contractTypeId={contractTypeId(envelope)} schema={fields[name]} since={since} until={until} now={until} stale={stale} current={stale ? undefined : envelope.fields[name]} points={historyPoints(rows, envelope.contract_id, name, envelope.registry_revision, gaps.data ?? [])} />}
      </>}
    </section>)}
  </div></Panel>;
}
