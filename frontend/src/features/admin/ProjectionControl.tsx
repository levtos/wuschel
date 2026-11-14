import { useMutation } from '@tanstack/react-query';
import { t } from '@/i18n';
import { api, describeError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Notice } from '@/components/ui/layout';

export function ProjectionControl({ paused, disabled, onChanged }: { paused: boolean; disabled: boolean; onChanged: () => void }) {
  const change = useMutation({
    mutationFn: () => api<{ projection_paused: boolean }>('operations/projection', { method: 'PUT', body: { paused: !paused } }),
    onSuccess: onChanged,
  });
  return <div className="space-y-3">
    <p className="text-sm text-muted">{t('b26.projection.help')}</p>
    {paused && <Notice tone="warn">{t('b26.projection.paused')}</Notice>}
    <Button aria-pressed={paused} disabled={disabled || change.isPending} onClick={() => change.mutate()}>{t(paused ? 'b26.projection.resume' : 'b26.projection.pause')}</Button>
    {change.isError && <Notice tone="bad">{describeError(change.error)}</Notice>}
  </div>;
}
