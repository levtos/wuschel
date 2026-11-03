import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { LayoutPreferencesProvider } from '@/components/PanelLayout';
import { WorkingDocumentProvider } from '@/features/authoring/WorkingDocument';
import SourcesPage from '@/features/sources/SourcesPage';
import { SchemaProvider } from '@/lib/schema-context';
import capabilities from '../test/fixtures/capabilities.json';
import '@/index.css';

const cfg = {
  config_schema_version: 1, fingerprint_version: 2, dependencies: [], catalogs: [], definitions: [],
  sources: Array.from({ length: 90 }, (_, i) => ({ source_id: `source.synthetic${i}`, source_type: 'boolean', source_origin: 'local', freshness: { mode: 'event_stateful', future_tolerance_s: 1 } })),
  bindings: Array.from({ length: 90 }, (_, i) => ({ binding_id: `binding.synthetic${i}`, source_id: `source.synthetic${i}`, adapter: { kind: 'ha_state', ha_entity_id: `binary_sensor.synthetic_${i}` } })),
  contracts: Array.from({ length: 100 }, (_, i) => ({ contract_id: `test.synthetic${i}` })),
};
const drafts = Array.from({ length: 9 }, (_, i) => ({ draft_id: `synthetic${i}`, draft_version: 1, config: cfg, display_name: `Synthetic ${i}`, base_revision: i === 8 ? 11 : i, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }));
const entities = Array.from({ length: 6000 }, (_, i) => ({ entity_id: `binary_sensor.unrelated_${i}`, display_name: `Synthetic ${i}`, domain: 'binary_sensor', attributes: [] }));
const revision = { revision: 11, config: cfg, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' };
const calls: string[] = [];
Object.assign(window, { syntheticCalls: calls });
window.fetch = async (input, init) => {
  const url = String(input);
  const path = url.split('/api/v1/')[1] ?? '';
  calls.push(`${init?.method ?? 'GET'} ${path.split('?')[0]}`);
  const body = path === 'capabilities' ? capabilities :
    path === 'registry/active' ? revision :
    path === 'registry/active/export' ? cfg :
    path === 'registry/revisions' ? [revision] :
    path === 'registry/drafts' ? drafts :
    path.startsWith('ha/entities') ? { items: entities, areas: [], next_cursor: null } :
    path === 'info' ? { server_time: new Date().toISOString() } : {};
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={client}>
    <SchemaProvider schema={(capabilities as any).schemas.registry}>
      <MemoryRouter><LayoutPreferencesProvider value="perf"><WorkingDocumentProvider><SourcesPage /></WorkingDocumentProvider></LayoutPreferencesProvider></MemoryRouter>
    </SchemaProvider>
  </QueryClientProvider>,
);
