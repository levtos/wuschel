import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
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
const preview = (digest = 'first') => ({ config: incoming, plan_digest: digest, added: 1, reused: 28, conflicts: [], unresolved: [], editorial_versions: {}, editorial_changed: {}, package: { config: incoming, editorial: [] } });

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

it('shows an explicit name-only correction without a functional conflict', async () => {
  const { fake, stage } = await openImport(() => reply(200, { ...preview(), display_name_changes: [{ id: 'synthetic.workday', before: 'Arbeitstag today', after: 'Arbeitstag heute' }] }));
  expect(await screen.findByText('„Arbeitstag today“ wird zu „Arbeitstag heute“')).toBeVisible();
  expect(screen.queryByRole('region', { name: /Konflikte/ })).not.toBeInTheDocument();
  expect(stage).toBeEnabled();
  expect(fake.activations).toHaveLength(0);
});

it('rechecks a first preview whose active revision arrives late before allowing staging', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: target, checksum: 'synthetic', created_at: '2026-10-26T00:00:00Z' }] });
  let finish!: (response: Response) => void;
  let count = 0;
  fake.routes.push({ match: /^portable\/preview$/, respond: () => ++count === 1 ? new Promise(resolve => { finish = resolve; }) : reply(200, preview('current-revision')) });
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  window.location.hash = '#/import-export';
  render(<QueryClientProvider client={cache}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  const user = userEvent.setup();
  const file = new File([packageText], 'synthetic-late-revision.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: async () => packageText });
  await user.upload(await screen.findByLabelText('Datei auswählen'), file);
  await user.click(screen.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' }));
  await waitFor(() => expect(count).toBe(1));
  await act(async () => { cache.setQueryData(['registry', 'active'], { revision: 5, config: target, checksum: 'new-revision', created_at: '2026-10-26T00:00:00Z' }); });
  await act(async () => finish(reply(200, preview('obsolete-revision'))));
  const stage = await screen.findByRole('button', { name: 'Als Entwurf einlesen' });
  expect(stage).toBeDisabled();
  await waitFor(() => expect(count).toBe(2));
  await waitFor(() => expect(stage).toBeEnabled());
  expect(fake.calls.some(call => call.path === 'portable/stage')).toBe(false);
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
  const retry = screen.getByRole('button', { name: 'Vorschau neu prüfen' });
  expect(retry.parentElement).toBe(stage.parentElement);
  await act(async () => resolveOld(reply(200, preview('obsolete'))));
  expect(stage).toBeDisabled();
  await user.click(retry);
  await waitFor(() => expect(stage).toBeEnabled());
});

it('always offers a visible recheck beside staging and blocks a failed manual recheck until success', async () => {
  let count = 0; let finish!: (value: Response) => void;
  const { user, stage } = await openImport(() => ++count === 2 ? new Promise(resolve => { finish = resolve; }) : reply(200, preview(`digest-${count}`)));
  await user.click(screen.getByRole('button', { name: 'Vorschau neu prüfen' }));
  expect(stage).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Vorschau wird geprüft …' })).toBeDisabled();
  await act(async () => finish(reply(409, { error: 'draft_version_conflict' })));
  expect(stage).toBeDisabled();
  expect(screen.getByText('Die Vorschau konnte nicht aktualisiert werden. Prüfe den Fehler und versuche es erneut.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Vorschau neu prüfen' }));
  await waitFor(() => expect(stage).toBeEnabled());
});

it('puts compact conflicts first, explains the blocked action with a working jump and resolves same IDs from the file', async () => {
  const keys = ['bindings:binding.synthetic', 'contracts:day.synthetic'];
  const { fake, user, stage } = await openImport(body => reply(200, { ...preview('resolved'), conflicts: keys, unresolved: keys.filter(key => body.resolutions[key] !== 'file') }));
  expect(stage).toBeDisabled();
  const first = document.getElementById('import-conflict-0')!;
  const scroll = vi.fn(); first.scrollIntoView = scroll;
  await user.click(screen.getByRole('link', { name: '2 Konflikte noch nicht entschieden' }));
  expect(scroll).toHaveBeenCalled(); expect(first).toHaveFocus();
  const conflicts = screen.getByRole('region', { name: 'Konflikte (2)' });
  expect(conflicts.compareDocumentPosition(screen.getByRole('heading', { name: 'Aktualität zuordnen' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  for (const select of within(conflicts).getAllByLabelText('Umgang mit dem vorhandenen Objekt')) await user.selectOptions(select, 'file');
  await user.click(screen.getByRole('button', { name: 'Vorschau neu prüfen' }));
  await waitFor(() => expect(stage).toBeEnabled());
  expect(screen.queryByRole('link', { name: /Konflikte noch nicht entschieden/ })).not.toBeInTheDocument();
  await user.click(stage);
  await waitFor(() => expect(fake.calls.some(call => call.path === 'portable/stage')).toBe(true));
  expect(fake.calls.find(call => call.path === 'portable/stage')?.body).toMatchObject({ resolutions: Object.fromEntries(keys.map(key => [key, 'file'])), plan_digest: 'resolved' });
  expect(fake.activations).toHaveLength(0);
});

it('shows only changed or new organization rows among forty unchanged objects and distinguishes contract from template', async () => {
  const unchanged = Array.from({ length: 40 }, (_, i) => ({ object: { kind: 'contract', id: `same.${i}` }, category: 'other', room: { name: '' } }));
  const rows = [...unchanged, { object: { kind: 'contract', id: 'shared.name' }, category: 'time', room: { name: '' } }, { object: { kind: 'template', id: 'shared.name' }, category: 'time', room: { name: '' } }];
  const { fake, user, stage } = await openImport(() => reply(200, { ...preview(), editorial_versions: Object.fromEntries(rows.slice(0, 41).map(row => [`${row.object.kind}:${row.object.id}`, 1])), editorial_changed: Object.fromEntries(rows.map((row, i) => [`${row.object.kind}:${row.object.id}`, i >= 40])), package: { config: incoming, editorial: rows } }));
  expect(screen.getByText('40 Objekte behalten Kategorie und Bereich')).toBeInTheDocument();
  expect(screen.queryByText(/same\.0 ·/)).not.toBeInTheDocument();
  expect(screen.getByText(/Vertrag: shared.name/)).toBeInTheDocument();
  expect(screen.getByText(/Vorlage: shared.name/)).toBeInTheDocument();
  expect(screen.getAllByLabelText('Kategorie und Bereich')).toHaveLength(1);
  await user.click(stage);
  await waitFor(() => expect(fake.calls.some(call => call.path === 'portable/stage')).toBe(true));
  expect(fake.calls.find(call => call.path === 'portable/stage')?.body).toMatchObject({ editorial_choices: {} });
});

it('includes changed rules and new sources, compares template rules, and ignores names or object key order', () => {
  const same = { ...source('existing'), display_name: 'Renamed', freshness_selection: { rule: { future_tolerance_s: 0, interval_s: 8400, mode: 'report_heartbeat' }, kind: 'own' } };
  const config = { ...target, sources: [same, source('changed', 9000), source('new')] };
  expect(importFreshnessSources(config, { ...target, sources: [source('existing'), source('changed')] }).map(row => row.source_id)).toEqual(['changed', 'new']);
  const shared = { source_id: 'shared', freshness_selection: { kind: 'shared', template_id: 'freshness.contact' } };
  expect(importFreshnessSources({ ...target, sources: [shared] }, { ...target, sources: [shared], freshness_templates: [{ ...target.freshness_templates[0], rule: rule(9000) }] })).toEqual([shared]);
});
