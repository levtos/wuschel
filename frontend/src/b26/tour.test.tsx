import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { fieldLabel } from '@/i18n/codes';
import { defaultDraftName } from '@/features/authoring/draftName';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function mount() {
  window.location.hash = '#/changes/drafts';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
}
function fixture() {
  const config = sampleConfig();
  return installFakeApi({
    revisions: [{ revision: 2, config, checksum: 'synthetic', created_at: '2026-01-01T12:00:00Z' }],
    drafts: [
      makeDraft('old', config, { base_revision: 1, display_name: 'Alter Entwurf' }),
      makeDraft('current', config, { base_revision: 2, display_name: 'Aktueller Entwurf' }),
      makeDraft('complete', config, { base_revision: 1, lifecycle: 'completed', display_name: 'Abgeschlossen' }),
    ],
  });
}
it('previews only obsolete open drafts, cancels without deletion and deletes with the shown version', async () => {
  const user = userEvent.setup(); const api = fixture(); mount();
  const cleanup = await screen.findByRole('button', { name: 'Veraltete Entwürfe aufräumen' });
  await waitFor(() => expect(cleanup).toBeEnabled());
  await user.click(cleanup);
  let dialog = screen.getByRole('alertdialog', { name: 'Veraltete Entwürfe aufräumen' });
  expect(within(dialog).getByText(/Alter Entwurf/)).toBeVisible();
  expect(within(dialog).queryByText(/Aktueller Entwurf|Abgeschlossen/)).toBeNull();
  expect(api.calls.filter(call => call.method === 'DELETE')).toHaveLength(0);
  await user.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
  expect(api.drafts.has('old')).toBe(true);
  await user.click(cleanup);
  dialog = screen.getByRole('alertdialog', { name: 'Veraltete Entwürfe aufräumen' });
  await user.click(within(dialog).getByRole('button', { name: 'Endgültig löschen' }));
  await waitFor(() => expect(api.drafts.has('old')).toBe(false));
  expect(api.drafts.has('current')).toBe(true); expect(api.drafts.has('complete')).toBe(true);
  expect(api.calls.find(call => call.method === 'DELETE')?.body).toEqual({ draft_version: 1 });
});
it('keeps a concurrently changed draft when the preview version is stale', async () => {
  const user = userEvent.setup(); const api = fixture(); mount();
  const cleanup = await screen.findByRole('button', { name: 'Veraltete Entwürfe aufräumen' });
  await waitFor(() => expect(cleanup).toBeEnabled()); await user.click(cleanup);
  api.touchDraft('old', { display_name: 'Anderer Stand' });
  await user.click(within(screen.getByRole('alertdialog', { name: 'Veraltete Entwürfe aufräumen' })).getByRole('button', { name: 'Endgültig löschen' }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  expect(api.drafts.get('old')?.display_name).toBe('Anderer Stand');
  expect(api.calls.filter(call => call.method === 'DELETE')).toHaveLength(1);
});
it('uses German field names and one padded Berlin date format for new drafts', () => {
  for (const name of ['active', 'date_time', 'private_time', 'entry_door_open', 'track_muted', 'temperature', 'valve_position', 'session']) expect(fieldLabel(name)).not.toBe(name);
  expect(defaultDraftName(new Date('2026-01-01T12:00:00Z'))).toContain('01.01.2026, 13:00');
});
