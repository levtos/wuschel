import { t as uiText } from '@/i18n';
import { fieldLabel } from '@/i18n/codes';
import { isObject, type JsonObject } from '@/lib/json';
import { asArray, str } from '@/features/authoring/model';
import { resolveRef } from '@/features/authoring/usage';

/**
 * Dependency graph derived from a registry document: sources feed contracts,
 * contracts feed other contracts directly or through a derived result
 * (`contract.field`). Display only; the server owns cycle/type validation.
 */
export type GraphNodeKind = 'source' | 'contract' | 'result';

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  detail: string;
  /** Index into the document collection (for deep links). */
  index?: number;
  enabled?: boolean;
  declarative?: boolean;
  contractId?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  kind: 'input' | 'liveness' | 'dependency';
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export const nodeId = (kind: GraphNodeKind, id: string): string => `${kind}:${id}`;

export function buildGraph(config: JsonObject | null | undefined, options: { structural?: boolean } = {}): Graph {
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const definitions = new Set(asArray(config?.definitions).filter(isObject).map((d) => `${str(d.type_id)}@${String(d.type_version)}`));

  asArray(config?.sources).filter(isObject).forEach((source, index) => {
    const id = str(source.source_id);
    nodes.set(nodeId('source', id), { id: nodeId('source', id), kind: 'source', label: str(source.display_name) || id, detail: uiText('alpha7.graph.sourceDetail'), index });
  });
  asArray(config?.contracts).filter(isObject).forEach((contract, index) => {
    const id = str(contract.contract_id);
    nodes.set(nodeId('contract', id), {
      id: nodeId('contract', id),
      kind: 'contract',
      label: str(contract.display_name) || id,
      detail: uiText('alpha7.graph.templateVersion', { n: String(contract.type_version) }),
      index,
      enabled: contract.enabled !== false,
      declarative: definitions.has(`${str(contract.type_id)}@${String(contract.type_version)}`),
      contractId: id,
    });
  });

  for (const contract of asArray(config?.contracts).filter(isObject)) {
    const to = nodeId('contract', str(contract.contract_id));
    for (const input of asArray(contract.inputs).filter(isObject)) {
      const target = resolveRef(str(input.ref), config);
      if (!target) continue;
      if (target.kind === 'source') {
        edges.push({ id: `${nodeId('source', target.id)}->${to}:${str(input.name)}`, source: nodeId('source', target.id), target: to, label: str(input.name), kind: 'input' });
      } else if (target.field) {
        const resultId = nodeId('result', `${target.id}.${target.field}`);
        if (!nodes.has(resultId)) nodes.set(resultId, { id: resultId, kind: 'result', label: fieldLabel(target.field), detail: uiText("features.graph.buildGraph.ergebnis_von_v0_b655b9", { v0: (nodes.get(nodeId('contract', target.id))?.label ?? target.id) }), contractId: target.id });
        const from = nodeId('contract', target.id);
        const produce = `${from}->${resultId}`;
        if (!edges.some((e) => e.id === produce)) edges.push({ id: produce, source: from, target: resultId, kind: 'input' });
        edges.push({ id: `${resultId}->${to}:${str(input.name)}`, source: resultId, target: to, label: str(input.name), kind: 'input' });
      } else {
        edges.push({ id: `${nodeId('contract', target.id)}->${to}:${str(input.name)}`, source: nodeId('contract', target.id), target: to, label: str(input.name), kind: 'input' });
      }
    }
  }

  if (options.structural) {
    const dependencies = new Map(asArray(config?.dependencies).filter(isObject).map((d) => [str(d.dependency_id), d]));
    for (const source of asArray(config?.sources).filter(isObject)) {
      const id = nodeId('source', str(source.source_id));
      const live = str(effectiveRule(config ?? {}, source).liveness_source);
      if (live && nodes.has(nodeId('source', live))) edges.push({ id: `${nodeId('source', live)}~>${id}`, source: nodeId('source', live), target: id, label: uiText("features.graph.buildGraph.liveness_3f256d"), kind: 'liveness' });
      const monitor = str(dependencies.get(str(source.dependency_id))?.source_id);
      if (monitor && nodes.has(nodeId('source', monitor)) && monitor !== str(source.source_id)) edges.push({ id: `${nodeId('source', monitor)}~dep>${id}`, source: nodeId('source', monitor), target: id, label: uiText("features.graph.buildGraph.dependency_711595"), kind: 'dependency' });
    }
  }
  return { nodes: [...nodes.values()], edges };
}

/** Nodes within `depth` hops of `center` (both directions). */
export function neighborhood(graph: Graph, center: string, depth: number): Graph {
  const keep = new Set([center]);
  let frontier = [center];
  for (let i = 0; i < depth; i += 1) {
    const next: string[] = [];
    for (const edge of graph.edges) {
      if (frontier.includes(edge.source) && !keep.has(edge.target)) next.push(edge.target);
      if (frontier.includes(edge.target) && !keep.has(edge.source)) next.push(edge.source);
    }
    next.forEach((id) => keep.add(id));
    frontier = next;
  }
  return { nodes: graph.nodes.filter((n) => keep.has(n.id)), edges: graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) };
}

/** Upstream (what it depends on) and downstream (what depends on it), transitive. */
export function reach(graph: Graph, id: string): { upstream: string[]; downstream: string[] } {
  const walk = (start: string, direction: 'up' | 'down'): string[] => {
    const seen = new Set<string>([start]);
    const stack = [start];
    while (stack.length) {
      const current = stack.pop()!;
      for (const edge of graph.edges) {
        const [from, to] = direction === 'up' ? [edge.target, edge.source] : [edge.source, edge.target];
        if (from === current && !seen.has(to)) {
          seen.add(to);
          stack.push(to);
        }
      }
    }
    return [...seen].filter(node => node !== start);
  };
  return { upstream: walk(id, 'up'), downstream: walk(id, 'down') };
}

/** Only directed ancestors and descendants of the chosen start, never sibling
 * branches reached by turning around at a shared ancestor or consumer. */
export function dependencyChain(graph: Graph, start: string | null): Graph {
  if (!start || !graph.nodes.some(node => node.id === start)) return { nodes: [], edges: [] };
  const { upstream, downstream } = reach(graph, start);
  const keep = new Set([start, ...upstream, ...downstream]);
  return { nodes: graph.nodes.filter(node => keep.has(node.id)), edges: graph.edges.filter(edge => keep.has(edge.source) && keep.has(edge.target)) };
}
import { effectiveRule } from '@/features/sources/freshness';
