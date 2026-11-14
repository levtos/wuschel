import { useState } from 'react';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Notice } from '@/components/ui/layout';
import { downloadText } from '@/features/authoring/importExport';
import type { Catalog, Entry } from './CatalogPage';
import { titleLabel } from './titleLabel';
import { catalogLabel } from './catalogLabels';

interface Findings { items: (Entry & { kind: string; catalog_id: string; definition_version: number })[]; counts: Record<string, number>; total: number; next_offset: number | null }
export function CatalogInspection({ catalog }: { catalog?: Catalog }) {
  const [open, setOpen] = useState(false); const [findings, setFindings] = useState<Findings | null>(null);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const parameters = new URLSearchParams({ catalog_id: catalog?.catalog_id ?? 'all', definition_version: String(catalog?.version ?? 1) });
  const inspect = async (offset = 0) => { setOpen(true); setBusy(true); setError(''); try { setFindings(await api<Findings>(`catalogs/care/inspect?${parameters}&offset=${offset}`)); } catch (failure) { setError(describeError(failure)); } finally { setBusy(false); } };
  return <div className="flex gap-3"><Button disabled={busy} onClick={() => void inspect()}>{t('b26.catalog.inspect')}</Button>
    <Button disabled={busy} onClick={async () => { setBusy(true); setError(''); try { const result = await api<{ csv: string }>(`catalogs/care/export?${parameters}`); downloadText('master-varianten.csv', result.csv, 'text/csv;charset=utf-8'); } catch (failure) { setOpen(true); setError(describeError(failure)); } finally { setBusy(false); } }}>{t('b26.catalog.export')}</Button>
    <Dialog open={open} onOpenChange={setOpen} title={t('b26.catalog.inspect')} footer={<><Button disabled={busy} onClick={() => void inspect()}>{t('b26.catalog.inspectAgain')}</Button><Button disabled={busy || findings?.next_offset == null} onClick={() => void inspect(findings?.next_offset ?? 0)}>{t('alpha10.catalog.next')}</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      {findings && <><p>{t('b26.catalog.findings', { count: findings.total })}</p><dl>{Object.entries(findings.counts).map(([kind, count]) => <div key={kind}><dt>{t(`b26.catalog.finding.${kind}`)}</dt><dd>{count}</dd></div>)}</dl><ul>{findings.items.map((row, index) => <li key={index}>{catalogLabel({ catalog_id: row.catalog_id, version: row.definition_version })} · {titleLabel(row)} · {t(`b26.catalog.finding.${row.kind}`)}</li>)}</ul></>}
    </Dialog>
  </div>;
}
