import { useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/form';
import { Collapsible } from '@/components/ui/controls';
import { PageHeader, Panel, Spinner } from '@/components/ui/layout';
import { SecondsField } from '@/components/SecondsField';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { asObject, emptyRegistry } from '@/features/authoring/model';
import { deepEqual, type JsonObject } from '@/lib/json';
import { durationText } from '@/lib/duration';

function retentionValues(config: JsonObject | null) {
  const policy = asObject(config?.history_retention);
  return { raw_days: Number(policy.raw_days ?? 7), rollup_days: Number(policy.rollup_days ?? 730), batch_size: Number(policy.batch_size ?? 1000), interval_s: Number(policy.interval_s ?? 3600) };
}

type Retention = ReturnType<typeof retentionValues>;

function retentionErrors(values: Retention) {
  const rawError = !Number.isInteger(values.raw_days) || values.raw_days < 1 || values.raw_days > 3650;
  const rollupError = !Number.isInteger(values.rollup_days) || values.rollup_days < 1 || values.rollup_days > 36500;
  const relationError = !rawError && !rollupError && values.rollup_days < values.raw_days;
  const advancedError = !Number.isInteger(values.batch_size) || values.batch_size < 1 || values.batch_size > 10000 || !Number.isInteger(values.interval_s) || values.interval_s < 60 || values.interval_s > 86400;
  return { rawError, rollupError, relationError, advancedError, invalid: rawError || rollupError || relationError || advancedError };
}

export default function RetentionPage() {
  const src = useAuthoringSource();
  const [base, setBase] = useState<JsonObject | null>(null);
  const [values, setValues] = useState(() => retentionValues(null));
  const [saved, setSaved] = useState(false);
  return <>
    <PageHeader title={t('alpha9.retention.title')} description={t('alpha9.retention.description')} actions={<Button variant="primary" disabled={src.loading} onClick={() => { setBase(src.config ?? emptyRegistry()); setValues(retentionValues(src.config)); setSaved(false); }}>{t('alpha7.mode.edit')}</Button>} />
    {src.loading ? <Spinner /> : <Panel className="max-w-4xl space-y-5 p-5">
      {!src.config?.history_retention && <p className="text-sm text-muted">{t('alpha9.retention.suggestion')}</p>}
      <RetentionFields values={retentionValues(src.config)} />
      <p className="text-sm text-muted">{t('alpha9.retention.deleteEffect')}</p>
    </Panel>}
    {base && <DraftEditor base={base} input={values} dirty={!base.history_retention || !deepEqual(values, retentionValues(base))} ready={!retentionErrors(values).invalid} title={t('alpha9.retention.title')} size="lg" onClose={() => setBase(null)} onSaved={() => setSaved(true)} plan={() => ({ config: { ...base, history_retention: { ...asObject(base.history_retention), ...values } } })}>
      <RetentionFields values={values} onChange={setValues} />
      <p className="mt-3 text-sm text-muted">{t('alpha9.retention.deleteEffect')}</p>
    </DraftEditor>}
    {saved && <p role="status" className="mt-4 text-sm text-ok">{t('alpha9.retention.pending')}</p>}
  </>;
}

function RetentionFields({ values, onChange }: { values: Retention; onChange?: (values: Retention) => void }) {
  const { rawError, rollupError, relationError, advancedError } = retentionErrors(values);
  const change = (key: keyof Retention, value: number) => onChange?.({ ...values, [key]: value });
  const relation = relationError ? [{ code: 'relation', message: t('a2.edit.relation.ge', { left: `${t('alpha9.retention.rollupDays')} (${durationText(values.rollup_days * 86_400)})`, right: `${t('alpha9.retention.rawDays')} (${durationText(values.raw_days * 86_400)})` }) }] : [];
  return <fieldset disabled={!onChange} className="space-y-5">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label={t('alpha9.retention.rawDays')} hint={t('alpha9.retention.rawHelp')} errors={rawError ? [{ code: 'range', message: t('alpha9.retention.rawRange') }] : relation}>{props => <Input {...props} type="number" min={1} max={3650} step={1} value={values.raw_days} onChange={event => change('raw_days', event.target.value === '' ? 0 : event.target.valueAsNumber)} />}</Field>
          <Field label={t('alpha9.retention.rollupDays')} hint={t('alpha9.retention.rollupHelp')} errors={rollupError ? [{ code: 'range', message: t('alpha9.retention.rollupRange') }] : relation}>{props => <Input {...props} type="number" min={1} max={36500} step={1} value={values.rollup_days} onChange={event => change('rollup_days', event.target.value === '' ? 0 : event.target.valueAsNumber)} />}</Field>
        </div>
        <Collapsible title={t('alpha7.contract.advanced')}><div className="grid gap-5 sm:grid-cols-2">
          <Field label={t('alpha9.retention.batch')}>{props => <Input {...props} type="number" min={1} max={10000} step={1} value={values.batch_size} onChange={event => change('batch_size', event.target.value === '' ? 0 : event.target.valueAsNumber)} />}</Field>
          <SecondsField label={t('alpha9.retention.interval')} min={60} max={86400} value={values.interval_s} onChange={value => change('interval_s', value)} />
        </div>{advancedError && <p role="alert" className="mt-3 text-sm text-bad">{t('alpha9.retention.advancedError')}</p>}</Collapsible>
      </fieldset>;
}
