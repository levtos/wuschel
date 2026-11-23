import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/controls';
import { Field, Select } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';
import { InlineHelp } from '@/components/ui/inline-help';
import { CatalogEditor } from '@/editors/RegistryObjects';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { contractHref, definitionHref, ObjectReadLink } from '@/features/authoring/ObjectReadLinks';
import { asArray, asObject, str } from '@/features/authoring/model';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { t } from '@/i18n';
import { clone, isObject, type JsonObject } from '@/lib/json';
import { useRevisions } from '@/lib/queries';
import type { Registry } from '@/lib/types';
import { catalogUsage, catalogVersionName, indirectConsumers, nextCatalogVersion, switchCatalogConsumers } from './catalogVersioning';

export function CatalogUsage({ config, id, version }: { config: Registry; id: string; version: number }) {
  const { definitions, direct, indirect } = catalogUsage(config, id, version);
  return <section className="space-y-2">
    <div className="flex items-center gap-1"><h3 className="font-medium">{t('a2.care.usedBy')}</h3><InlineHelp text={t('a2.help.consumers')} /></div>
    {definitions.length + direct.length + indirect.length === 0 ? <p>{t('a2.care.noConsumers')}</p> : <>
      <h4>{t('a2.care.templates')}</h4>
      <ul>{definitions.map(template => <li key={str(template.type_id) + '@' + Number(template.type_version)}><ObjectReadLink to={definitionHref(str(template.type_id), Number(template.type_version)) + '?tab=rules'}>{t('a2.care.versionName', { name: str(template.display_name) || str(template.type_id), version: Number(template.type_version) })}</ObjectReadLink></li>)}</ul>
      <ConsumerList config={config} entries={direct} label={t('a2.care.direct')} />
      <ConsumerList config={config} entries={indirect} label={t('a2.care.indirect')} />
    </>}
  </section>;
}

function ConsumerList({ config, entries, label }: { config: Registry; entries: string[]; label: string }) {
  if (!entries.length) return null;
  return <section><h4>{label}</h4><ul>{entries.map(id => <li key={id}><ObjectReadLink to={contractHref(id)}>{catalogVersionName(config, id)}</ObjectReadLink></li>)}</ul></section>;
}

export function CatalogVersions({ id, version }: { id: string; version: number }) {
  const { config } = useAuthoringSource();
  const revisions = useRevisions();
  const history = (revisions.data ?? []).map(row => row.config);
  const catalogs = asArray(config?.catalogs).filter(isObject);
  const current = catalogs.find(item => item.catalog_id === id && item.kind === 'managed' && Number(item.version) === version);
  const alternatives = catalogs.filter(item => item.catalog_id === id && item.kind === 'managed' && Number(item.version) > version && item.lifecycle !== 'retired');
  const [creating, setCreating] = useState<{ base: Registry; value: JsonObject } | null>(null);
  const [moving, setMoving] = useState<{ base: Registry; target: number; selected: string[] } | null>(null);

  if (!config || !current) return null;
  const available = catalogUsage(moving?.base ?? config, id, version);
  const changeVersion = () => {
    const next = clone(current);
    const number = nextCatalogVersion(config, id, history);
    next.version = number;
    delete next.lifecycle;
    delete next.reactivation_note;
    setCreating({ base: config, value: next });
  };
  return <div className="space-y-3 rounded border border-line p-3 text-sm">
    <CatalogUsage config={config} id={id} version={version} />
    <div className="flex flex-wrap gap-2">
      <Button size="sm" onClick={changeVersion}>{t('a2.care.newVersion')}</Button>
      <Button size="sm" disabled={!alternatives.length || !available.direct.length} onClick={() => setMoving({ base: config, target: Math.min(...alternatives.map(item => Number(item.version))), selected: [] })}>{t('a2.care.switchConsumers')}</Button>
    </div>
    {creating && <DraftEditor title={t('a2.care.newVersion')} base={creating.base} dirty input={creating.value}
      onClose={() => setCreating(null)}
      plan={() => ({ config: { ...creating.base, catalogs: [...asArray(creating.base.catalogs), creating.value] } })}
      onSaved={saved => {
        if (catalogUsage(saved.config, id, version).direct.length) setMoving({ base: saved.config, target: Number(creating.value.version), selected: [] });
      }}>
      <Notice tone="info">{t('a2.care.newVersionHelp')}</Notice>
      <p className="my-3 font-medium">{t('a2.care.versionName', { name: str(asObject(creating.value).display_name) || id, version: creating.value.version })}</p>
      <CatalogEditor identityLocked value={creating.value} pointer={'/catalogs/' + asArray(creating.base.catalogs).length} onChange={value => setCreating({ ...creating, value })} />
    </DraftEditor>}
    {moving && <DraftEditor title={t('a2.care.switchConsumers')} base={moving.base} input={moving}
      dirty={moving.selected.length > 0} ready={moving.selected.length > 0}
      onClose={() => setMoving(null)}
      plan={() => ({ config: switchCatalogConsumers(moving.base, id, version, moving.target, moving.selected, history) })}>
      <Notice tone="warn">{t('a2.care.switchHelp')}</Notice>
      <Field label={t('a2.care.targetVersion')}>{props => <Select {...props} value={moving.target} onChange={event => setMoving({ ...moving, target: Number(event.target.value) })}>
        {asArray(moving.base.catalogs).filter(isObject).filter(item => item.catalog_id === id && Number(item.version) > version && item.lifecycle !== 'retired')
          .map(item => <option key={Number(item.version)} value={Number(item.version)}>{t('a2.care.version', { version: item.version })}</option>)}
      </Select>}</Field>
      <h3 className="my-2 font-medium">{t('a2.care.direct')}</h3>
      <div className="space-y-2">{available.direct.map(id => <Checkbox key={id} checked={moving.selected.includes(id)} label={catalogVersionName(moving.base, id)}
        onCheckedChange={value => setMoving({ ...moving, selected: value ? [...moving.selected, id] : moving.selected.filter(chosen => chosen !== id) })} />)}</div>
      <ConsumerList config={moving.base} entries={indirectConsumers(moving.base, moving.selected)} label={t('a2.care.indirect')} />
    </DraftEditor>}
  </div>;
}
