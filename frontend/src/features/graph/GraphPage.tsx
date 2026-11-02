import { t as uiText } from '@/i18n';
import dagre from '@dagrejs/dagre';
import { applyNodeChanges, Background, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow, type Edge, type Node, type NodeChange, type NodeProps } from '@xyflow/react';
import { Cable, FileCode2, Sigma } from 'lucide-react';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { FieldStatusBadge, QualityBadge } from '@/components/status-parts';
import { Badge } from '@/components/ui/badge';
import { LayoutPanel, PanelLayout } from '@/components/PanelLayout';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, Select } from '@/components/ui/form';
import { Empty, KeyValue, PageHeader, Panel, PanelHeader, Spinner } from '@/components/ui/layout';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { useLive } from '@/lib/live-context';
import { cn } from '@/lib/utils';
import type { Envelope } from '@/lib/types';
import { useActiveRevision } from '@/lib/queries';
import { contractState } from '@/features/contracts/presentation';
import { buildGraph, dependencyChain, nodeId, reach, type Graph, type GraphNode } from './buildGraph';
import { useRouteFilters } from '@/lib/route-filters';

interface NodeData extends Record<string, unknown> {
  node: GraphNode;
  envelope: Envelope | null;
  dim: boolean;
}

const WIDTH = 280;
const HEIGHT = 80;

function layout(graph: Graph): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 24, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  graph.nodes.forEach((n) => g.setNode(n.id, { width: WIDTH, height: HEIGHT }));
  graph.edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return new Map(graph.nodes.map((n) => {
    const pos = g.node(n.id);
    return [n.id, { x: (pos?.x ?? 0) - WIDTH / 2, y: (pos?.y ?? 0) - HEIGHT / 2 }];
  }));
}

/** Contract nodes show server-provided health; sources and results are structural. */
const WuschelNode = memo(function WuschelNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const { node, envelope, dim } = data;
  const state = contractState(envelope, node.enabled !== false);
  const tone = node.kind !== 'contract' ? 'border-line-strong' : ({ ok: 'border-ok/60', warn: 'border-warn/70', bad: 'border-bad/70', info: 'border-info/60', neutral: 'border-line-strong', accent: 'border-line-strong' }[state.tone]);
  const Icon = node.kind === 'source' ? Cable : node.kind === 'contract' ? FileCode2 : Sigma;
  return (
    <div
      className={cn('rounded-[var(--radius-md)] border bg-panel px-3 py-2 shadow-sm', tone, selected && 'ring-2 ring-faint', dim && 'opacity-35')}
      style={{ width: WIDTH, height: HEIGHT }}
      data-testid={`node-${node.id}`}
    >
      <Handle type="target" position={Position.Left} className="!size-2 !border-line-strong !bg-raised" />
      <div className="flex items-center gap-2">
        <Icon size={16} className="shrink-0 text-muted" />
        <span className="truncate text-sm font-medium" title={uiText('alpha7.graph.tooltip', { name: node.label, id: node.id })}>{node.label}</span>
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-xs text-muted">
        <span className="truncate">{node.detail}</span>
        {node.kind === 'contract' && node.enabled === false && <Badge>{uiText("features.graph.GraphPage.aus_2337c3")}</Badge>}
        {node.kind === 'contract' && envelope && <Badge tone={state.tone}>{state.label}</Badge>}
      </div>
      <Handle type="source" position={Position.Right} className="!size-2 !border-line-strong !bg-raised" />
    </div>
  );
});
const nodeTypes = { terra: WuschelNode };

