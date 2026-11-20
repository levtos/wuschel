import { useState } from 'react';
import { t } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/form';
import { Switch } from '@/components/ui/controls';
import { PageHeader, Panel, PanelHeader } from '@/components/ui/layout';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { SecondsField } from '@/components/SecondsField';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { asObject, emptyRegistry } from '@/features/authoring/model';
import { deepEqual, type JsonObject } from '@/lib/json';

export function noticeSettings(config: JsonObject | null) {
  const notices = asObject(config?.notifications);
  return { ...notices, signal: { enabled: true, debounce_s: 30, clear_after_s: 10, min_update_s: 300, ...asObject(notices.signal) },
    configuration: { enabled: false, debounce_s: 60, clear_after_s: 10, min_update_s: 300, ...asObject(notices.configuration) }, max_items: notices.max_items ?? 10 };
}
export default function NotificationsPage() {
  const source = useAuthoringSource();
  const [base, setBase] = useState<JsonObject | null>(null);
  const [settings, setSettings] = useState(() => noticeSettings(null));
  return <>
    <PageHeader title={t('alpha9.notifications.title')} description={t('alpha9.notifications.description')} actions={<Button variant="primary" disabled={source.loading} onClick={() => { setBase(source.config ?? emptyRegistry()); setSettings(noticeSettings(source.config)); }}>{t('alpha7.mode.edit')}</Button>} />
    <NoticeFields settings={noticeSettings(source.config)} />
    <Panel className="mt-4"><PanelHeader title={t('alpha9.notifications.preview')} /><div className="space-y-3 p-4 text-sm"><p>{t('alpha9.notifications.signalTitle')}</p><p>{t('alpha9.notifications.signalSummary', { count: 3 })}</p><p>{t('alpha9.notifications.configTitle')}</p><p>{t('alpha9.notifications.configSummary', { count: 2 })}</p></div></Panel>
    {base && <DraftEditor base={base} input={settings} dirty={!deepEqual(settings, noticeSettings(base))} title={t('alpha9.notifications.title')} size="lg" onClose={() => setBase(null)} plan={() => ({ config: { ...base, config_schema_version: Math.max(2, Number(base.config_schema_version ?? 1)), notifications: settings } })}>
      <NoticeFields settings={settings} change={setSettings} />
    </DraftEditor>}
  </>;
}

function NoticeFields({ settings, change }: { settings: ReturnType<typeof noticeSettings>; change?: (value: ReturnType<typeof noticeSettings>) => void }) {
  return <fieldset disabled={!change} className="space-y-4">
      {(['signal', 'configuration'] as const).map(axis => <Panel key={axis}><PanelHeader title={t(`alpha9.notifications.${axis}`)} /><div className="space-y-4 p-4">
        <Switch label={t(`alpha9.notifications.${axis}`)} checked={Boolean(settings[axis].enabled)} disabled={!change} onCheckedChange={enabled => change?.({ ...settings, [axis]: { ...settings[axis], enabled } })} />
        {axis === 'configuration' && <p className="text-sm text-muted">{t('alpha9.notifications.configurationHelp')}</p>}
        <div className="grid gap-4 sm:grid-cols-3">{([
          ['debounce_s', 'delay'], ['clear_after_s', 'clearDelay'], ['min_update_s', 'updateDelay'],
        ] as const).map(([key, label]) => <SecondsField key={key} label={t(`alpha9.notifications.${label}`)} value={Number(settings[axis][key])} onChange={value => change?.({ ...settings, [axis]: { ...settings[axis], [key]: value } })} />)}</div>
      </div></Panel>)}
      <Field label={t('alpha9.notifications.maxItems')}>{props => <Input {...props} type="number" min={1} max={10} step={1} value={Number(settings.max_items)} onChange={event => change?.({ ...settings, max_items: event.target.value === '' ? 0 : event.target.valueAsNumber })} />}</Field>
    </fieldset>;
}
