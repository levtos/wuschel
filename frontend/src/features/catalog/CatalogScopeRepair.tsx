import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { uuid } from '@/lib/uuid';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Notice, Spinner } from '@/components/ui/layout';
import { catalogLabel } from './catalogLabels';
import { titleLabel } from './titleLabel';

interface Finding { identity: string; key: string; display_name: string; reason: string; master_before: string | null; master_after: string | null }
interface Plan { complete: boolean; plan_digest: string; clear_pc: boolean; catalogs: { catalog_id: string; definition_version: number; moved: number; collisions: number; removed: number; conflicts?: number; findings?: Finding[] }[] }

export function CatalogScopeRepair({ activeRevision }: { activeRevision: number }) {
  const cache = useQueryClient();
  const [open, setOpen] = useState(false); const [clearPc, setClearPc] = useState(true);
  const [plan, setPlan] = useState<Plan | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const requestId = useRef(uuid()); const sequence = useRef(0); const checkedRevision = useRef(0);
  const inspect = async (clear = clearPc) => {
    const serial = ++sequence.current; setBusy(true); setError(''); setPlan(null); checkedRevision.current = activeRevision;
    try { const result = await api<Plan>('catalogs/scope/preview', { method: 'POST', body: { clear_pc: clear, expected_active_revision: activeRevision } }); if (serial === sequence.current) { setPlan(result); requestId.current = uuid(); } }
    catch (failure) { if (serial === sequence.current) setError(describeError(failure)); }
    finally { if (serial === sequence.current) setBusy(false); }
  };
  const apply = async () => {
    if (!plan || plan.complete || busy || checkedRevision.current !== activeRevision) return;
    setBusy(true); setError('');
    try {
      setPlan(await api<Plan>('catalogs/scope/apply', { method: 'POST', body: { expected_active_revision: activeRevision, clear_pc: clearPc, plan_digest: plan.plan_digest, request_id: requestId.current, confirmed: true } }));
      await Promise.all(['catalog-entries', 'catalog-history', 'catalog-masters', 'catalog-details', 'managed-catalogs'].map(name => cache.invalidateQueries({ queryKey: [name] })));
    } catch (failure) { setError(describeError(failure)); setPlan(null); }
    finally { setBusy(false); }
  };
  return <>
    <Button onClick={() => { setOpen(true); void inspect(); }}>{t('b21.repair.title')}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) { setOpen(value); ++sequence.current; } }} title={t('b21.repair.title')} footer={<>
      <Button disabled={busy || plan?.complete} onClick={() => void inspect()}>{t('alpha10.catalog.preview')}</Button>
      <Button variant="danger" disabled={busy || !plan || plan.complete || checkedRevision.current !== activeRevision} onClick={() => void apply()}>{t('b21.repair.confirm')}</Button>
    </>}>
      <div className="space-y-4">
        <Notice tone="warn">{t('b21.repair.explain')}</Notice>
        <label><input type="checkbox" checked={clearPc} disabled={busy || plan?.complete} onChange={event => { setClearPc(event.target.checked); void inspect(event.target.checked); }} /> {t('b21.repair.pc')}</label>
        {busy && <Spinner />}{error && <Notice tone="bad">{error}</Notice>}
        {plan?.complete ? <Notice tone="info">{t('b21.repair.done')}</Notice> : plan && <ul>{plan.catalogs.map(row => <li key={row.catalog_id}>
          <p>{catalogLabel({ catalog_id: row.catalog_id, version: row.definition_version })}: {t('b21.repair.counts', { moved: row.moved, collisions: row.collisions, removed: row.removed })}</p>
          {!!row.conflicts && <details><summary>{t('b21.repair.resolutions', { n: row.conflicts })}</summary><ul>{row.findings?.map((finding, index) => <li key={`${finding.identity}-${index}`} className="my-3">
            <p>{titleLabel(finding)}</p><p>{t(`b21.repair.resolution.${finding.reason}`)}</p>
            <p>{t('b21.repair.masterBefore')}: {finding.master_before ? titleLabel({ key: finding.master_before }) : t('alpha10.catalog.independent')}</p>
            <p>{t('b21.repair.masterAfter')}: {finding.master_after ? titleLabel({ key: finding.master_after }) : t('alpha10.catalog.independent')}</p>
          </li>)}</ul></details>}
        </li>)}</ul>}
      </div>
    </Dialog>
  </>;
}