function Canvas({ graph, envelopes, selected, onSelect, focus }: { graph: Graph; envelopes: Record<string, Envelope> | null; selected: string | null; onSelect: (id: string | null) => void; focus: string | null }) {
  const flow = useReactFlow();
  const positions = useMemo(() => layout(graph), [graph]);
  const highlighted = useMemo(() => {
    if (!selected) return null;
    const r = reach(graph, selected);
    return new Set([selected, ...r.upstream, ...r.downstream]);
  }, [graph, selected]);

  const desired: Node<NodeData>[] = useMemo(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: 'terra',
        position: positions.get(node.id) ?? { x: 0, y: 0 },
        // Fixed card size: nodes are usable (and fitView works) before any DOM measurement.
        width: WIDTH,
        height: HEIGHT,
        selected: node.id === selected,
        data: { node, envelope: node.contractId && node.kind === 'contract' ? (envelopes?.[node.contractId] ?? null) : null, dim: highlighted !== null && !highlighted.has(node.id) },
      })),
    [graph, positions, selected, envelopes, highlighted],
  );
  // React Flow reports node sizes as changes; a controlled canvas must apply them or nothing renders.
  const [nodes, setNodes] = useState<Node<NodeData>[]>(desired);
  useEffect(() => {
    setNodes((previous) => desired.map((node) => ({ ...node, measured: previous.find((p) => p.id === node.id)?.measured })));
  }, [desired]);
  const onNodesChange = useCallback((changes: NodeChange<Node<NodeData>>[]) => setNodes((current) => applyNodeChanges(changes, current)), []);
  const edges: Edge[] = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    type: 'smoothstep',
    animated: false,
    style: edge.kind === 'input' ? undefined : { strokeDasharray: '5 4' },
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    labelStyle: { fontSize: 13, fill: 'var(--color-muted)' },
    labelBgStyle: { fill: 'var(--color-panel)' },
  }));

  useEffect(() => {
    if (focus) void flow.fitView({ nodes: [{ id: focus }], duration: 300, maxZoom: 1.2 });
  }, [focus, flow]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onInit={(instance) => { if (focus) void instance.fitView({ nodes: [{ id: focus }], maxZoom: 1.2 }); else void instance.setViewport({ x: 16, y: 16, zoom: 1 }); }}
      minZoom={0.65}
      maxZoom={1.8}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect(null)}
      proOptions={{ hideAttribution: true }}
      aria-label={uiText("features.graph.GraphPage.abhangigkeitsgraph_52b36e")}
    >
      <Background gap={24} color="var(--color-line)" />
      <div className="absolute bottom-3 left-3 z-10 flex gap-2 rounded-lg border border-line bg-panel p-2" role="group" aria-label={uiText('alpha7.graph.view')}>
        <Button size="sm" onClick={() => void flow.zoomIn()}>{uiText('alpha7.graph.zoomIn')}</Button>
        <Button size="sm" onClick={() => void flow.zoomOut()}>{uiText('alpha7.graph.zoomOut')}</Button>
        <Button size="sm" onClick={() => void flow.setViewport({ x: 16, y: 16, zoom: 1 })}>{uiText('alpha7.graph.readable')}</Button>
      </div>
    </ReactFlow>
  );
}

