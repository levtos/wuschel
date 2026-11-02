import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import type { GraphNode } from './buildGraph';
import type { JsonObject } from '@/lib/json';
import { installFakeApi, sampleConfig } from '../../../test/fakeApi';
import { idleLive } from '../../../test/harness';

const flow = vi.hoisted(() => ({ fitView: vi.fn().mockResolvedValue(true), setViewport: vi.fn().mockResolvedValue(true), zoomIn: vi.fn(), zoomOut: vi.fn() }));
// Observe the graph passed to the layout library. Traversal has separate exact
// node/edge assertions; this boundary does not depend on jsdom canvas geometry.
vi.mock('@xyflow/react', async importOriginal => ({
  ...await importOriginal<typeof import('@xyflow/react')>(),
  ReactFlowProvider: ({ children }: PropsWithChildren) => children,
  Background: () => null,
  useReactFlow: () => flow,
  ReactFlow: ({ nodes, onNodeClick, children, ...props }: PropsWithChildren<{
    nodes: { id: string; data: { node: GraphNode } }[];
    onNodeClick: (event: unknown, node: { id: string }) => void;
    'aria-label': string;
  }>) => <div role="region" aria-label={props['aria-label']}>
    {nodes.map(node => <button key={node.id} data-chain-node={node.id} onClick={event => onNodeClick(event, node)}>{node.data.node.label}</button>)}
    {children}
  </div>,
}));

const config = sampleConfig({
  sources: [{ source_id: 'source.synthetic_calendar', display_name: 'Synthetischer Kalender' }, { source_id: 'source.synthetic_unused', display_name: 'Synthetische fremde Quelle' }],
  contracts: [
    { contract_id: 'synthetic.holiday', display_name: 'Feiertag morgen', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'calendar', ref: 'source.synthetic_calendar' }], parameters: {} },
    { contract_id: 'synthetic.wake', display_name: 'Synthetisches Wecken', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'holiday', ref: 'synthetic.holiday.value' }], parameters: {} },
    { contract_id: 'synthetic.sibling', display_name: 'Synthetischer fremder Seitenzweig', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'calendar', ref: 'source.synthetic_calendar' }], parameters: {} },
  ],
});
const holidayNodes = ['contract:synthetic.holiday', 'contract:synthetic.wake', 'result:synthetic.holiday.value', 'source:source.synthetic_calendar'].sort();
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function app(hash = '#/contracts/relations/graph', value: JsonObject = config) {
  const fake = installFakeApi({ revisions: [{ revision: 1, config: structuredClone(value), checksum: 'synthetic', created_at: '2032-04-05T12:00:00Z' }] });
  location.hash = hash;
  const view = render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench live={idleLive()} onSetup={() => {}} /></QueryClientProvider>);
  return { fake, view };
}
const renderedIds = (graph: HTMLElement) => Array.from(graph.querySelectorAll('[data-chain-node]'), node => node.getAttribute('data-chain-node')).sort();

it('starts with a searchable source/contract choice and no complete diagram', async () => {
  const { fake } = app();
  await screen.findByRole('option', { name: /Vertrag: Feiertag morgen/ });
  expect(screen.queryByRole('region', { name: 'Abhängigkeitsgraph' })).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Startpunkt auswählen' })).toHaveValue('');
  expect(screen.queryByRole('option', { name: /Ergebnis/ })).not.toBeInTheDocument();
  const user = userEvent.setup();
  await user.type(screen.getByRole('textbox', { name: 'Startpunkt suchen' }), 'Feiertag');
  expect(screen.queryByRole('option', { name: /fremder Seitenzweig/ })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByRole('combobox', { name: 'Startpunkt auswählen' }), 'contract:synthetic.holiday');
  const graph = await screen.findByRole('region', { name: 'Abhängigkeitsgraph' });
  await waitFor(() => expect(renderedIds(graph)).toEqual(holidayNodes));
  expect(fake.calls.some(call => ['POST', 'PUT', 'DELETE'].includes(call.method))).toBe(false);
});

it('inspects chain nodes without changing the start or expanding into a sibling branch', async () => {
  app('#/contracts/relations/graph?node=contract%3Asynthetic.holiday');
  const graph = await screen.findByRole('region', { name: 'Abhängigkeitsgraph' });
  await userEvent.click(within(graph).getByRole('button', { name: 'Synthetisches Wecken' }));
  expect(screen.getByRole('combobox', { name: 'Startpunkt auswählen' })).toHaveValue('contract:synthetic.holiday');
  expect(renderedIds(graph)).toEqual(holidayNodes);
  await userEvent.click(screen.getByRole('button', { name: 'Startpunkt ändern' }));
  expect(screen.queryByRole('region', { name: 'Abhängigkeitsgraph' })).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Startpunkt auswählen' })).toHaveValue('');
});

it('uses source identity in the source details link and keeps the source chain scoped', async () => {
  app('#/contracts/relations/graph?node=source%3Asource.synthetic_calendar');
  const graph = await screen.findByRole('region', { name: 'Abhängigkeitsgraph' });
  expect(renderedIds(graph)).toEqual([...holidayNodes, 'contract:synthetic.sibling'].sort());
  await userEvent.click(screen.getByRole('button', { name: 'Quelle öffnen' }));
  await waitFor(() => expect(location.hash).toBe('#/sources/devices?source_id=source.synthetic_calendar'));
});

it('shows a missing-start notice instead of another chain when the selected object is gone', async () => {
  app('#/contracts/relations/graph?node=contract%3Asynthetic.missing');
  expect(await screen.findByText('Der Startpunkt ist in dieser Konfiguration nicht vorhanden')).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Abhängigkeitsgraph' })).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Startpunkt auswählen' })).toHaveValue('');
});

it('opens an old result deep link at its producing contract', async () => {
  app('#/contracts/relations/graph?node=result%3Asynthetic.holiday.value');
  const graph = await screen.findByRole('region', { name: 'Abhängigkeitsgraph' });
  expect(screen.getByRole('combobox', { name: 'Startpunkt auswählen' })).toHaveValue('contract:synthetic.holiday');
  expect(renderedIds(graph)).toEqual(holidayNodes);
});
