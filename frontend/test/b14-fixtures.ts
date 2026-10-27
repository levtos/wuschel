import type { Json, JsonObject } from '../src/lib/json';

export const episodeId = '11111111-1111-4111-8111-111111111111';
export const stamp = '2030-01-01T08:00:00Z';
const field = (type: string, value: Json, extra: JsonObject = {}) => ({ schema: { type, required: true, physical_state: false, ...extra }, value });
const sleep = {
  value: field('enum', 'pre_sleep', { value_catalog: ['awake', 'pre_sleep', 'sleep', 'waking'] }),
  entry_origin: field('text', 'initial'),
  last_action_origin: field('text', 'confirmed'),
  episode_id: field('text', episodeId),
  episode_active: field('bool', true),
  sleep_context_active: field('bool', true),
  deadline_at: field('timestamp', stamp),
};
const wake = {
  status: field('text', 'scheduled'), next_wake_start: field('timestamp', stamp),
  plan_date: field('text', '2030-01-01'), window_start: field('timestamp', stamp),
  window_end: field('timestamp', '2030-01-01T08:30:00Z'),
  decision: field('text', 'rule_wake'), minimum_sleep_applied: field('bool', false),
};
const definition = (type_id: string, values: Record<string, ReturnType<typeof field>>) => ({
  type_id, type_version: 1, language_version: 5, inputs: [],
  schema: { fields: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value.schema])), available_projection: Object.keys(values) },
  outputs: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, { op: 'literal', ...value }])),
});
export const sleepRegistry: JsonObject = {
  config_schema_version: 4, fingerprint_version: 4, dependencies: [], sources: [], bindings: [], catalogs: [],
  definitions: [definition('sleep.flow', sleep), definition('sleep.state_view', sleep), definition('wake.plan_view', wake)],
  contracts: [
    { contract_id: 'synthetic.b14.internal', type_id: 'sleep.flow', type_version: 1, display_name: 'Interner Beispielablauf', enabled: true, inputs: [], parameters: {} },
    { contract_id: 'synthetic.b14.state', type_id: 'sleep.state_view', type_version: 1, display_name: 'Schlafzustand', enabled: true, inputs: [], parameters: {} },
    { contract_id: 'synthetic.b14.wake', type_id: 'wake.plan_view', type_version: 1, display_name: 'Weckplan', enabled: true, inputs: [], parameters: {} },
  ],
};
