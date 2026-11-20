import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/form';
import { Notice, PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { api, ApiError, describeError } from '@/lib/api';
import { EffectStrip } from '@/features/authoring/DraftEditor';
import { useEditorGuard } from '@/features/authoring/EditorGuard';
import type { OperationSettings } from './operations';

export default function OperationsPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['operations-settings'], queryFn: ({ signal }) => api<OperationSettings>('operations/settings', { signal }), refetchOnWindowFocus: false, refetchOnReconnect: false });
  const [values, setValues] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState(false);
  const [checked, setChecked] = useState(false);
  const reset = () => { if (query.data) setValues(Object.fromEntries(query.data.settings.map(field => [field.key, field.value]))); setChecked(false); setError(''); };
  useEffect(reset, [query.data]);
  const invalid = query.data?.settings.some(field => !Number.isFinite(values[field.key]) || values[field.key]! < field.minimum || values[field.key]! > field.maximum || field.integer && !Number.isInteger(values[field.key]));
  const changed = query.data?.settings.filter(field => field.value !== values[field.key]).length ?? 0;
  const dirty = Object.keys(values).length > 0 && changed > 0;
  const guard = useEditorGuard(dirty, reset, busy);
  const change = (key: string, value: number) => { setValues(current => ({ ...current, [key]: value })); setSaved(false); setChecked(false); };
  const save = async () => {
    setBusy(true); setError(''); setSaved(false);
    try {
      const result = await api<OperationSettings>('operations/settings', { method: 'PUT', body: values });
      client.setQueryData(['operations-settings'], result); setSaved(true);
    } catch (failure) { setError(failure instanceof ApiError && failure.issues.length ? failure.issues.map(issue => issue.message).join(' ') : describeError(failure)); }
    finally { setBusy(false); }
  };
  return <>
    <PageHeader title={t('b26.operations.title')} description={t('b26.operations.description')} />
    {query.isPending && <Spinner />}
    {query.isError && <Notice tone="bad">{describeError(query.error)}</Notice>}
    {query.data && <div className="space-y-4">
      {query.data.notice && <Notice tone="warn">{t(query.data.notice === 'operations_missing' ? 'b26.operations.missing' : 'b26.operations.invalid')}</Notice>}
      <EffectStrip effect="restart" />
      <p className="text-sm text-muted">{t('b26.operations.restartHelp')}</p>
      <Button disabled={busy} onClick={() => { setValues(Object.fromEntries(query.data!.settings.map(field => [field.key, field.default]))); setSaved(false); setChecked(false); }}>{t('b26.operations.resetAll')}</Button>
      <fieldset disabled={busy} className="grid gap-4 lg:grid-cols-2">
        {query.data.settings.map(field => {
          const value = values[field.key];
          const wrong = !Number.isFinite(value) || value! < field.minimum || value! > field.maximum || field.integer && !Number.isInteger(value);
          const range = t('b26.operations.range', { min: field.minimum, max: field.maximum, unit: field.unit, standard: field.default });
          return <Panel key={field.key} className="space-y-3 p-4">
            <Field label={field.name} hint={t('b26.operations.hint', { explanation: field.explanation, range })} errors={wrong ? [{ code: 'range', message: t(field.integer ? 'b26.operations.integerRange' : 'b26.operations.validRange', { min: field.minimum, max: field.maximum, unit: field.unit }) }] : []}>
              {props => <Input {...props} type="number" min={field.minimum} max={field.maximum} step={field.integer ? 1 : 'any'} value={Number.isFinite(value) ? value : ''} onChange={event => change(field.key, event.target.valueAsNumber)} />}
            </Field>
            <p className="text-xs text-muted">{t('b26.operations.active', { value: field.active_value, unit: field.unit })}</p>
            {field.restart_required && <p className="text-xs text-muted">{t('b26.operations.restart')}</p>}
            {field.pending_restart && <Notice tone="warn">{t('b26.operations.pending')}</Notice>}
            <Button onClick={() => change(field.key, field.default)}>{t('b26.operations.reset')}</Button>
          </Panel>;
        })}
      </fieldset>
      {checked && <Notice tone={invalid ? 'bad' : 'info'}>{t(invalid ? 'a2.edit.operationsInvalid' : 'a2.edit.operationsChecked', { count: changed })}</Notice>}
    </div>}
    {saved && <Notice className="mt-4" tone="ok">{t('b26.operations.saved')}</Notice>}
    {error && <Notice className="mt-4" tone="bad">{error}</Notice>}
    <footer className="sticky bottom-0 mt-4 flex flex-wrap justify-end gap-2 border-t border-line bg-panel p-3">
      <Button disabled={busy || !dirty} onClick={() => guard.requestClose()}>{t('common.cancel')}</Button>
      <Button disabled={busy || !query.data} onClick={() => setChecked(true)}>{t('a2.edit.check')}</Button>
      <Button variant="primary" disabled={busy || !query.data || query.isError || invalid} onClick={() => void save()}>{t('b26.operations.save')}</Button>
    </footer>
    {guard.confirmation}
  </>;
}
