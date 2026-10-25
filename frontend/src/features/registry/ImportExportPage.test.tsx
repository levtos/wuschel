import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { emptyRegistry } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';
import { importFreshnessSources } from '@/features/sources/freshness';
import { installFakeApi, makeDraft, reply } from '../../../test/fakeApi';
import { idleLive } from '../../../test/harness';

const rule = (interval_s = 8400) => ({ mode: 'report_heartbeat', interval_s, future_tolerance_s: 0 });
const source = (id: string, interval = 8400): JsonObject => ({ source_id: id, display_name: id, freshness_selection: { kind: 'own', rule: rule(interval) } });
const existing = Array.from({ length: 28 }, (_, index) => source(`source.existing.${index}`));
const target = { ...emptyRegistry(), sources: existing, freshness_templates: [{ template_id: 'freshness.contact', display_name: 'Kontakte', sensor_kind: 'contact', rule: rule() }] };
const incoming = { ...target, sources: [...existing, source('source.new')] };
const packageText = JSON.stringify({ scope: 'config', config: incoming });
const preview = (digest = 'first') => ({ config: incoming, plan_digest: digest, added: 1, reused: 28, conflicts: [], unresolved: [], editorial_versions: {}, package: { config: incoming, editorial: [] } });

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openImport(respond: (body: any) => Response | Promise<Response> = () => reply(200, preview())) {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: target, checksum: 'synthetic', created_at: '2026-10-26T00:00:00Z' }] });
  fake.routes.push({ match: /^portable\/preview$/, respond: (_, body) => respond(body) });
  fake.routes.push({ match: /^portable\/stage$/, respond: () => { const draft = makeDraft('imported', incoming); fake.drafts.set(draft.draft_id, draft); return reply(200, draft); } });
  window.location.hash = '#/import-export';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  const user = userEvent.setup();
  const file = new File([packageText], 'synthetic-full-config.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: async () => packageText });
  await user.upload(await screen.findByLabelText('Datei auswählen'), file);
  await user.click(screen.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' }));
  await screen.findByText('28 bestehende Quellen bleiben unverändert');
  return { fake, user, stage: screen.getByRole('button', { name: 'Als Entwurf einlesen' }) };
}

it('keeps reused sources implicit and allows unknown sensor types and file rules without a forced choice', async () => {
  const { fake, user, stage } = await openImport();
  expect(screen.queryByText(/source.existing.0 ·/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Sensortyp bestätigen')).toHaveValue('');
  expect(stage).toBeEnabled();
  await user.click(stage);
  await waitFor(() => expect(window.location.hash).toBe('#/changes/drafts/imported'));
  expect(fake.calls.find(call => call.path === 'portable/stage')?.body).toMatchObject({ package_text: packageText, plan_digest: 'first', freshness_choices: {} });
  expect(fake.activations).toHaveLength(0);
});

it('automatically debounces a freshness selection and stages the newly checked digest without a second preview click', async () => {
  let count = 0;
  const { fake, user, stage } = await openImport(() => reply(200, preview(`digest-${++count}`)));
  await user.selectOptions(screen.getByLabelText('Sensortyp bestätigen'), 'contact');
  await user.selectOptions(screen.getByLabelText('Aktualität zuordnen'), 'freshness.contact');
  expect(stage).toBeDisabled();
  expect(screen.getByText('Auswahl geändert – Vorschau wird aktualisiert')).toHaveAttribute('role', 'status');
  await waitFor(() => expect(stage).toBeEnabled());
  expect(count).toBe(2);
  await user.click(stage);
  await waitFor(() => expect(fake.calls.some(call => call.path === 'portable/stage')).toBe(true));
  expect(fake.calls.find(call => call.path === 'portable/stage')?.body).toMatchObject({ plan_digest: 'digest-2', freshness_choices: { 'source.new': { kind: 'shared', template_id: 'freshness.contact', sensor_kind: 'contact' } } });
});

it('shows a failed recheck beside staging, keeps it blocked and ignores an older response', async () => {
  let count = 0; let resolveOld!: (value: Response) => void;
  const { user, stage } = await openImport(() => {
    count++;
    if (count === 2) return new Promise(resolve => { resolveOld = resolve; });
    if (count === 3) return reply(409, { error: 'draft_version_conflict' });
    return reply(200, preview());
  });
  await user.selectOptions(screen.getByLabelText('Aktualität zuordnen'), 'own');
  await waitFor(() => expect(count).toBe(2));
  await user.selectOptions(screen.getByLabelText('Aktualität zuordnen'), '');
  await screen.findByText('Die Vorschau konnte nicht aktualisiert werden. Prüfe den Fehler und versuche es erneut.');
  expect(stage).toBeDisabled();
  const retry = screen.getByRole('button', { name: 'Vorschau erneut prüfen' });
  expect(retry.compareDocumentPosition(stage) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  await act(async () => resolveOld(reply(200, preview('obsolete'))));
  expect(stage).toBeDisabled();
  await user.click(retry);
  await waitFor(() => expect(stage).toBeEnabled());
});

it('includes changed rules and new sources, compares template rules, and ignores names or object key order', () => {
  const same = { ...source('existing'), display_name: 'Renamed', freshness_selection: { rule: { future_tolerance_s: 0, interval_s: 8400, mode: 'report_heartbeat' }, kind: 'own' } };
  const config = { ...target, sources: [same, source('changed', 9000), source('new')] };
  expect(importFreshnessSources(config, { ...target, sources: [source('existing'), source('changed')] }).map(row => row.source_id)).toEqual(['changed', 'new']);
  const shared = { source_id: 'shared', freshness_selection: { kind: 'shared', template_id: 'freshness.contact' } };
  expect(importFreshnessSources({ ...target, sources: [shared] }, { ...target, sources: [shared], freshness_templates: [{ ...target.freshness_templates[0], rule: rule(9000) }] })).toEqual([shared]);
});
