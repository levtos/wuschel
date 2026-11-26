import { t } from '@/i18n';
import { asArray, asObject, str } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import type { BrowserConnectionEvent } from '@/lib/live';
import type { Envelope, HistoryItem, Reason, Registry } from '@/lib/types';
import { contractState, contractTitle } from '@/features/contracts/presentation';
import { sourceHref } from '@/features/authoring/ObjectReadLinks';

export function contractCounts(config: Registry | null | undefined, envelopes: Record<string, Envelope> | null) {
  const contracts = asArray(config?.contracts).filter(isObject);
  const enabled = contracts.filter(contract => contract.enabled !== false);
  return {
    active: enabled.length,
    healthy: envelopes === null ? null : enabled.filter(contract => contractState(envelopes[str(contract.contract_id)] ?? null, true).category === 'healthy').length,
    degraded: envelopes === null ? null : enabled.filter(contract => contractState(envelopes[str(contract.contract_id)] ?? null, true).category === 'degraded').length,
    off: contracts.length - enabled.length,
  };
}

export function contractName(config: Registry | null | undefined, id: string): string {
  const contract = asArray(config?.contracts).filter(isObject).find(item => item.contract_id === id);
  return contractTitle(contract, id);
}

export function problemAdvice(reasons: Reason[], config: Registry | null | undefined, contractId: string) {
  const codes = new Set(reasons.map(reason => reason.code));
  const ids = new Set(asArray(config?.sources).filter(isObject).map(item => str(item.source_id)));
  const source = reasons.flatMap(reason => [reason.source_id, reason.input]).find(id => id && ids.has(id));
  const sourceLink = source ? sourceHref(source) : '/sources/devices';
  const calendar = reasons.find(reason => reason.code === 'calendar_coverage_missing');
  if (calendar) {
    const calendarSource = [calendar.source_id, calendar.input].find(id => id && ids.has(id));
    return { text: calendar.detail || t('codes.calendar_coverage_missing'), advice: t('a2.calendar.advice'), to: calendarSource ? sourceHref(calendarSource) : sourceLink, action: t('a2.read.openCalendar') };
  }
  if (codes.has('evaluation_error') || codes.has('invalid_value')) return { text: t('alpha7.problem.invalid'), advice: t('alpha7.problem.invalidHelp'), to: `/contracts/view/${encodeURIComponent(contractId)}?tab=config`, action: t('alpha7.problem.inputs') };
  if (codes.has('manual_restart')) return { text: t('alpha7.restart.marker'), advice: t('alpha7.restart.markerHelp'), to: `/contracts/view/${encodeURIComponent(contractId)}`, action: t('alpha7.problem.contract') };
  if ([...codes].some(code => code.startsWith('restore_context_')) || codes.has('state_unknown')) return { text: t('alpha7.problem.machine'), advice: t('alpha7.problem.machineHelp'), to: `/contracts/view/${encodeURIComponent(contractId)}`, action: t('alpha7.problem.contract') };
  if (codes.has('input_stale')) return { text: t('alpha7.problem.stale'), advice: t('alpha7.problem.staleHelp'), to: sourceLink, action: t('alpha7.problem.source') };
  if (codes.has('dependency_unavailable') || codes.has('input_unavailable')) return { text: t('alpha7.problem.unavailable'), advice: t('alpha7.problem.connectionHelp'), to: '/settings/connections', action: t('alpha7.problem.connection') };
  if (codes.has('partial_evidence')) return { text: t('alpha7.problem.partial'), advice: t('alpha7.problem.partialHelp'), to: sourceLink, action: t('alpha7.problem.source') };
  return { text: t('alpha7.problem.unknown'), advice: t('alpha7.problem.unknownHelp'), to: sourceLink, action: t('alpha7.problem.source') };
}