function Details({ node, graph, envelope }: { node: GraphNode; graph: Graph; envelope: Envelope | null }) {
  const navigate = useNavigate();
  const { upstream, downstream } = reach(graph, node.id);
  const label = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  return (
    <Panel>
      <PanelHeader title={<span title={node.id}>{node.label}</span>} aside={<Badge>{node.kind === 'source' ? uiText("features.graph.GraphPage.quelle_037889") : node.kind === 'contract' ? (node.declarative ? uiText("features.graph.GraphPage.contract_deklarativ_4bb04f") : uiText("features.graph.GraphPage.contract_paket_typ_ad3d17")) : uiText("features.graph.GraphPage.abgeleitetes_ergebnis_1c7c27")}</Badge>} />
      <div className="flex flex-col gap-4 p-4 text-sm">
        <KeyValue items={[{ label: uiText("features.graph.GraphPage.details_45989d"), value: node.detail }, ...(node.kind === 'contract' ? [{ label: uiText("features.graph.GraphPage.status_920e41"), value: node.enabled === false ? uiText("features.graph.GraphPage.deaktiviert_10f33e") : uiText("features.graph.GraphPage.aktiviert_acb27b") }] : [])]} />
        {node.kind === 'contract' && envelope && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap gap-2"><QualityBadge quality={envelope.contract_health} /><Badge tone={envelope.available ? 'ok' : 'warn'}>{envelope.available ? uiText("features.graph.GraphPage.verfugbar_e5a644") : uiText("features.graph.GraphPage.nicht_verfugbar_9a98b3")}</Badge></div>
            <ul className="space-y-1 text-xs">
              {Object.entries(envelope.fields).map(([name, field]) => (
                <li key={name} className="flex items-center gap-2"><code>{name}</code><FieldStatusBadge field={field} /></li>
              ))}
            </ul>
          </div>
        )}
        {node.kind === 'contract' && !envelope && <p className="text-xs text-muted">{uiText("features.graph.GraphPage.kein_live_zustand_deaktiviert_nur_im_draft_oder__f8cc5d")}</p>}
        <div>
          <p className="mb-1 text-xs font-medium text-muted">{uiText("features.graph.GraphPage.hangt_ab_von_e6eb2f")}{upstream.length})</p>
          <p className="flex flex-wrap gap-1.5">{upstream.length ? upstream.map((id) => <Badge key={id}>{label(id)}</Badge>) : <span className="text-xs text-faint">–</span>}</p>
        </div>
        <div>
          <p className="mb-1 text-xs font-medium text-muted">{uiText("features.graph.GraphPage.wird_verwendet_von_f076e4")}{downstream.length})</p>
          <p className="flex flex-wrap gap-1.5">{downstream.length ? downstream.map((id) => <Badge key={id}>{label(id)}</Badge>) : <span className="text-xs text-faint">–</span>}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {node.kind === 'contract' && node.contractId && <Button size="sm" variant="primary" onClick={() => navigate(`/contracts/view/${encodeURIComponent(node.contractId!)}`)}>{uiText("features.graph.GraphPage.detailansicht_9d72e5")}</Button>}
          {node.kind === 'contract' && node.contractId && <Link to={`/settings/diagnostics?contract=${encodeURIComponent(node.contractId)}`}><Button size="sm">{uiText("features.graph.GraphPage.diagnose_64dd65")}</Button></Link>}
          {node.kind === 'result' && node.contractId && <Button size="sm" variant="primary" onClick={() => navigate(`/contracts/view/${encodeURIComponent(node.contractId!)}`)}>{uiText("features.graph.GraphPage.contract_offnen_019eff")}</Button>}
          {node.kind === 'source' && <Button size="sm" variant="primary" onClick={() => navigate(`/sources/devices?source_id=${encodeURIComponent(node.id.slice('source:'.length))}`)}>{uiText("features.graph.GraphPage.quelle_offnen_16c604")}</Button>}
        </div>
      </div>
    </Panel>
  );
}

