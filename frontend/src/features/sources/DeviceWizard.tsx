import { useMemo, useState, type ReactNode } from 'react';
import { hasTranslation, t } from '@/i18n';
import { codeLabel } from '@/i18n/codes';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { Spinner } from '@/components/ui/layout';
import { useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import { describeError } from '@/lib/api';
import { isObject, type JsonObject } from '@/lib/json';
import { validIdentifier } from '@/lib/identifiers';
import { formatNumber } from '@/lib/format';
import type { HaEntity } from '@/lib/types';
import { DEVICE_PRESETS, DevicePlanError, fieldTypeFor, freshnessPreset, planDevice, presetFor, type DeviceOptions, type DevicePreset } from './devicePlan';
import { FreshnessSettings } from './FreshnessSettings';
import { effectiveRule } from './freshness';
import { invalidDurations } from '@/components/SecondsField';
import { useHaCatalog } from './useHaCatalog';
import { CategorySelect, useEditorial } from '@/features/editorial/Editorial';
import { NO_ROOM, type Category } from '@/features/editorial/model';

export function deviceValue(entity: HaEntity): string {
  const value = entity.current_value;
  if (value === undefined || value === null) return t('alpha7.device.valueMissing');
  if (['unknown', 'unavailable'].includes(value)) return codeLabel(value);
  const type = fieldTypeFor(entity);
  if (type === 'bool') return ['on', 'true'].includes(value) ? t('alpha7.device.yes') : ['off', 'false'].includes(value) ? t('alpha7.device.no') : value;
  if (type === 'number' && value.trim() && Number.isFinite(Number(value))) return `${formatNumber(Number(value))}${entity.unit ? ` ${entity.unit}` : ''}`;
  return value;
}

export function DeviceWizard({ open, onOpenChange, title, contract, expectedSchema, onAdded, manualOption }: {
  open: boolean; onOpenChange: (open: boolean) => void; title?: string; contract?: DeviceOptions['contract']; expectedSchema?: FieldSchema;
  onAdded?: (result: ReturnType<typeof planDevice>) => void; manualOption?: ReactNode;
}) {
  const doc = useWorkingDocument(); const ha = useHaCatalog(open, true);
  const metadata = useEditorial(); const [category, setCategory] = useState<Category>('other');
  const [search, setSearch] = useState(''); const [area, setArea] = useState(''); const [domain, setDomain] = useState('');
  const [selected, setSelected] = useState<HaEntity | null>(null);
  const [name, setName] = useState(''); const [preset, setPreset] = useState<DevicePreset>('other'); const [freshness, setFreshness] = useState<JsonObject>(freshnessPreset('other'));
  const [fieldType, setFieldType] = useState<'number' | 'bool' | 'text'>('text'); const [sourceId, setSourceId] = useState(''); const [contractId, setContractId] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [sharedTemplate, setSharedTemplate] = useState('');
  const areas = [...new Map(ha.entities.filter(entity => entity.area_id).map(entity => [entity.area_id!, entity.area_name || entity.area_id!])).entries()];
  const domains = [...new Set(ha.entities.map(entity => entity.entity_id.split('.')[0]!))].sort();
  const shown = ha.entities.filter(entity => (!search || `${entity.display_name} ${entity.entity_id}`.toLowerCase().includes(search.toLowerCase())) && (!area || (area === '__none__' ? !entity.area_id : entity.area_id === area)) && (!domain || entity.entity_id.startsWith(`${domain}.`))).slice(0, 300);
  const existingBinding = selected ? asArray(doc.config?.bindings).filter(isObject).find(binding => asObject(binding.adapter).kind === 'ha_state' && asObject(binding.adapter).ha_entity_id === selected.entity_id) : undefined;
  const existingSource = asArray(doc.config?.sources).filter(isObject).find(source => source.source_id === existingBinding?.source_id);
  const suggestions = useMemo(() => {
    if (!selected || !doc.config) return null;
    try { return planDevice(doc.config, selected, { contract, name, freshness }); } catch { return null; }
  }, [selected, doc.config, contract, name, freshness]);
  const pick = (entity: HaEntity) => {
    setSelected(entity); setName(entity.display_name); const next = presetFor(entity); setPreset(next);
    const binding = asArray(doc.config?.bindings).filter(isObject).find(binding => asObject(binding.adapter).kind === 'ha_state' && asObject(binding.adapter).ha_entity_id === entity.entity_id);
    const source = asArray(doc.config?.sources).filter(isObject).find(source => source.source_id === binding?.source_id);
    if (source) setName(str(source.display_name) || entity.display_name);
    setFreshness(source ? effectiveRule(doc.config ?? {}, source) : freshnessPreset(next));
    setSharedTemplate(source && asObject(source.freshness_selection).kind === 'shared' ? String(asObject(source.freshness_selection).template_id) : '');
    const sourceType = asObject(source?.value_schema).type || (source?.source_type === 'numeric' ? 'number' : source?.source_type === 'boolean' ? 'bool' : source?.source_type === 'text' ? 'text' : null);
    setFieldType(['number', 'bool', 'text'].includes(String(sourceType)) ? sourceType as 'number' | 'bool' | 'text' : fieldTypeFor(entity));
    setSourceId(''); setContractId(''); setError('');
  };
  const liveness = asArray(doc.config?.sources).filter(isObject).filter(source => source.source_type === 'boolean' || asObject(source.value_schema).type === 'bool').map(source => ({ id: str(source.source_id), name: str(source.display_name) || str(source.source_id) }));
  const incompatible = selected && expectedSchema && (expectedSchema.type !== 'object' && fieldType !== expectedSchema.type || (expectedSchema.unit ?? '') !== (fieldType === 'number' ? selected.unit ?? '' : ''));
  const disabledReason = busy ? t('alpha7.device.saving') : !selected ? t('alpha7.device.selectFirst') : incompatible ? t('alpha7.device.incompatible') : sourceId && (!validIdentifier(sourceId) || !sourceId.startsWith('source.')) || contractId && !validIdentifier(contractId) ? t('alpha7.device.badIdentifier') : !freshness.mode || invalidDurations.size ? t('alpha7.device.badFreshness') : freshness.mode !== 'event_stateful' && (!(Number(freshness.interval_s) > 0) || freshness.mode === 'liveness_source' && !freshness.liveness_source) ? t('alpha7.device.badFreshness') : '';
  const create = async () => {
    if (invalidDurations.size) { setError(t('b4.time.precision')); return; }
    if (!selected || disabledReason) return;
    setBusy(true); setError('');
    try {
      const current = await doc.ensureDraft();
      const result = planDevice(current, selected, { contract, name, preset, freshness, freshnessSelection: sharedTemplate ? { kind: 'shared', template_id: sharedTemplate, ...(freshness.mode === 'liveness_source' ? { liveness_source: freshness.liveness_source } : {}) } : undefined, fieldType, sourceId: sourceId || undefined, contractId: contractId || undefined });
      if (contract === 'new' && result.contractId) {
        const object = { kind: 'contract' as const, id: result.contractId };
        const created = asArray(result.config.contracts).filter(isObject).find(item => item.contract_id === result.contractId)!;
        const template = { kind: 'template' as const, id: str(created.type_id) };
        await doc.editAndSave(() => result.config, [object, template].map(ref => ({ object: ref, expected_editorial_version: metadata.item(ref).editorial_version, category, room: NO_ROOM })));
      } else doc.replaceAll(result.config);
      onOpenChange(false); onAdded?.(result);
    } catch (failure) { setError(failure instanceof DevicePlanError ? failure.message : describeError(failure)); }
    finally { setBusy(false); }
  };
  return <Dialog open={open} onOpenChange={value => !busy && onOpenChange(value)} size="xl" title={title || t('alpha7.device.title')} description={t('alpha7.device.explanation')} footer={<>
    {disabledReason && <p role="status" className="w-full text-sm text-muted">{disabledReason}</p>}{manualOption}<Button onClick={() => onOpenChange(false)} disabled={busy}>{t('alpha7.device.cancel')}</Button><Button variant="primary" disabled={!!disabledReason} onClick={() => void create()}>{t(contract === 'new' ? 'alpha7.device.createContract' : 'alpha7.device.add')}</Button>
  </>}>
    <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
      <div className="space-y-3">
        <Input aria-label={t('alpha7.device.search')} placeholder={t('alpha7.device.search')} value={search} onChange={event => setSearch(event.target.value)} />
        <div className="grid grid-cols-2 gap-3"><Field label={t('alpha7.device.area')}>{props => <Select {...props} value={area} onChange={event => setArea(event.target.value)}><option value="">{t('alpha7.device.allAreas')}</option><option value="__none__">{t('alpha7.device.noArea')}</option>{areas.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</Select>}</Field><Field label={t('alpha7.device.domain')}>{props => <Select {...props} value={domain} onChange={event => setDomain(event.target.value)}><option value="">{t('alpha7.device.allDomains')}</option>{domains.map(value => <option key={value} value={value}>{hasTranslation(`alpha7.device.domain.${value}`) ? t(`alpha7.device.domain.${value}`) : t('alpha7.device.domain.other')}</option>)}</Select>}</Field></div>
        {ha.isPending && <Spinner />}{ha.isError && <p role="alert" className="text-sm text-bad">{t('alpha7.device.unavailable')}</p>}
        <ul aria-label={t('alpha7.device.entities')} className="max-h-96 divide-y divide-line overflow-auto rounded-lg border border-line">{shown.map(entity => <li key={entity.entity_id}><label className="flex cursor-pointer items-start gap-3 px-3 py-3 hover:bg-raised"><input type="radio" name="ha-device" aria-label={entity.display_name} checked={selected?.entity_id === entity.entity_id} onChange={() => pick(entity)} className="mt-1" /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium" title={entity.entity_id}>{entity.display_name}</span><span className="mt-1 block text-xs text-muted">{deviceValue(entity)} · {entity.area_name || t('alpha7.device.noArea')}</span></span></label></li>)}</ul>
        {!ha.isPending && !ha.isError && shown.length === 0 && <p className="text-sm text-muted">{t('alpha7.device.noMatches')}</p>}
        <p className="text-xs text-muted">{t('alpha7.device.previewScope')}</p>
      </div>
      <div className="space-y-4">
        {!selected ? <p className="py-5 text-sm text-muted">{t('alpha7.device.selectFirst')}</p> : <>
          <Field label={t('alpha7.device.name')}>{props => <Input {...props} maxLength={128} value={name} onChange={event => setName(event.target.value)} />}</Field>
          {contract === 'new' && <CategorySelect value={category} onChange={value => setCategory(value || 'other')} />}
          <Field label={t('b4.confirmKind')}>{props => <Select {...props} value={preset} onChange={event => { const next = event.target.value as DevicePreset; setPreset(next); setSharedTemplate(''); setFreshness(freshnessPreset(next)); }}>{DEVICE_PRESETS.map(value => <option key={value} value={value}>{t(`alpha7.device.preset.${value}`)}</option>)}</Select>}</Field>
          <Field label={t('b26.freshness.selection')}>{props => <Select {...props} value={sharedTemplate} onChange={event => { const id = event.target.value; setSharedTemplate(id); if (id) setFreshness(asObject(asArray(doc.config?.freshness_templates).filter(isObject).find(row => row.template_id === id)?.rule)); }}><option value="">{t('b4.own')}</option>{asArray(doc.config?.freshness_templates).filter(isObject).filter(row => row.sensor_kind === preset).map(row => <option key={str(row.template_id)} value={str(row.template_id)}>{str(row.display_name)}</option>)}</Select>}</Field>
          {sharedTemplate ? <p>{t('b4.uses')} {sharedTemplate}</p> : <FreshnessSettings value={freshness} onChange={setFreshness} livenessSources={liveness} />}
          {sharedTemplate && freshness.mode === 'liveness_source' && <Field label={t('alpha7.freshness.liveness')}>{props => <Select {...props} value={str(freshness.liveness_source)} onChange={event => setFreshness(previous => ({ ...previous, liveness_source: event.target.value }))}><option value="">{t('alpha10.transfer.decide')}</option>{liveness.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</Select>}</Field>}
          {preset === 'contact' && <p className="text-sm text-muted">{t('alpha7.device.contactHelp')}</p>}
          <p className="text-sm text-muted">{t(contract === 'new' ? 'alpha7.device.willCreateContract' : 'alpha7.device.willCreate')}</p>
          {existingSource && <p className="text-sm text-muted">{t('alpha7.device.reused')}</p>}
          <details><summary className="cursor-pointer text-sm text-muted">{t('alpha7.device.advanced')}</summary><div className="mt-3 space-y-3">
            <p className="break-all text-xs text-muted">{selected.entity_id}</p>
            <Field label={t('alpha7.device.type')}>{props => <Select {...props} disabled={!!existingSource} value={fieldType} onChange={event => setFieldType(event.target.value as typeof fieldType)}><option value="number">{codeLabel('number')}</option><option value="bool">{codeLabel('bool')}</option><option value="text">{codeLabel('text')}</option></Select>}</Field>
            {existingSource ? <p className="text-xs text-muted">{t('alpha7.device.existingTypeLocked')}</p> : <Field label={t('alpha7.device.sourceId')}>{props => <Input {...props} maxLength={128} placeholder={suggestions?.sourceId} value={sourceId} onChange={event => setSourceId(event.target.value)} />}</Field>}
            {contract === 'new' && <Field label={t('alpha7.device.contractId')}>{props => <Input {...props} maxLength={128} placeholder={suggestions?.contractId} value={contractId} onChange={event => setContractId(event.target.value)} />}</Field>}
            <p className="text-xs text-muted">{t('alpha7.device.idsAutomatic')}</p>
          </div></details>
        </>}
        {error && <p role="alert" className="text-sm text-bad">{error}</p>}
      </div>
    </div>
  </Dialog>;
}
