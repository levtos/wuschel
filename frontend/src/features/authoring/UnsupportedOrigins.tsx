import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Notice } from '@/components/ui/layout';
import { describeError } from '@/lib/api';
import type { Registry } from '@/lib/types';
import { t } from '@/i18n';
import { asArray, asObject, str } from './model';

function unsupportedSources(config: Registry): Set<string> {
  return new Set(asArray(config.bindings).map(asObject)
    .filter(binding => ['connectivity', 'scheduler'].includes(str(asObject(binding.adapter).kind)))
    .map(binding => str(binding.source_id)));
}

export function removeUnsupportedOrigins(config: Registry): Registry {
  const ids = unsupportedSources(config);
  return { ...config, sources: asArray(config.sources).map(source => {
    const row = asObject(source);
    if (!ids.has(str(row.source_id)) || !row.value_origin) return source;
    const corrected = { ...row };
    delete corrected.value_origin;
    return corrected;
  }) };
}

/** Explicit draft correction only; opening a page never changes configuration. */
export function UnsupportedOrigins({ config, onCorrect }: { config: Registry | null; onCorrect: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!config) return null;
  const ids = unsupportedSources(config);
  const count = asArray(config.sources).map(asObject).filter(row => ids.has(str(row.source_id)) && row.value_origin).length;
  if (!count) return null;
  return <Notice tone="warn"><p>{t('b22.origins.help', { n: count })}</p><p>{t('b22.origins.draftOnly')}</p>
    <Button disabled={busy} onClick={() => { setBusy(true); setError(''); void onCorrect().catch(failure => setError(describeError(failure))).finally(() => setBusy(false)); }}>{t('b22.origins.remove')}</Button>
    {error && <p role="alert">{error}</p>}
  </Notice>;
}
