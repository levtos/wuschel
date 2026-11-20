import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { beforeEach, expect, it } from 'vitest';
import { useState } from 'react';
import { installFakeApi, makeDraft, sampleConfig } from '../../../test/fakeApi';
import type { Draft, Registry } from '@/lib/types';
import { DraftEditor } from './DraftEditor';
import { EditorNavigationGuard } from './EditorGuard';
import { ConflictDialog } from './DraftBar';
import { WorkingDocumentProvider, useWorkingDocument } from './WorkingDocument';

beforeEach(() => sessionStorage.clear());

const base = sampleConfig({ marker: 'Synthetic baseline' });
const revision = { revision: 1, config: base, checksum: 'sum', created_at: '2026-01-01T00:00:00Z' };

function Subject({ draft }: { draft?: Draft }) {
  const doc = useWorkingDocument();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(String(base.marker));
  return <>
    {draft && <button onClick={() => doc.open(draft)}>Open existing draft</button>}
    <button onClick={() => { setValue(String(base.marker)); setOpen(true); }}>Open local editor</button>
    {open && <DraftEditor title="Synthetic editor" base={base} input={value} dirty={value !== base.marker} plan={() => ({ config: { ...base, marker: value } })} onClose={() => setOpen(false)}>
      <label>Fixture value<input value={value} onChange={event => setValue(event.target.value)} /></label>
    </DraftEditor>}
    <ConflictDialog />
  </>;
}

function mount(draft?: Draft) {
  const router = createMemoryRouter([{ path: '/', element: <EditorNavigationGuard><Subject draft={draft} /></EditorNavigationGuard> }]);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <WorkingDocumentProvider validateDebounceMs={100_000}><RouterProvider router={router} /></WorkingDocumentProvider>
  </QueryClientProvider>);
  return router;
}

async function changeFixture(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Open local editor' }));
  const editor = within(await screen.findByRole('dialog', { name: 'Synthetic editor' }));
  const input = editor.getByLabelText('Fixture value');
  await user.clear(input);
  await user.type(input, 'Synthetic local change');
  return editor;
}

it('checks a local edit without a draft, retains it on cancel, and guards navigation and closing', async () => {
  const api = installFakeApi({ revisions: [revision] });
  const user = userEvent.setup();
  const router = mount();
  const editor = await changeFixture(user);

  await user.click(editor.getByRole('button', { name: 'Prüfen' }));
  await waitFor(() => expect(api.calls.some(call => call.path === 'registry/validate')).toBe(true));
  expect(api.drafts.size).toBe(0);

  await user.keyboard('{Escape}');
  const cancel = within(await screen.findByRole('alertdialog', { name: /Ungespeicherte Änderungen/ }));
  await user.click(cancel.getByRole('button', { name: 'Abbrechen' }));
  expect(editor.getByLabelText('Fixture value')).toHaveValue('Synthetic local change');

  await act(async () => { await router.navigate('/?fixture=second'); });
  const discard = within(await screen.findByRole('alertdialog', { name: /Ungespeicherte Änderungen/ }));
  expect(router.state.location.search).toBe('');
  await user.click(discard.getByRole('button', { name: 'Änderungen verwerfen' }));
  expect(router.state.location.search).toBe('?fixture=second');
  expect(screen.queryByRole('dialog', { name: 'Synthetic editor' })).toBeNull();
  expect(api.drafts.size).toBe(0);
  expect(api.calls.some(call => call.path === 'registry/drafts' && call.method === 'POST')).toBe(false);
});

it('merges an explicitly saved local change with the other session after the version conflict', async () => {
  const initial = makeDraft('synthetic-draft', base, { base_revision: 1 });
  const api = installFakeApi({ revisions: [revision], drafts: [initial] });
  const user = userEvent.setup();
  mount(initial as unknown as Draft);
  await user.click(screen.getByRole('button', { name: 'Open existing draft' }));
  const editor = await changeFixture(user);
  api.touchDraft(initial.draft_id, { config: { ...base, independent: true } as Registry });

  await user.click(editor.getByRole('button', { name: 'In Entwurf speichern' }));
  const conflict = within(await screen.findByRole('dialog', { name: /Versionskonflikt/ }));
  expect(api.drafts.get(initial.draft_id)?.config).toMatchObject({ marker: base.marker, independent: true });
  await user.click(conflict.getByRole('button', { name: 'Änderungen zusammenführen' }));
  await waitFor(() => expect(api.drafts.get(initial.draft_id)?.config).toMatchObject({ marker: 'Synthetic local change', independent: true }));
  expect(api.revisions[0]?.config).toEqual(base);
});
