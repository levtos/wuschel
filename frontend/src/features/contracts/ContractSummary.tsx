import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { t } from '@/i18n';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { ContractLiveState } from '@/components/ContractLiveState';
import { CommandsPanel } from '@/features/diagnostics/CommandsPanel';
import { useHaCatalog } from '@/features/sources/useHaCatalog';
import { asArray, asObject, str, type FieldSchema } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import { formatDuration, formatTime } from '@/lib/format';
import { api } from '@/lib/api';
import { POLL_MS } from '@/lib/queries';
import { ContractHistory } from './ContractHistory';
import type { ContractDiagnosis, Envelope, Registry } from '@/lib/types';
import { problemAdvice } from '@/features/overview/overviewModel';
import { affectedContractInputs, contractSourcePaths, contractSources, contractState, contractTitle } from './presentation';
import { reasonLabel } from '@/i18n/codes';
import { ContractStateChip } from './ContractStateChip';
import { MachineRestartPanel } from './MachineRestartPanel';
import { ManualSourcePanel } from '@/features/sources/ManualSourcePanel';

export { historyPoints } from './historyModel';
export { MiniHistory } from './MiniHistory';

/** Measurement clock from stored observations, never the receipt time or reason onset. */
export function observationTime(observation: JsonObject): string | undefined {
  const haTime = observation.observation_kind === 'report' ? observation.ha_last_reported : observation.observation_kind === 'live_change' ? observation.ha_time_fired || observation.ha_last_changed : observation.ha_last_reported || observation.ha_last_changed || observation.ha_time_fired;
  const value = str(observation.device_time || haTime);
  return value && Number.isFinite(Date.parse(value)) ? value : undefined;
}

import { CurrentValues } from './CurrentValues';
import { InputOrigins } from './InputOrigins';
import { contractDefinition } from './inputPresentation';
import { consumersOfContract } from '@/features/authoring/usage';

