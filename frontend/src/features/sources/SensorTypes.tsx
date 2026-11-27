import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Field, Input, Select } from '@/components/ui/form';
import { Notice, Spinner } from '@/components/ui/layout';
import { DraftEditor } from '@/features/authoring/DraftEditor';
import { asArray, str } from '@/features/authoring/model';
import { api } from '@/lib/api';
import { durationText } from '@/lib/duration';
import { isObject, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import { t } from '@/i18n';
import { effectiveRule, ownRule, sensorTypeName, type SensorType, upgradeFreshness } from './freshness';

interface Proposal {
  sensor_kind: string | null;
  basis: 'device_class' | 'unit' | 'value_type' | 'uncertain';
}

interface ProposalResult {
  proposals: Record<string, Proposal>;
  metadata_available: boolean;
  sensor_kinds: SensorType[];
}

const explain: Record<Proposal['basis'], string> = {
  device_class: t('b31.sensor.deviceClass'),
  unit: t('b31.sensor.unit'),
  value_type: t('b31.sensor.valueType'),
  uncertain: t('b31.sensor.uncertain'),
};

function useProposals(base: Registry, enabled = true) {
  return useQuery({
    queryKey: ['sensor-kind-proposals', base],
    queryFn: () => api<ProposalResult>('registry/freshness/proposals', { method: 'POST', body: base }),
    enabled,
    retry: false,
  });
}

export function confirmSensorTypes(config: Registry, kinds: SensorType[], choices: Record<string, string>): Registry {
  const next = upgradeFreshness(config);
  next.config_schema_version = 5;
  next.sensor_kinds = kinds;
  next.sources = asArray(next.sources).filter(isObject).map(source => {
    const chosen = choices[str(source.source_id)];
    if (!chosen || source.origin === 'manual') return source;
    const row = source.sensor_kind === chosen ? source : ownRule(source, effectiveRule(next, source));
    return { ...row, sensor_kind: chosen, sensor_kind_origin: 'confirmed' };
  });
  return next;
}

export function ConfirmSensorTypes({ base, onClose, onSaved }: EditorProps) {
  const query = useProposals(base);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const kinds = query.data?.sensor_kinds ?? [];
  const proposals = query.data?.proposals ?? {};
  const sources = asArray(base.sources).filter(isObject);
  const groups = [...kinds, { sensor_kind: '', display_name: t('b31.sensor.unknown') }];
  return <DraftEditor base={base} title={t('b4.assign')} size="lg"
    dirty={Object.values(choices).some(Boolean)} input={choices}
    ready={!!query.data && Object.values(choices).some(Boolean)}
    plan={() => ({ config: confirmSensorTypes(base, kinds, choices) })}
    onClose={onClose} onSaved={onSaved}>
    {query.isPending && <Spinner />}
    {query.isError && <Notice tone="bad">{t('b31.sensor.proposalsError')} <Button onClick={() => void query.refetch()}>{t('b31.sensor.retry')}</Button></Notice>}
    {query.data && !query.data.metadata_available && <Notice tone="info">{t('b31.sensor.metadataMissing')}</Notice>}
    {query.data && Object.keys(proposals).length === 0 && <p>{t('b31.sensor.allKnown')}</p>}
    <div className="space-y-4">{groups.map(group => {
      const rows = sources.filter(source => {
        const proposal = proposals[str(source.source_id)];
        return proposal && (proposal.sensor_kind ?? '') === group.sensor_kind;
      });
      if (!rows.length) return null;
      return <section className="space-y-2 rounded-lg border border-line p-3" key={group.sensor_kind}>
        <h3>{group.display_name} · {rows.length}</h3>
        {group.sensor_kind && <Button onClick={() => setChoices({
          ...choices,
          ...Object.fromEntries(rows.map(row => [str(row.source_id), group.sensor_kind])),
        })}>{t('b31.sensor.confirmGroup')}</Button>}
        {rows.map(source => {
          const id = str(source.source_id);
          const rule = effectiveRule(base, source);
          const hint = [explain[proposals[id]!.basis], rule.interval_s == null ? null : durationText(Number(rule.interval_s))].filter(Boolean).join(' · ');
          return <Field key={id} label={str(source.display_name) || id} help={hint}>{props =>
            <Select {...props} value={choices[id] || ''} onChange={event => setChoices({ ...choices, [id]: event.target.value })}>
              <option value="">{t('b31.sensor.skip')}</option>
              {kinds.map(kind => <option key={kind.sensor_kind} value={kind.sensor_kind}>{kind.display_name}</option>)}
            </Select>
          }</Field>;
        })}
      </section>;
    })}</div>
  </DraftEditor>;
}

type EditorProps = { base: Registry; onClose: () => void; onSaved: () => void };

export function ManageSensorTypes({ base, onClose, onSaved }: EditorProps) {
  const query = useProposals(base);
  const [edited, setEdited] = useState<SensorType[] | null>(null);
  const [replacement, setReplacement] = useState<Record<string, string>>({});
  const [removing, setRemoving] = useState<string | null>(null);
  const original = query.data?.sensor_kinds ?? [];
  const rows = edited ?? original;
  const sourceRows = asArray(base.sources).filter(isObject);
  const templateRows = asArray(base.freshness_templates).filter(isObject);
  const usedBy = (key: string) => [...sourceRows, ...templateRows].filter(row => (replacement[str(row.sensor_kind)] || row.sensor_kind) === key).length;
  const plan = () => {
    const config = confirmSensorTypes(base, rows, {});
    const move = (row: JsonObject) => {
      const nextKind = replacement[str(row.sensor_kind)];
      return nextKind ? { ...row, sensor_kind: nextKind } : row;
    };
    config.sources = asArray(config.sources).filter(isObject).map(move);
    config.freshness_templates = asArray(config.freshness_templates).filter(isObject).map(move);
    return { config };
  };
  const ready = rows.length > 0 && rows.every(row => /^[a-z][a-z0-9_]{0,63}$/.test(row.sensor_kind) && row.display_name.trim().length > 0);
  return <DraftEditor base={base} title={t('b31.sensor.manage')} input={{ rows, replacement }}
    dirty={edited !== null || (!!query.data && base.config_schema_version !== 5)} ready={ready && !removing}
    plan={plan} onClose={onClose} onSaved={onSaved}>
    {query.isPending && <Spinner />}
    {query.isError && <Notice tone="bad">{t('b31.sensor.typesError')} <Button onClick={() => void query.refetch()}>{t('b31.sensor.retry')}</Button></Notice>}
    <div className="space-y-4">{rows.map((row, index) => <div className="grid gap-2 sm:grid-cols-2" key={index}>
      <Field label={t('b31.sensor.key')}>{props => <Input {...props} value={row.sensor_kind}
        readOnly={original.some(item => item.sensor_kind === row.sensor_kind)}
        onChange={event => setEdited(rows.map((item, i) => i === index ? { ...item, sensor_kind: event.target.value } : item))} />}</Field>
      <Field label={t('b31.sensor.name')}>{props => <Input {...props} value={row.display_name} maxLength={80}
        onChange={event => setEdited(rows.map((item, i) => i === index ? { ...item, display_name: event.target.value } : item))} />}</Field>
      <Button disabled={rows.length <= 1} onClick={() => {
        if (usedBy(row.sensor_kind)) setRemoving(row.sensor_kind);
        else setEdited(rows.filter((_, i) => i !== index));
      }}>{t('b31.sensor.removeRow')}</Button>
    </div>)}</div>
    {removing && <section className="space-y-2">
      <p>{t('b31.sensor.removeImpact', { count: usedBy(removing), name: sensorTypeName(base, removing) })}</p>
      <Field label={t('b31.sensor.replacement')}>{props => <Select {...props} value={replacement[removing] ?? ''} onChange={event => setReplacement({ ...replacement, [removing]: event.target.value })}>
        <option value="">{t('b4.choose')}</option>
        {rows.filter(item => item.sensor_kind && item.sensor_kind !== removing).map(item => <option key={item.sensor_kind} value={item.sensor_kind}>{item.display_name}</option>)}
      </Select>}</Field>
      <Button onClick={() => {
        const next = { ...replacement }; delete next[removing]; setReplacement(next); setRemoving(null);
      }}>{t('common.cancel')}</Button>
      <Button disabled={!replacement[removing]} onClick={() => {
        const to = replacement[removing]!;
        setReplacement(Object.fromEntries(Object.entries(replacement).map(([from, target]) => [from, target === removing ? to : target])));
        setEdited(rows.filter(item => item.sensor_kind !== removing)); setRemoving(null);
      }}>{t('b31.sensor.removeRow')}</Button>
    </section>}
    <Button disabled={!query.data} onClick={() => setEdited([...rows, { sensor_kind: '', display_name: '' }])}>{t('b31.sensor.add')}</Button>
  </DraftEditor>;
}

interface IdenticalGroup {
  sensor_kind: string;
  rule: JsonObject;
  sources: Record<string, { liveness_source?: string }>;
}

export function shareIdenticalRule(config: Registry, group: IdenticalGroup, name: string): Registry {
  const templates = asArray(config.freshness_templates).filter(isObject);
  const taken = new Set(templates.map(row => row.template_id));
  let number = 1;
  while (taken.has(`freshness.${group.sensor_kind}.${number}`)) number += 1;
  const templateId = `freshness.${group.sensor_kind}.${number}`;
  const chosen = new Set(Object.keys(group.sources));
  return {
    ...config,
    freshness_templates: [...templates, {
      template_id: templateId, sensor_kind: group.sensor_kind, display_name: name.trim(), rule: group.rule,
    }],
    sources: asArray(config.sources).filter(isObject).map(source => {
      const id = str(source.source_id);
      if (!chosen.has(id)) return source;
      return { ...source, freshness_selection: {
        kind: 'shared', template_id: templateId, ...group.sources[id],
      } };
    }),
  };
}

export function ShareSensorRules({ base, onClose, onSaved }: EditorProps) {
  const query = useQuery({
    queryKey: ['freshness', base],
    queryFn: () => api<{ identical_own_rules: IdenticalGroup[] }>('registry/freshness', { method: 'POST', body: base }),
    retry: false,
  });
  const groups = query.data?.identical_own_rules ?? [];
  const sources = new Map(asArray(base.sources).filter(isObject).map(row => [str(row.source_id), row]));
  const [index, setIndex] = useState(-1);
  const [name, setName] = useState('');
  const group = groups[index];
  return <DraftEditor base={base} title={t('b31.sensor.share')} input={{ index, name }}
    dirty={index >= 0} ready={!!group && !!name.trim()}
    plan={() => ({ config: group ? shareIdenticalRule(base, group, name) : base })} onClose={onClose} onSaved={onSaved}>
    <p>{t('b31.sensor.shareHelp')}</p>
    {query.isPending && <Spinner />}
    {query.isError && <Notice tone="bad">{t('b31.sensor.proposalsError')} <Button onClick={() => void query.refetch()}>{t('b31.sensor.retry')}</Button></Notice>}
    {query.isSuccess && !groups.length && <p>{t('b31.sensor.noGroups')}</p>}
    <Field label={t('b31.sensor.sharedSources')}>{props => <Select {...props} value={index} onChange={event => {
      const selected = Number(event.target.value);
      setIndex(selected);
      setName(groups[selected] ? sensorTypeName(base, groups[selected].sensor_kind) : '');
    }}>
      <option value={-1}>{t('b31.sensor.chooseGroup')}</option>
      {groups.map((item, i) => <option key={i} value={i}>{sensorTypeName(base, item.sensor_kind)} · {t('b31.sensor.count', { count: Object.keys(item.sources).length })} · {t(`alpha7.freshness.${str(item.rule.mode)}`)} {item.rule.interval_s == null ? '' : durationText(Number(item.rule.interval_s))}</option>)}
    </Select>}</Field>
    {group && <><Field label={t('b31.sensor.templateName')}>{props => <Input {...props} value={name} maxLength={256} onChange={event => setName(event.target.value)} />}</Field>
      <ul>{Object.keys(group.sources).map(id => <li key={id}>{str(sources.get(id)?.display_name) || id}</li>)}</ul></>}
  </DraftEditor>;
}
