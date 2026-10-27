import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { contractFieldValue, contractTitle, contractValue } from '@/features/contracts/presentation';
import { contractName } from '@/features/overview/overviewModel';
import type { Envelope, FieldValue } from '@/lib/types';
import type { Json, JsonObject } from '@/lib/json';
import { installFakeApi } from '../test/fakeApi';
import { idleLive } from '../test/harness';
import { episodeId, sleepRegistry } from '../test/b14-fixtures';

const valid = (value: Json): FieldValue => ({ status: 'valid', quality: 'healthy', value, evidence: [], reasons: [] });
function envelope(id: string, type: string): Envelope {
  const definition = (sleepRegistry.definitions as JsonObject[]).find(row => row.type_id === type)!;
  return { contract_id: id, type: { type_id: type, type_version: 1 }, fields: Object.fromEntries(Object.entries(definition.outputs as JsonObject).map(([name, rule]) => [name, valid((rule as JsonObject).value!)])), enabled: true, available: true, contract_health: 'healthy', computed_at: '2026-01-01T00:00:00Z', published_at: '2026-01-01T00:00:00Z', publication_seq: 1, registry_revision: 1, epoch_id: 'synthetic' };
}
const state = envelope('synthetic.b14.state', 'sleep.state_view');
const wake = envelope('synthetic.b14.wake', 'wake.plan_view');
const internal = envelope('synthetic.b14.internal', 'sleep.flow');
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function app(hash: string) {
  installFakeApi({ revisions: [{ revision: 1, config: sleepRegistry, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  window.location.hash = hash;
  const live = idleLive();
  vi.spyOn(live, 'getSnapshot').mockReturnValue({ ...live.getSnapshot(), contracts: Object.fromEntries([state, wake, internal].map(row => [row.contract_id, row])), registryRevision: 1, stale: false, connection: 'open' });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={live} /></QueryClientProvider>);
}

it('keeps the list summaries short and preserves unknown and user text', () => {
  expect(contractValue(state, {})).toMatch(/^Zustand: Vor dem Schlaf · Frist bis /);
  expect(contractValue(state, {})).not.toContain(episodeId);
  expect(contractValue(wake, {})).toContain('Plan heute: Geplant');
  expect(contractValue(wake, {})).not.toContain('rule_wake');
  expect(contractValue(internal, {})).toBe('Interner Ablauf für Schlafzustand und Weckplan');
  expect(contractValue({ ...state, fields: { ...state.fields, value: { ...valid(null), status: 'unknown' } } }, {})).toContain('Zustand: unbekannt');
  expect(contractFieldValue('user.text', 'entry_origin', valid('initial'))).toBe('initial');
  expect(contractTitle({ type_id: 'sleep.flow' })).toBe('Schlafablauf (intern)');
  expect(contractName(sleepRegistry, internal.contract_id)).toBe('Schlafablauf (intern)');
});

it('shows only core values in the real contract list layout', async () => {
  app('#/contracts/list/all');
  const table = await screen.findByRole('table', { name: 'Verträge' });
  await within(table).findByText(/Zustand: Vor dem Schlaf/);
  expect(within(table).queryByText(episodeId)).not.toBeInTheDocument();
  expect(within(table).queryByText('Zu Beginn')).not.toBeInTheDocument();
  expect(within(table).getByRole('link', { name: 'Schlafablauf (intern)' })).toBeVisible();
});

it('keeps the episode and translated secondary fields in the detail view', async () => {
  app('#/contracts/view/synthetic.b14.state');
  await screen.findByRole('heading', { name: 'Wert jetzt' });
  expect(screen.getByTestId('value-episode_id')).toHaveTextContent(episodeId);
  expect(screen.getByTestId('value-entry_origin')).toHaveTextContent('Zu Beginn');
  expect(screen.getByTestId('value-last_action_origin')).toHaveTextContent('Bestätigt');
  expect(screen.getByText('Schlafkontext aktiv')).toBeVisible();
  expect(screen.queryByText('entry_origin')).not.toBeInTheDocument();
});

it('translates the wake decision and field labels in the detail view', async () => {
  app('#/contracts/view/synthetic.b14.wake');
  await screen.findByRole('heading', { name: 'Wert jetzt' });
  expect(screen.getByTestId('value-decision')).toHaveTextContent('Weckregel');
  expect(screen.getByText('Beginn des Weckfensters')).toBeVisible();
  expect(screen.getByTestId('value-minimum_sleep_applied')).toHaveTextContent('Nein');
});