export function ContractSummary({ envelope, stale, fields, config, machine }: { envelope: Envelope; stale: boolean; fields: Record<string, FieldSchema>; config: Registry | null; machine: boolean }) {
  const [advanced, setAdvanced] = useState(false);
  const ha = useHaCatalog(asArray(config?.bindings).filter(isObject).some(binding => isObject(binding.adapter) && String(binding.adapter.kind).startsWith('ha_')));
  const sources = contractSources(envelope.contract_id, config, ha.entities);
  const paths = contractSourcePaths(envelope.contract_id, config, ha.entities);
  const problems = affectedContractInputs(envelope, config, ha.entities);
  const indirectManual = [...new Map(paths.filter(path => path.owner !== envelope.contract_id && asArray(config?.sources).map(asObject)[path.source.sourceIndex]?.origin === 'manual').map(path => [`${path.owner}:${path.source.id}`, path])).values()];
  const facts = useQuery({ queryKey: ['contract-inputs', envelope.contract_id, envelope.registry_revision, envelope.epoch_id, envelope.publication_seq], queryFn: () => api<ContractDiagnosis>(`diagnostics/contracts/${encodeURIComponent(envelope.contract_id)}?include_inputs=1`), enabled: sources.length > 0 && !stale, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const inputs = !stale && facts.data?.registry_revision === envelope.registry_revision && facts.data.input_epoch_id === envelope.epoch_id ? facts.data.inputs ?? {} : {};
  const names = Object.fromEntries(sources.map(source => [source.inputName, source.name]));
  const definition = contractDefinition(envelope, config);
  const internal = (contract: JsonObject | undefined) => contract?.type_id === 'sleep.flow' || /\(intern\)/i.test(str(contract?.display_name));
  const currentContract = asArray(config?.contracts).filter(isObject).find(contract => contract.contract_id === envelope.contract_id);
  const consumers = consumersOfContract(config, envelope.contract_id).map(id => asArray(config?.contracts).filter(isObject).find(contract => contract.contract_id === id)).filter((contract): contract is JsonObject => !!contract && !internal(contract));
  const reasons = Object.values(envelope.fields).flatMap(field => field.reasons);
  const affected = problems.find(source => source.kind === 'source');
  const affectedName = affected ? str(asArray(config?.sources).filter(isObject)[affected.sourceIndex]?.display_name) || affected.name : '';
  const evidence = useQuery({ queryKey: ['evidence', 'source', affected?.id, envelope.registry_revision], queryFn: () => api<JsonObject[]>(`evidence/${encodeURIComponent(affected!.id)}`), enabled: !!affected, refetchInterval: POLL_MS, retry: false });
  const measured = (evidence.isPlaceholderData ? [] : evidence.data ?? []).map(observationTime).filter((at): at is string => !!at).sort().at(-1);
  const age = measured && Date.parse(measured) <= Date.now() ? formatDuration((Date.now() - Date.parse(measured)) / 1000) : null;
  const why = stale ? t('alpha7.contract.state.oldConnectionHelp') : reasons.length ? problemAdvice(reasons, config, envelope.contract_id).text : contractState(envelope, true).hint;
  return <div className="space-y-4">
    {[...new Set(reasons.filter(reason => reason.code === 'calendar_coverage_missing').map(reason => reason.detail).filter(Boolean))].map(message => <p key={message} role="status" className="rounded-xl border border-warn p-4 text-sm">{message}</p>)}
    {Object.values(envelope.fields).some(field => field.configuration?.complete === false) && <Panel className="p-5"><h3 className="font-medium">{t('alpha8.configuration.title')}</h3><p className="text-sm text-muted">{t('alpha8.configuration.validHint')}</p><ul className="mt-2 text-sm">{Object.entries(envelope.fields).flatMap(([name, field]) => (field.configuration?.hints ?? []).map((hint, index) => <li key={`${name}:${index}`}>{hint.message || t('alpha8.configuration.unassigned')}</li>))}</ul></Panel>}
    <Panel><PanelHeader title={t('alpha7.contract.now')} aside={<ContractStateChip envelope={envelope} enabled={envelope.enabled} stale={stale} />} />
      <CurrentValues envelope={envelope} fields={fields} config={config} inputs={inputs} names={names} attribution={sources.some(source => !!source.entityId)} />
      <p className="px-6 pb-4 text-xs text-muted">{t('alpha7.contract.liveVersion', { version: envelope.registry_revision, time: formatTime(envelope.published_at) })}</p>
      <p className="px-6 pb-4 text-sm">{t('beta16.inputs.reliability', { state: contractState(envelope, true).label })}</p>
    </Panel>
    {internal(currentContract) && <Panel className="p-5"><p>{t('b22.output.internalFeeds')}</p>{consumers.length ? <ul>{consumers.map(contract => <li key={str(contract.contract_id)}><Link className="underline" to={`/contracts/view/${encodeURIComponent(str(contract.contract_id))}`}>{contractTitle(contract)}</Link></li>)}</ul> : <p>{t('b22.output.noConsumers')}</p>}</Panel>}
    <InputOrigins envelope={envelope} config={config} sources={sources} inputs={inputs} why={why} rule={str(definition.description)}>
      {affected && <p className="text-sm">{t(age ? 'post1.why.inputAge' : 'post1.why.inputAgeUnknown', { name: affectedName, age })}</p>}
      {problems.length > 0 && <ul className="text-sm">{[...new Map(problems.map(input => [`${input.kind}:${input.id}`, input])).values()].map(input => <li key={`${input.kind}:${input.id}`}>{t('beta16.inputs.affected')} <Link to={input.href} className="underline">{input.name}</Link>: {[...new Set(problems.filter(row => row.kind === input.kind && row.id === input.id).flatMap(row => row.reasons.map(reason => reasonLabel(reason.code))))].join(' · ')}</li>)}</ul>}
      {ha.isError && <p className="text-xs text-muted">{t('alpha7.contract.namesUnavailable')}</p>}
    </InputOrigins>
    <ContractHistory key={envelope.contract_id} envelope={envelope} fields={fields} stale={stale} />
    {[...new Map(sources.filter(source => source.kind === 'source' && asArray(config?.sources).map(asObject)[source.sourceIndex]?.origin === 'manual').map(source => [source.id, source])).values()].map(source => <ManualSourcePanel key={source.id} sourceId={source.id} />)}
    {indirectManual.map(path => <p key={`${path.owner}:${path.source.id}`} className="text-sm text-muted">{t('beta16.inputs.manualAt', { name: path.source.name })} <Link className="underline" to={`/contracts/view/${encodeURIComponent(path.owner)}`}>{contractTitle(asArray(config?.contracts).filter(isObject).find(item => item.contract_id === path.owner), path.owner)}</Link></p>)}
    {machine && <CommandsPanel contractId={envelope.contract_id} onContract={() => {}} fixed />}
    {machine && <MachineRestartPanel key={envelope.contract_id} contractId={envelope.contract_id} />}
    <details onToggle={event => setAdvanced(event.currentTarget.open)}><summary className="cursor-pointer py-2 text-sm text-muted">{t('alpha7.contract.advanced')}</summary>{advanced && <><h3 className="text-sm font-medium">{t('beta16.inputs.paths')}</h3><ul className="mb-4 text-sm">{paths.map(path => <li key={path.ids.join('>')}>{path.path.join(' → ')} <code className="text-xs text-muted">{path.ids.join(' → ')}</code></li>)}</ul><ContractLiveState envelope={envelope} stale={stale} fields={fields} /></>}</details>
  </div>;
}
