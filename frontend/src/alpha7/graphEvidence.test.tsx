import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { EvidenceTab } from '@/features/diagnostics/EvidenceLookup';
import { buildGraph } from '@/features/graph/buildGraph';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { idleLive, wrap } from '../../test/harness';

const config = sampleConfig({ sources: [{ source_id: 'source.a', display_name: 'Thermometer' }, { source_id: 'source.b', display_name: 'Fensterkontakt' }], contracts: [{ contract_id: 'raum', display_name: 'Raumtemperatur', type_id: 'test.echo', type_version: 1, enabled: true, inputs: [{ name: 'a', ref: 'source.a' }] }] });
const revision = { revision: 1, config, checksum: 'sum', created_at: '2026-10-16T12:00:00Z' };
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it('chooses an active source by name and renders saved clocks and values as a German table', async () => {
  const user = userEvent.setup(); const api = installFakeApi({ revisions: [revision] });
  api.routes.push({ match: /^evidence\//, respond: () => reply(200, [{ source_id: 'source.a', binding_id: 'binding.a', value_raw: '21.5', value_normalized: 21.5, normalized: true, observation_kind: 'report', device_time: null, received_at: '2026-10-16T12:00:00Z', availability: 'available' }]) });
  const view = wrap(<EvidenceTab />);
  const picker = await screen.findByLabelText('Quelle auswählen');
  expect(screen.queryByLabelText("Kennung des Nachweises")).toBeNull();
  expect(api.calls.some(call => call.path.startsWith('evidence/'))).toBe(false);
  await user.selectOptions(picker, 'source.a');
  const table = await screen.findByRole('table', { name: 'Gespeicherter Nachweis' });
  expect(within(table).getByText('21,5')).toBeVisible();
  expect(within(table).getByText('Ja')).toBeVisible();
  expect(within(table).getByText('neue Meldung')).toBeVisible();
  expect(within(table).getByText('Messzeit vom Gerät').closest('tr')).toHaveTextContent('—');
  expect(within(table).getByText('Empfangen').closest('tr')).not.toHaveTextContent('—');
  expect(view.container.querySelector('pre')).toBeNull();
  expect(api.calls.filter(call => call.path === 'evidence/source.a')).toHaveLength(1);
});

it('does not retain another source result while a new source request is pending', async () => {
  const user = userEvent.setup(); const api = installFakeApi({ revisions: [revision] });
  let finish!: (response: Response) => void;
  api.routes.push({ match: /^evidence\/source\./, respond: path => path.endsWith('a') ? reply(200, [{ value_normalized: 21.5 }]) : new Promise(resolve => { finish = resolve; }) });
  wrap(<EvidenceTab />);
  const picker = await screen.findByLabelText('Quelle auswählen'); await user.selectOptions(picker, 'source.a');
  await screen.findByText('21,5'); await user.selectOptions(picker, 'source.b');
  expect(screen.queryByText('21,5')).toBeNull();
  finish(reply(200, [])); await screen.findByText('Keine aktuelle Beobachtung');
});

it('keeps display names separate from identifiers in graph nodes and links', () => {
  const graph = buildGraph(config);
  expect(graph.nodes.find(node => node.id === 'contract:raum')).toMatchObject({ label: 'Raumtemperatur', contractId: 'raum' });
  expect(graph.nodes.find(node => node.id === 'source:source.a')?.label).toBe('Thermometer');
  expect(graph.edges[0]).toMatchObject({ source: 'source:source.a', target: 'contract:raum' });
});

it('clearly marks the graph as live, explains unavailable controls, and keeps named contract links correct', async () => {
  const user = userEvent.setup(); installFakeApi({ revisions: [revision] });
  window.location.hash = '#/contracts/relations/graph?node=contract%3Araum';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  await screen.findByText(/Verknüpfungen der Konfiguration 1/, {}, { timeout: 5000 });
  // #97 P4 makes this deep link an explicit start, replacing the global search-first view.
  expect(screen.getByRole('combobox', { name: 'Startpunkt auswählen' })).toHaveValue('contract:raum');
  expect(screen.getByText(/^Startpunkt: Raumtemperatur\./)).toBeVisible();
  expect(screen.queryByRole('option', { name: 'Alle Zusammenhänge' })).not.toBeInTheDocument();
  await user.click(await screen.findByRole('button', { name: 'Detailansicht' }));
  await waitFor(() => expect(window.location.hash).toBe('#/contracts/view/raum'));
});