export default function GraphPage() {
  const src = useAuthoringSource();
  const live = useLive();
  const active = useActiveRevision();
  const envelopes = src.source === 'active' && active.data?.revision === live.registryRevision ? live.contracts : null;
  const route = useRouteFilters();
  const [requested, setStart] = route.field('node', '');
  const [search, setSearch] = route.field('q', '');
  const [structure, setStructure] = route.field('structural', 'false');
  const structural = structure === 'true';
  const [inspected, setInspected] = useState<string | null>(null);

  const full = useMemo(() => buildGraph(src.config, { structural }), [src.config, structural]);
  const requestedNode = full.nodes.find(node => node.id === requested);
  // Old result deep links start at their producing contract; results are not
  // independent choices in the source/contract picker.
  const start = requestedNode?.kind === 'result' ? full.nodes.find(node => node.id === nodeId('contract', requestedNode.contractId ?? '')) : requestedNode;
  const graph = useMemo(() => dependencyChain(full, start?.id ?? null), [full, start?.id]);
  const selectedNode = graph.nodes.find(node => node.id === inspected) ?? start ?? null;
  const needle = search.trim().toLocaleLowerCase('de');
  const choices = full.nodes.filter(node => node.kind !== 'result' && (node.id === start?.id || `${node.label} ${node.id}`.toLocaleLowerCase('de').includes(needle))).sort((a, b) => a.label.localeCompare(b.label, 'de') || a.id.localeCompare(b.id));
  const choose = (id: string) => { setInspected(null); setStart(id); };
  useEffect(() => setInspected(null), [start?.id]);

  return (
    <>
      <PageHeader title={uiText("features.graph.GraphPage.graph_32ab01")} description={uiText('alpha9.pages.graph')} actions={<Button variant="primary" disabled={!requested && !search && !structural} onClick={() => { setInspected(null); route.update({ node: undefined, q: undefined, structural: undefined }); }}>{uiText('b22.graph.changeStart')}</Button>} />
      <p className="mb-3 flex items-center gap-2 text-sm"><Badge tone={src.source === 'draft' ? 'info' : 'neutral'}>{uiText(src.source === 'draft' ? 'alpha7.graph.draft' : 'alpha7.graph.live')}</Badge>{uiText(src.source === 'draft' ? 'alpha7.graph.draftHelp' : 'alpha7.graph.liveHelp', { n: active.data?.revision ?? '—' })}</p>
      <div className="mb-3 flex flex-wrap items-center gap-3 pr-12">
        <Field label={uiText('b22.graph.searchStart')}>{props => <Input {...props} className="w-64" value={search} onChange={event => setSearch(event.target.value)} />}</Field>
        <Field label={uiText('b22.graph.selectStart')}>{props => <Select {...props} className="max-w-full sm:w-96" value={start?.id ?? ''} onChange={event => choose(event.target.value)}>
          <option value="">{uiText('b22.graph.chooseStart')}</option>
          {choices.map(node => <option key={node.id} value={node.id}>{uiText(node.kind === 'source' ? 'features.graph.GraphPage.quelle_037889' : 'b22.graph.contract')}: {node.label} · {node.id.slice(node.kind.length + 1)}</option>)}
        </Select>}</Field>
        {!!needle && choices.filter(node => `${node.label} ${node.id}`.toLocaleLowerCase('de').includes(needle)).length === 0 && <span className="text-xs text-muted">{uiText('alpha7.graph.noMatch')}</span>}
        <Switch checked={structural} onCheckedChange={value => setStructure(String(value))} label={uiText("features.graph.GraphPage.liveness_dependencies_4224a7")} />
        <span className="ml-auto flex items-center gap-2 text-xs text-muted">
          <span className="flex items-center gap-1"><Cable size={13} /> {uiText("features.graph.GraphPage.quelle_037889")}</span>
          <span className="flex items-center gap-1"><span className="inline-block size-2 rounded-full border border-ok/70" /> {uiText("features.graph.GraphPage.contract_gesund_0a175e")}</span>
          <span className="flex items-center gap-1"><span className="inline-block size-2 rounded-full border border-warn/70" /> {uiText("features.graph.GraphPage.eingeschrankt_7c77f8")}</span>
          <span className="flex items-center gap-1"><Sigma size={13} /> {uiText("features.graph.GraphPage.ergebnis_cd8e22")}</span>
        </span>
      </div>
      {start && <p className="mb-3 text-sm" role="status">{uiText('b22.graph.chain', { name: start.label })}</p>}
      {start && live.stale && live.contracts && <p className="mb-2 text-xs text-warn">{uiText("features.graph.GraphPage.zustandsfarben_letzter_bekannter_stand_live_verb_243a87")}</p>}
      {src.loading ? (
        <Spinner />
      ) : full.nodes.length === 0 ? (
        <Panel><Empty title={uiText("features.graph.GraphPage.noch_nichts_darzustellen_acbf63")}>{uiText("features.graph.GraphPage.der_graph_entsteht_aus_sources_und_contracts_der_ff3f8d")}</Empty></Panel>
      ) : !start ? (
        <Panel><Empty title={uiText(requested ? 'b22.graph.missingStart' : 'b22.graph.chooseStart')}>{uiText('b22.graph.startHelp')}</Empty></Panel>
      ) : (
        <PanelLayout view="graph"><LayoutPanel id="graph" title={uiText("features.graph.GraphPage.graph_32ab01")} span={2}>
          <Panel className="h-[min(70vh,44rem)] min-h-[24rem] overflow-hidden">
            <ReactFlowProvider>
              <Canvas key={`${structural}-${start.id}`} graph={graph} envelopes={envelopes} selected={selectedNode?.id ?? start.id} onSelect={setInspected} focus={start.id} />
            </ReactFlowProvider>
          </Panel>
          </LayoutPanel><LayoutPanel id="details" title={uiText("features.graph.GraphPage.knotendetails_1af0bd")} span={1}>
          {selectedNode && <Details node={selectedNode} graph={graph} envelope={selectedNode.contractId && selectedNode.kind === 'contract' ? (envelopes?.[selectedNode.contractId] ?? null) : null} />}
          </LayoutPanel></PanelLayout>
      )}
    </>
  );
}

export { nodeId };
