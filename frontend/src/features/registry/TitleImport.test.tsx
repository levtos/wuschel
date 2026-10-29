import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { emptyRegistry } from '@/features/authoring/model';
import { installFakeApi, makeDraft, reply } from '../../../test/fakeApi';
import { idleLive } from '../../../test/harness';

const config = emptyRegistry();
const context = { key: '["synthetic","synthetic-app"]', context: 'Synthetic Context', source_app: 'synthetic-app' };
const result = (unresolved: string[] = []) => ({ import_id: 'synthetic', plan_digest: 'checked', already_applied: false, contexts: [context], report: { input_rows: 3181, target_entries: 6362, additional_forms_created: 3181, additional_forms_skipped: 0, error_count: 0, outcomes: {}, errors: [] }, unresolved, conflicts: [] });
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function open(respond: (body: any) => Response | Promise<Response> = () => reply(200, result()), entries = [{ key: 'Synthetic - Ambiguous - Music' }]) {
  const fake = installFakeApi({ revisions: [{ revision: 4, config, checksum: 'synthetic', created_at: '2026-10-27T00:00:00Z' }] });
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'media.homepods', version: 1 }]) });
  fake.routes.push({ match: /^title-import\/preview$/, respond: (_, body) => respond(body) });
  fake.routes.push({ match: /^title-import\/stage$/, respond: () => { const draft = makeDraft('titles', config); fake.drafts.set('titles', draft); return reply(200, draft); } });
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  window.location.hash = '#/import-export';
  render(<QueryClientProvider client={cache}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Titelkataloge einlesen' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Titelkataloge einlesen' }));
  const text = JSON.stringify({ version: 3, entries });
  const file = new File([text], 'synthetic-v3.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: async () => text });
  await user.upload(dialog.getByLabelText('Datei auswählen'), file);
  const check = dialog.getByRole('button', { name: 'Datei und Zuordnungen prüfen' });
  const stage = dialog.getByRole('button', { name: 'In Entwurf übernehmen' });
  return { fake, cache, user, dialog, check, stage, text };
}

it('invalidates checked choices, confirms hidden/excluded contexts and only creates a draft', async () => {
  const { fake, user, dialog, check, stage, text } = await open(body => reply(200, result(body.mappings[context.key] ? [] : ['unmapped'])));
  expect(stage).toBeDisabled(); await user.click(check);
  await dialog.findByText('synthetic-app'); expect(stage).toBeDisabled();
  await user.selectOptions(dialog.getByLabelText('Gerätekatalog'), 'exclude');
  expect(dialog.queryByLabelText('Ausgeblendete Titel mitnehmen')).not.toBeInTheDocument();
  expect(stage).toBeDisabled(); await user.click(check);
  await waitFor(() => expect(stage).toBeEnabled()); await user.click(stage);
  await waitFor(() => expect(window.location.hash).toBe('#/changes/drafts/titles'));
  expect(fake.calls.find(call => call.path === 'title-import/stage')?.body).toMatchObject({ source_text: text, mappings: { [context.key]: 'exclude' }, plan_digest: 'checked' });
  expect(fake.activations).toHaveLength(0);
});

it('ignores a preview from an older active revision', async () => {
  let finish!: (response: Response) => void;
  const { cache, user, check, stage, fake } = await open(() => new Promise(resolve => { finish = resolve; }));
  await user.click(check);
  await act(async () => { cache.setQueryData(['registry', 'active'], { revision: 5, config }); });
  await act(async () => finish(reply(200, result())));
  expect(stage).toBeDisabled(); expect(fake.calls.some(call => call.path === 'title-import/stage')).toBe(false);
});

it('keeps the file and mapping after a 409 and refreshes the active revision for rechecking', async () => {
  const { fake, user, dialog, check, stage, text } = await open();
  await user.click(check); await dialog.findByText('synthetic-app');
  await user.selectOptions(dialog.getByLabelText('Gerätekatalog'), 'media.homepods');
  await user.selectOptions(dialog.getByLabelText('Bedeutung der bisherigen Zahlen bestätigen'), 'music');
  await user.click(check); await waitFor(() => expect(stage).toBeEnabled());
  fake.revisions.push({ revision: 5, config, checksum: 'new', created_at: '2026-10-27T01:00:00Z' });
  fake.failNext.push({ path: /^title-import\/stage$/, status: 409, body: { error: 'active_revision_conflict' } });
  await user.click(stage); await waitFor(() => expect(stage).toBeDisabled());
  expect(dialog.getByLabelText('Gerätekatalog')).toHaveValue('media.homepods');
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'registry/active').length).toBeGreaterThan(1));
  await user.click(check); await waitFor(() => expect(stage).toBeEnabled());
  expect(fake.calls.filter(call => call.path === 'title-import/preview').at(-1)?.body).toMatchObject({ source_text: text, expected_active_revision: 5 });
});

it('needs no manual artist split, keeps row exclusion and blocks an already applied import', async () => {
  const { user, dialog, check, stage } = await open(body => reply(200, body.excluded_rows.includes(1) ? { ...result(), already_applied: true } : result()));
  await user.click(check); await dialog.findByText('synthetic-app'); expect(stage).toBeEnabled();
  expect(dialog.queryByLabelText('Interpret')).not.toBeInTheDocument();
  await user.click(dialog.getByText('Einzelne Zeilen bewusst ausschließen'));
  await user.click(dialog.getByLabelText('Diese Zeile bewusst ausschließen')); await user.click(check);
  await dialog.findByText('Dieser Import wurde bereits übernommen. Es entsteht keine weitere Katalogrevision.');
  expect(stage).toBeDisabled();
});

it('allows exclusion beyond the first hundred rows without another manual splitting round', async () => {
  const { fake, user, dialog, check, stage } = await open(undefined, Array.from({ length: 101 }, (_, i) => ({ key: `Synthetic Song ${i + 1}` })));
  await user.click(check); await dialog.findByText('synthetic-app');
  await user.click(dialog.getByText('Einzelne Zeilen bewusst ausschließen'));
  await user.click(dialog.getByRole('button', { name: 'Weitere Zeilen' }));
  expect(stage).toBeEnabled();
  expect(dialog.getByText('Zeile 101 · Synthetic Song 101')).toBeVisible();
  await user.click(dialog.getByLabelText('Diese Zeile bewusst ausschließen'));
  expect(stage).toBeDisabled();
  await user.click(check); await waitFor(() => expect(stage).toBeEnabled());
  const body = fake.calls.filter(call => call.path === 'title-import/preview').at(-1)?.body;
  expect(body).toMatchObject({ excluded_rows: [101] });
  expect(body).not.toHaveProperty('music_keys');
});
