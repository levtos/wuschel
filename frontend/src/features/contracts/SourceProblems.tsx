import { Link } from 'react-router';
import { t } from '@/i18n';
import { reasonLabel } from '@/i18n/codes';
import { Badge } from '@/components/ui/badge';
import { asArray, str } from '@/features/authoring/model';
import { isObject } from '@/lib/json';
import type { Envelope, Registry } from '@/lib/types';

export function SourceProblems({ rows, config }: { rows: { envelope: Envelope | null; stale: boolean }[]; config: Registry | null }) {
  const sources = asArray(config?.sources).filter(isObject);
  const problems = new Map<string, Set<string>>();
  for (const row of rows) if (!row.stale && row.envelope?.contract_health === 'degraded') for (const field of Object.values(row.envelope.fields)) for (const reason of field.reasons ?? []) {
    if (!reason.source_id || !sources.some(source => source.source_id === reason.source_id)) continue;
    const codes = problems.get(reason.source_id) ?? new Set(); codes.add(reason.code); problems.set(reason.source_id, codes);
  }
  return problems.size ? <section className="mt-4 rounded-lg border border-line p-4"><h2>{t('b22.source.problems')}</h2><ul>{[...problems].map(([id, codes]) => <li key={id} className="flex flex-wrap gap-2 py-2"><Badge tone="warn">{t('beta16.inputs.source')}</Badge><Link className="underline" to={`/sources/devices?source_id=${encodeURIComponent(id)}`}>{str(sources.find(source => source.source_id === id)?.display_name) || id}</Link><span>{[...codes].map(reasonLabel).join(' · ')}</span></li>)}</ul></section> : null;
}