export interface OverviewEvent {
  id: string;
  at: string;
  kind: 'activate' | 'rollback' | 'contract_stale' | 'contract_error' | 'contract_recovered' | 'source_error' | 'source_recovered' | 'ha_connected' | 'ha_disconnected';
  revision?: number;
  contractId?: string;
  sourceId?: string;
  session?: boolean;
}

export function overviewEvents(activations: HistoryItem[], history: HistoryItem[], connections: BrowserConnectionEvent[], observations: HistoryItem[] = []): OverviewEvent[] {
  const events: OverviewEvent[] = [];
  for (const item of activations) if ((item.operation === 'activate' || item.operation === 'rollback') && typeof item.at === 'string' && typeof item.revision === 'number') events.push({ id: `activation-${item.id}`, at: item.at, kind: item.operation, revision: item.revision });

  const previous = new Map<string, string>();
  for (const item of [...history].sort((a, b) => Date.parse(str(a.published_at)) - Date.parse(str(b.published_at)))) {
    if (typeof item.contract_id !== 'string' || typeof item.published_at !== 'string' || !Number.isFinite(Date.parse(item.published_at))) continue;
    const fields = Object.values(asObject(item.fields)).filter(isObject);
    const reasons = fields.flatMap(field => asArray(field.reasons).filter(isObject));
    const codes = reasons.map(reason => str(reason.code));
    const kind = codes.includes('input_stale') ? 'contract_stale' : item.contract_health === 'degraded' || codes.some(code => ['evaluation_error', 'invalid_value', 'config_incomplete'].includes(code)) ? 'contract_error' : null;
    const prior = previous.get(item.contract_id);
    if (kind && prior !== kind) {
      const onset = reasons.filter(reason => kind === 'contract_stale' ? reason.code === 'input_stale' : reason.code !== 'input_stale')
        .map(reason => str(reason.since)).filter(at => Number.isFinite(Date.parse(at))).sort()[0];
      events.push({ id: `contract-${item.id}`, at: onset ?? item.published_at, kind, contractId: item.contract_id });
    } else if (!kind && prior && item.contract_health === 'healthy') {
      events.push({ id: `contract-${item.id}`, at: item.published_at, kind: 'contract_recovered', contractId: item.contract_id });
    }
    if (kind) previous.set(item.contract_id, kind);
    else if (item.contract_health === 'healthy') previous.delete(item.contract_id);
  }

  const disturbed = new Map<string, Set<string>>();
  for (const item of [...observations].sort((a, b) => Date.parse(str(a.received_at)) - Date.parse(str(b.received_at)))) {
    if (typeof item.source_id !== 'string' || typeof item.binding_id !== 'string' || typeof item.received_at !== 'string' || !Number.isFinite(Date.parse(item.received_at))) continue;
    const bindings = disturbed.get(item.source_id) ?? new Set<string>();
    const at = item.received_at;
    const event = { id: `source-${item.id}`, at, sourceId: item.source_id };
    if (['unavailable', 'unknown', 'absent'].includes(str(item.availability)) || typeof item.transform_error === 'string' || item.invalid_timestamp === true) {
      if (!bindings.size) events.push({ ...event, kind: 'source_error' });
      bindings.add(item.binding_id);
      disturbed.set(item.source_id, bindings);
    } else if (item.availability === 'available' && bindings.delete(item.binding_id) && !bindings.size) {
      events.push({ ...event, kind: 'source_recovered' });
      disturbed.delete(item.source_id);
    }
  }
  events.push(...connections.map(event => ({ ...event, session: true })));
  return events.filter(event => Number.isFinite(Date.parse(event.at))).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

export function eventText(event: OverviewEvent, config: JsonObject | null | undefined) {
  const source = asArray(config?.sources).filter(isObject).find(item => item.source_id === event.sourceId);
  const name = event.contractId ? contractName(config, event.contractId) : str(source?.display_name) || event.sourceId || '';
  return t(`alpha7.event.${event.kind}`, { name, version: event.revision });
}
