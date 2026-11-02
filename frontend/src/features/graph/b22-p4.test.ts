import { expect, it } from 'vitest';
import type { JsonObject } from '@/lib/json';
import { buildGraph, dependencyChain, nodeId, reach } from './buildGraph';

export const chainConfig: JsonObject = {
  config_schema_version: 1,
  dependencies: [], definitions: [], bindings: [], catalogs: [],
  sources: [
    { source_id: 'source.synthetic_calendar', display_name: 'Synthetischer Kalender', freshness: { mode: 'event_stateful' } },
    { source_id: 'source.synthetic_clock', display_name: 'Synthetische Uhr', freshness: { mode: 'event_stateful' } },
    { source_id: 'source.synthetic_other', display_name: 'Synthetische andere Quelle', freshness: { mode: 'event_stateful' } },
    { source_id: 'source.synthetic_unused', display_name: 'Synthetische unbenutzte Quelle', freshness: { mode: 'event_stateful' } },
  ],
  contracts: [
    { contract_id: 'synthetic.date', display_name: 'Synthetisches Datum', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'clock', ref: 'source.synthetic_clock' }], parameters: {} },
    { contract_id: 'synthetic.holiday.tomorrow', display_name: 'Feiertag morgen', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'calendar', ref: 'source.synthetic_calendar' }, { name: 'date', ref: 'synthetic.date.value' }], parameters: {} },
    { contract_id: 'synthetic.calendar.sibling', display_name: 'Synthetischer fremder Seitenzweig', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'calendar', ref: 'source.synthetic_calendar' }], parameters: {} },
    { contract_id: 'synthetic.wake', display_name: 'Synthetische Weckentscheidung', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'holiday', ref: 'synthetic.holiday.tomorrow.value' }, { name: 'other', ref: 'source.synthetic_other' }], parameters: {} },
    { contract_id: 'synthetic.public', display_name: 'Synthetische öffentliche Sicht', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'wake', ref: 'synthetic.wake' }], parameters: {} },
    { contract_id: 'synthetic.isolated', display_name: 'Synthetischer einzelner Vertrag', type_id: 'test.echo', type_version: 1, enabled: false, inputs: [], parameters: {} },
  ],
};

const ids = (graph: ReturnType<typeof buildGraph>) => graph.nodes.map(node => node.id).sort();
const holiday = nodeId('contract', 'synthetic.holiday.tomorrow');

it('shows exactly the directed predecessor and successor chain of Feiertag morgen', () => {
  const full = buildGraph(chainConfig);
  const graph = dependencyChain(full, holiday);
  expect(ids(graph)).toEqual([
    'source:source.synthetic_calendar', 'source:source.synthetic_clock',
    'contract:synthetic.date', 'result:synthetic.date.value', holiday,
    'result:synthetic.holiday.tomorrow.value', 'contract:synthetic.wake', 'contract:synthetic.public',
  ].sort());
  expect(graph.edges.map(edge => [edge.source, edge.target])).toEqual([
    ['source:source.synthetic_clock', 'contract:synthetic.date'],
    ['source:source.synthetic_calendar', holiday],
    ['contract:synthetic.date', 'result:synthetic.date.value'],
    ['result:synthetic.date.value', holiday],
    [holiday, 'result:synthetic.holiday.tomorrow.value'],
    ['result:synthetic.holiday.tomorrow.value', 'contract:synthetic.wake'],
    ['contract:synthetic.wake', 'contract:synthetic.public'],
  ]);
  // No undirected turn at the shared calendar or at a consuming contract.
  expect(ids(graph)).not.toContain('contract:synthetic.calendar.sibling');
  expect(ids(graph)).not.toContain('source:source.synthetic_other');
  expect(dependencyChain(full, null)).toEqual({ nodes: [], edges: [] });
  expect(dependencyChain(full, 'contract:synthetic.missing')).toEqual({ nodes: [], edges: [] });
  expect(full.nodes).toHaveLength(12);
});

it('starts at a source without pulling in the other inputs of its consumers', () => {
  const graph = dependencyChain(buildGraph(chainConfig), 'source:source.synthetic_calendar');
  expect(ids(graph)).toEqual([
    'source:source.synthetic_calendar', holiday, 'result:synthetic.holiday.tomorrow.value',
    'contract:synthetic.calendar.sibling', 'contract:synthetic.wake', 'contract:synthetic.public',
  ].sort());
  expect(graph.edges).toHaveLength(5);
});

it('retains a chosen isolated disabled contract and never falls back to the complete graph', () => {
  const graph = dependencyChain(buildGraph(chainConfig), 'contract:synthetic.isolated');
  expect(graph.nodes).toEqual([expect.objectContaining({ id: 'contract:synthetic.isolated', enabled: false })]);
  expect(graph.edges).toEqual([]);
});

it('includes source freshness monitors only when requested, still excluding unrelated branches', () => {
  const config = structuredClone(chainConfig);
  (config.sources as JsonObject[])[0]!.freshness = { mode: 'liveness_source', liveness_source: 'source.synthetic_unused', interval_s: 10 };
  expect(ids(dependencyChain(buildGraph(config), holiday))).not.toContain('source:source.synthetic_unused');
  const structural = dependencyChain(buildGraph(config, { structural: true }), holiday);
  expect(ids(structural)).toContain('source:source.synthetic_unused');
  expect(ids(structural)).not.toContain('contract:synthetic.calendar.sibling');
  expect(structural.edges.filter(edge => edge.kind === 'liveness')).toHaveLength(1);
});

it('terminates for an invalid cyclic draft and does not list the start as its own ancestor', () => {
  const config = structuredClone(chainConfig);
  (config.contracts as JsonObject[])[0]!.inputs = [{ name: 'cycle', ref: 'synthetic.public' }];
  const graph = buildGraph(config);
  const reached = reach(graph, holiday);
  expect(reached.upstream).not.toContain(holiday);
  expect(reached.downstream).not.toContain(holiday);
  expect(ids(dependencyChain(graph, holiday))).not.toContain('contract:synthetic.calendar.sibling');
});
