import { Profiler, useState } from 'react';
import { Session } from 'node:inspector';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { it } from 'vitest';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import SourcesPage from '@/features/sources/SourcesPage';
import { LayoutPreferencesProvider } from '@/components/PanelLayout';
import { installFakeApi, makeDraft, reply, sampleConfig } from '../test/fakeApi';
import { wrap } from '../test/harness';

function Probe() {
  const doc = useWorkingDocument();
  const [done, setDone] = useState(false);
  return <><button onClick={() => void doc.ensureDraft().then(() => setDone(true))}>Entität ändern</button><output>{done ? 'done' : ''}</output></>;
}

it('measures draft preparation with nine stale synthetic drafts', async () => {
  const cfg = sampleConfig({
    sources: Array.from({ length: 90 }, (_, i) => ({ source_id: `source.synthetic${i}` })),
    contracts: Array.from({ length: 100 }, (_, i) => ({ contract_id: `test.synthetic${i}` })),
  });
  const drafts = Array.from({ length: 9 }, (_, i) => makeDraft(`synthetic${i}`, cfg, { base_revision: i }));
  const fake = installFakeApi({ drafts, revisions: [{ revision: 11, config: cfg, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  let renderMs = 0;
  wrap(<Profiler id="working-document" onRender={(_id, _phase, duration) => { renderMs += duration; }}><WorkingDocumentProvider><Probe /></WorkingDocumentProvider></Profiler>);
  await waitFor(() => { if (!fake.calls.some(c => c.path === 'registry/active')) throw new Error('waiting'); });
  renderMs = 0;
  const before = fake.calls.length;
  const start = performance.now();
  fireEvent.click(screen.getByRole('button', { name: 'Entität ändern' }));
  await screen.findByText('done');
  console.log(JSON.stringify({ scenario: 'nine stale drafts', wall_ms: performance.now() - start,
    react_render_ms: renderMs, requests: fake.calls.slice(before).map(c => c.path.split('/').slice(0, 2).join('/')) }));
});

it('profiles the actual source page with synthetic discovery data', async () => {
  const cfg = sampleConfig({
    sources: Array.from({ length: 90 }, (_, i) => ({ source_id: `source.synthetic${i}`, source_type: 'boolean', source_origin: 'local', freshness: { mode: 'event_stateful', future_tolerance_s: 1 } })),
    bindings: Array.from({ length: 90 }, (_, i) => ({ binding_id: `binding.synthetic${i}`, source_id: `source.synthetic${i}`, adapter: { kind: 'ha_state', ha_entity_id: `binary_sensor.synthetic_${i}` } })),
    contracts: Array.from({ length: 100 }, (_, i) => ({ contract_id: `test.synthetic${i}` })),
  });
  const drafts = Array.from({ length: 9 }, (_, i) => makeDraft(`synthetic${i}`, cfg, { base_revision: i === 8 ? 11 : i }));
  const fake = installFakeApi({ drafts, revisions: [{ revision: 11, config: cfg, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  const entities = Array.from({ length: 6000 }, (_, i) => ({ entity_id: `binary_sensor.unrelated_${i}`, display_name: `Synthetic ${i}`, domain: 'binary_sensor', attributes: [] }));
  fake.routes.push({ match: /^ha\/entities\?/, method: 'GET', respond: () => reply(200, { items: entities, areas: [], next_cursor: null }) });
  let renderMs = 0;
  wrap(<LayoutPreferencesProvider value="one"><Profiler id="source-page" onRender={(_id, _phase, duration) => { renderMs += duration; }}><WorkingDocumentProvider><SourcesPage /></WorkingDocumentProvider></Profiler></LayoutPreferencesProvider>);
  await waitFor(() => { if (!fake.calls.some(c => c.path.startsWith('ha/entities'))) throw new Error('waiting'); });
  await waitFor(() => screen.getByRole('button', { name: /source.synthetic0/ }));
  fireEvent.click(screen.getByRole('button', { name: /source.synthetic0/ }));
  renderMs = 0;
  const before = fake.calls.length;
  const profiler = new Session();
  profiler.connect();
  const command = (name: string) => new Promise<any>((resolve, reject) => profiler.post(name, (error, result) => error ? reject(error) : resolve(result)));
  await command('Profiler.enable');
  await command('Profiler.start');
  const start = performance.now();
  fireEvent.click(screen.getByRole('button', { name: 'Entität ändern' }));
  await screen.findByRole('dialog');
  const wall = performance.now() - start;
  const { profile } = await command('Profiler.stop');
  profiler.disconnect();
  const nodes = new Map<number, any>(profile.nodes.map((node: any) => [node.id, node]));
  const counts = new Map<number, number>();
  for (const id of profile.samples ?? []) counts.set(id, (counts.get(id) ?? 0) + 1);
  const hot = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([id, samples]) => {
    const frame = nodes.get(id).callFrame;
    return { function: frame.functionName || '(anonymous)', file: frame.url.split('/').at(-1), line: frame.lineNumber + 1, samples };
  });
  console.log(JSON.stringify({ scenario: 'source page, nine mixed drafts, 6000 unmatched synthetic entities',
    wall_ms: wall, react_render_ms: renderMs, hot,
    requests: fake.calls.slice(before).map(c => ({ method: c.method, path: c.path.split('/').slice(0, 2).join('/') })) }));
});
