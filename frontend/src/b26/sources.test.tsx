import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';
import type { JsonObject } from '@/lib/json';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function mount(path: string, config: JsonObject) {
  const api = installFakeApi({ revisions: [{ revision: 1, config, checksum: 'synthetic', created_at: '2026-01-01T12:00:00Z' }] });
  window.location.hash = path;
  const show = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  return { api, show };
}
it('explains own freshness rules and unconfirmed types without assigning anything', async () => {
  const config = sampleConfig({ sources: [
    { source_id: 'known', sensor_kind: 'contact', sensor_kind_origin: 'confirmed', freshness_selection: { kind: 'own', rule: { mode: 'event_stateful' } } },
    { source_id: 'unknown', freshness: { mode: 'event_stateful' } },
  ], freshness_templates: [] });
  const { api, show } = mount('#/sources/freshness', config);
  api.routes.push({ match: /^registry\/freshness/, respond: () => reply(200, { templates: {} }) }); show();
  expect(await screen.findByText('Keine gemeinsame Vorlage. 1 Quelle dieses Typs nutzt eine eigene Regel.')).toBeVisible();
  expect(screen.getByText(/Bei 1 Quelle ist der Sensortyp noch nicht bestätigt/)).toBeVisible();
  expect(api.calls.some(call => call.method === 'PUT')).toBe(false);
});
it('shows batched managed counts and a direct catalog link, never configuration zeroes', async () => {
  const catalog = { catalog_id: 'synthetic', version: 1, kind: 'managed', classification_schema: { values: ['normal'] } };
  const { api, show } = mount('#/sources/catalogs', sampleConfig({ catalogs: [catalog] }));
  api.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ ...catalog, active_entry_count: 3 }]) }); show();
  expect(await screen.findByText(/v1 · 3 Einträge/)).toBeVisible();
  expect(api.calls.filter(call => call.path === 'catalogs')).toHaveLength(1);
  expect(api.calls.some(call => call.path.startsWith('catalogs/entries'))).toBe(false);
});
it('explains the source chain on the sources page', async () => {
  const { show } = mount('#/sources/devices', sampleConfig()); show();
  expect(await screen.findByText(/Engines lesen nur Verträge/)).toBeVisible();
});
