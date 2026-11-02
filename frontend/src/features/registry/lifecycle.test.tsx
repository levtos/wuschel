import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ConflictDialog } from '@/features/authoring/DraftBar';
import { useWorkingDocument, WorkingDocumentProvider } from '@/features/authoring/WorkingDocument';
import type { Draft } from '@/lib/types';
import { LiveStore, type SocketLike } from '@/lib/live';
import { LiveProvider } from '@/lib/live-context';
import { installFakeApi, makeDraft, sampleConfig, type FakeApi } from '../../../test/fakeApi';
import { idleLive } from '../../../test/harness';
import { ActivateDialog, RollbackDialog } from './ActivationDialogs';

const user = userEvent.setup();
it('checks and explicitly activates a configuration over HTTP without randomUUID', async () => {
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto), randomUUID: undefined });
  try {
    const api = installFakeApi({ drafts: [makeDraft('http', sampleConfig())], revisions: [{ revision: 4, config: sampleConfig(), checksum: 'synthetic', created_at: 'x' }] });
    renderDialog(<ActivateDialog draft={api.drafts.get('http') as unknown as Draft} open onOpenChange={() => {}} />);
    await screen.findByText(/Hier siehst du die geplanten Änderungen/);
    await user.click(screen.getByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ }));
    await user.click(screen.getByRole('button', { name: 'Jetzt übernehmen' }));
    await screen.findByText('Konfiguration 5 ist übernommen.');
    expect(api.calls.find(call => call.path.endsWith('/activate'))?.body).toMatchObject({ request_id: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  } finally { vi.unstubAllGlobals(); }
});

/** Minimal surface over the working document, driven like the real UI. */
function Surface({ draft }: { draft: Draft }) {
  const doc = useWorkingDocument();
  return (
    <div>
      <button onClick={() => doc.open(draft)}>open</button>
      <button onClick={() => doc.edit((c) => ({ ...c, marker: 'edited' }))}>edit</button>
      <button onClick={() => void doc.save()}>save</button>
      <button onClick={doc.undo}>undo</button>
      <button onClick={doc.redo}>redo</button>
      <output data-testid="state">{JSON.stringify({ dirty: doc.dirty, version: doc.draft?.draft_version, marker: (doc.config as { marker?: string } | null)?.marker ?? null, undo: doc.canUndo, redo: doc.canRedo, id: doc.draft?.draft_id })}</output>
      <ConflictDialog />
    </div>
  );
}

function mountSurface(api: FakeApi, draftId = 'd1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkingDocumentProvider validateDebounceMs={3_600_000}>
          <Surface draft={api.drafts.get(draftId) as unknown as Draft} />
        </WorkingDocumentProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const state = () => JSON.parse(screen.getByTestId('state').textContent!) as { dirty: boolean; version: number; marker: string | null; undo: boolean; redo: boolean; id: string };

describe('draft lifecycle: working document and OCC', () => {
  it('tracks edits, undo/redo and saves with the draft version', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())] });
    mountSurface(api);
    await user.click(screen.getByText('open'));
    expect(state()).toMatchObject({ dirty: false, version: 1, marker: null });
    await user.click(screen.getByText('edit'));
    expect(state()).toMatchObject({ dirty: true, marker: 'edited', undo: true });
    await user.click(screen.getByText('undo'));
    expect(state()).toMatchObject({ dirty: false, marker: null, redo: true });
    await user.click(screen.getByText('redo'));
    await user.click(screen.getByText('save'));
    await waitFor(() => expect(state()).toMatchObject({ dirty: false, version: 2, marker: 'edited' }));
    const put = api.calls.find((c) => c.method === 'PUT')!;
    expect(put.body).toMatchObject({ draft_version: 1, config: { marker: 'edited' } });
  });

  it('never overwrites silently on a version conflict and offers explicit choices', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())] });
    mountSurface(api);
    await user.click(screen.getByText('open'));
    await user.click(screen.getByText('edit'));
    api.touchDraft('d1', { config: sampleConfig({ marker: 'other-session' }) }); // someone else saved
    await user.click(screen.getByText('save'));
    const dialog = await screen.findByRole('dialog', { name: /Versionskonflikt/ });
    expect(api.drafts.get('d1')!.config).toMatchObject({ marker: 'other-session' }); // untouched
    expect(state().dirty).toBe(true); // local edits preserved

    // Cancel keeps both sides as they are.
    await user.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(state()).toMatchObject({ dirty: true, marker: 'edited' });
    expect(api.drafts.get('d1')!.config).toMatchObject({ marker: 'other-session' });
  });

  it('keeps local changes and requires a choice for conflicting saved input', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())] });
    mountSurface(api);
    await user.click(screen.getByText('open'));
    await user.click(screen.getByText('edit'));
    api.touchDraft('d1', { config: sampleConfig({ marker: 'other-session' }) });
    await user.click(screen.getByText('save'));
    const merge = await screen.findByRole('button', { name: 'Änderungen zusammenführen' });
    expect(merge).toBeDisabled();
    expect(screen.getAllByRole('radio').every(r => !(r as HTMLInputElement).checked)).toBe(true);
    expect(api.drafts.get('d1')!.config).toMatchObject({ marker: 'other-session' });
    await user.click(screen.getByRole('radio', { name: /Deine Eingabe/ }));
    await user.click(merge);
    await waitFor(() => expect(state().dirty).toBe(false));
    expect(api.drafts.size).toBe(1);
    expect(api.drafts.get('d1')!.config).toMatchObject({ marker: 'edited' });
  });

  it('taking the server state discards local edits only after the explicit choice', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())] });
    mountSurface(api);
    await user.click(screen.getByText('open'));
    await user.click(screen.getByText('edit'));
    api.touchDraft('d1', { config: sampleConfig({ marker: 'other-session' }) });
    await user.click(screen.getByText('save'));
    await user.click(await screen.findByRole('button', { name: "Gespeicherten Stand laden" }));
    await waitFor(() => expect(state()).toMatchObject({ dirty: false, marker: 'other-session', version: 2 }));
  });

  it('merges independent changes against the last read version without replacing the server input', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())] });
    mountSurface(api);
    await user.click(screen.getByText('open'));
    await user.click(screen.getByText('edit'));
    api.touchDraft('d1', { config: sampleConfig({ independent: 'other-session' }) });
    await user.click(screen.getByText('save'));
    await user.click(await screen.findByRole('button', { name: 'Änderungen zusammenführen' }));
    await waitFor(() => expect(api.drafts.get('d1')!.config).toMatchObject({ marker: 'edited', independent: 'other-session' }));
    const puts = api.calls.filter((c) => c.method === 'PUT');
    expect(puts.at(-1)!.body).toMatchObject({ draft_version: 2 }); // the version the user just saw
  });

  it('shows the unsaved validation result addressed by JSON pointer', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig({ __invalid: true }))] });
    function Probe() {
      const doc = useWorkingDocument();
      return (
        <div>
          <button onClick={() => doc.open(api.drafts.get('d1') as unknown as Draft)}>open</button>
          <button onClick={() => void doc.validateNow()}>validate</button>
          <output data-testid="issues">{doc.validation.issues.map((i) => `${i.code}@${i.path}`).join('|')}</output>
        </div>
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <WorkingDocumentProvider validateDebounceMs={3_600_000}>
          <Probe />
        </WorkingDocumentProvider>
      </QueryClientProvider>,
    );
    await user.click(screen.getByText('open'));
    await user.click(screen.getByText('validate'));
    await waitFor(() => expect(screen.getByTestId('issues').textContent).toContain('definition.unknown_rule_input@/definitions/0/outputs/value'));
    expect(api.calls.some((c) => c.path === 'registry/validate')).toBe(true);
    expect(api.drafts.get('d1')!.draft_version).toBe(1); // validation never writes
  });
});

function renderDialog(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LiveProvider store={idleLive()}>{ui}</LiveProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('activation requires an explicit, version-checked confirmation', () => {
  it('does not offer activation for an invalid draft and shows the issues', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig({ __invalid: true }))] });
    renderDialog(<ActivateDialog draft={api.drafts.get('d1') as unknown as Draft} open onOpenChange={() => {}} />);
    await screen.findByText(/nicht gültig/);
    expect(screen.getByRole('button', { name: "Jetzt übernehmen" })).toBeDisabled();
    expect(screen.getByText('Der Eingang ist in dieser Vorlage nicht deklariert.')).toBeInTheDocument();
    expect(api.calls.some((c) => c.path.endsWith('/activate'))).toBe(false);
  });

  it('activates only after the confirmation checkbox and sends both OCC tokens', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())], revisions: [{ revision: 4, config: sampleConfig(), checksum: 'c'.repeat(64), created_at: 'x' }] });
    renderDialog(<ActivateDialog draft={api.drafts.get('d1') as unknown as Draft} open onOpenChange={() => {}} />);
    const button = await screen.findByRole('button', { name: "Jetzt übernehmen" });
    await screen.findByText(/Hier siehst du die geplanten Änderungen/);
    expect(button).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ }));
    expect(screen.getByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ }).closest('footer')).not.toBeNull();
    expect(button).toBeEnabled();
    await user.click(button);
    await screen.findByText("Konfiguration 5 ist übernommen.");
    expect(api.calls.find((c) => c.path.endsWith('/activate'))!.body).toEqual({ draft_version: 1, expected_active_revision: 4, comparison_digest: 'd1:1:4', confirmations: ['impact'], note: '', request_id: expect.any(String) });
  });

  it('reports an active-revision conflict honestly and activates nothing', async () => {
    const api = installFakeApi({ drafts: [makeDraft('d1', sampleConfig())], revisions: [{ revision: 1, config: sampleConfig(), checksum: 'c'.repeat(64), created_at: 'x' }] });
    api.failNext.push({ path: /\/activate$/, status: 409, body: { error: 'active_revision_conflict', details: [] } });
    renderDialog(<ActivateDialog draft={api.drafts.get('d1') as unknown as Draft} open onOpenChange={() => {}} />);
    await screen.findByText(/Hier siehst du die geplanten Änderungen/);
    await user.click(screen.getByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ }));
    await user.click(screen.getByRole('button', { name: "Jetzt übernehmen" }));
    await screen.findByText("Dein Entwurf braucht den aktuellen Stand.");
    expect(screen.queryByText(/ist übernommen/)).toBeNull();
    expect(api.revisions).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Auf aktuellen Stand bringen – deine Änderungen bleiben erhalten' })).toHaveAttribute('href', '/changes/drafts/d1?continue=1');
  });

  it('rollback creates a new revision from history and also needs confirmation', async () => {
    const r1 = { revision: 1, config: sampleConfig({ marker: 'one' }), checksum: 'a'.repeat(64), created_at: 'x' };
    const r2 = { revision: 2, config: sampleConfig({ marker: 'two' }), checksum: 'b'.repeat(64), created_at: 'y' };
    const api = installFakeApi({ revisions: [r1, r2] });
    renderDialog(<RollbackDialog revision={r1} open onOpenChange={() => {}} />);
    await screen.findByText(/Hier siehst du die geplanten Änderungen/);
    const go = screen.getByRole('button', { name: "Zurücksetzen" });
    expect(go).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /als neue Konfiguration 3/ }));
    await user.click(go);
    await screen.findByText('Konfiguration 3 ist übernommen.');
    expect(api.revisions).toHaveLength(3);
    expect(api.revisions[2]!.config).toMatchObject({ marker: 'one' }); // historical content
    expect(api.revisions[0]!.config).toMatchObject({ marker: 'one' }); // history untouched
    expect(api.calls.find((c) => c.path === 'registry/rollback')!.body).toEqual({ revision: 1, expected_active_revision: 2 });
  });

  it('rollback loses the race against a newer activation (OCC) and changes nothing', async () => {
    const r1 = { revision: 1, config: sampleConfig(), checksum: 'a'.repeat(64), created_at: 'x' };
    const api = installFakeApi({ revisions: [r1, { ...r1, revision: 2 }] });
    api.failNext.push({ path: /^registry\/rollback$/, status: 409, body: { error: 'active_revision_conflict', details: [] } });
    renderDialog(<RollbackDialog revision={r1} open onOpenChange={() => {}} />);
    await screen.findByText(/Hier siehst du die geplanten Änderungen/);
    await user.click(screen.getByRole('checkbox', { name: /als neue Konfiguration 3/ }));
    await user.click(screen.getByRole('button', { name: "Zurücksetzen" }));
    await screen.findByText("Konflikt – nichts wurde übernommen");
    expect(api.revisions).toHaveLength(2);
  });
});

describe('T1B-03: the check, the diff and the activation always concern the same draft version', () => {
  function Switcher({ api }: { api: FakeApi }) {
    const [id, setId] = useState('a');
    const [open, setOpen] = useState(false);
    const draft = api.drafts.get(id) as unknown as Draft;
    return (
      <div>
        <button onClick={() => setId('b')}>zu B</button>
        <button onClick={() => setId('a')}>zu A</button>
        <button onClick={() => setOpen(true)}>öffnen</button>
        {/* One instance for the whole test: no remount when the draft changes. */}
        <ActivateDialog draft={draft} open={open} onOpenChange={setOpen} />
      </div>
    );
  }
  const two = () => installFakeApi({ drafts: [makeDraft('a', sampleConfig({ marker: 'A' }), { display_name: 'Alpha' }), makeDraft('b', sampleConfig({ marker: 'B' }), { display_name: 'Beta' })] });
  const validated = (api: FakeApi) => api.calls.filter((c) => c.path.endsWith('/validate')).map((c) => c.path);

  it('A → B without remount validates B, names B and activates B (never the validation of A)', async () => {
    const api = two();
    renderDialog(<Switcher api={api} />);
    await user.click(screen.getByText('zu B'));
    await user.click(screen.getByText('öffnen'));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText(/Hier siehst du die geplanten Änderungen/);
    expect(within(dialog).getByText(/Beta/)).toBeInTheDocument();
    expect(validated(api)).toEqual(['registry/drafts/b/validate']);
    await user.click(within(dialog).getByRole('checkbox', { name: /Auswirkungen geprüft/ }));
    await user.click(within(dialog).getByRole('button', { name: "Jetzt übernehmen" }));
    await within(dialog).findByText(/ist übernommen/);
    expect(api.calls.find((c) => c.path.endsWith('/activate'))!.path).toBe('registry/drafts/b/activate');
    expect(api.revisions[0]!.config).toMatchObject({ marker: 'B' });
  });

  it('switching the draft while the dialog is open drops the old result and the old confirmation', async () => {
    const api = two();
    renderDialog(<Switcher api={api} />);
    await user.click(screen.getByText('öffnen'));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText(/Hier siehst du die geplanten Änderungen/);
    await user.click(within(dialog).getByRole('checkbox', { name: /Auswirkungen geprüft/ }));
    expect(within(dialog).getByRole('button', { name: "Jetzt übernehmen" })).toBeEnabled();

    await user.click(screen.getByText('zu B', { selector: 'button' }));
    // A's confirmation is gone and B must be checked and confirmed on its own
    await waitFor(() => expect(validated(api)).toContain('registry/drafts/b/validate'));
    await within(dialog).findByText(/Beta/);
    await waitFor(() => expect(within(dialog).getByRole('checkbox', { name: /Auswirkungen geprüft/ })).not.toBeChecked());
    expect(within(dialog).getByRole('button', { name: "Jetzt übernehmen" })).toBeDisabled();
    await user.click(within(dialog).getByRole('checkbox', { name: /Auswirkungen geprüft/ }));
    await user.click(within(dialog).getByRole('button', { name: "Jetzt übernehmen" }));
    await within(dialog).findByText(/ist übernommen/);
    expect(api.calls.filter((c) => c.path.endsWith('/activate')).map((c) => c.path)).toEqual(['registry/drafts/b/activate']);
  });

  it('refuses to vouch for a version that is no longer the saved one', async () => {
    const api = two();
    const stale = { ...(api.drafts.get('a') as unknown as Draft) }; // what the user has open: v1
    api.touchDraft('a', { config: sampleConfig({ marker: 'andere Sitzung' }) }); // server is now v2
    renderDialog(<ActivateDialog draft={stale} open onOpenChange={() => {}} />);
    await screen.findByText('Prüfergebnis veraltet');
    expect(screen.queryByRole('checkbox', { name: /Auswirkungen geprüft/ })).toBeNull();
    expect(screen.getByRole('button', { name: "Jetzt übernehmen" })).toBeDisabled();
    expect(api.calls.some((c) => c.path.endsWith('/validate'))).toBe(false);
  });

  it('a newer active revision (seen on the live channel) invalidates a finished check', async () => {
    const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig())], revisions: [{ revision: 1, config: sampleConfig(), checksum: 'c'.repeat(64), created_at: 'x' }] });
    const sockets: { onmessage: ((e: { data: string }) => void) | null; onopen: (() => void) | null }[] = [];
    const store = new LiveStore({
      createSocket: () => {
        const socket = { onopen: null, onmessage: null, onclose: null, onerror: null, send() {}, close() {} };
        sockets.push(socket);
        return socket as unknown as SocketLike;
      },
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <LiveProvider store={store}>
            <ActivateDialog draft={api.drafts.get('a') as unknown as Draft} open onOpenChange={() => {}} />
          </LiveProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText(/Hier siehst du die geplanten Änderungen/);
    await user.click(screen.getByRole('checkbox', { name: /Auswirkungen geprüft/ }));
    expect(screen.getByRole('button', { name: "Jetzt übernehmen" })).toBeEnabled();
    act(() => sockets[0]!.onmessage!({ data: JSON.stringify({ type: 'snapshot', epoch_id: 'e', publication_seq: 9, registry_revision: 2, publication_confirmed: true, contracts: {} }) }));
    await screen.findByText('Dein Entwurf braucht den aktuellen Stand.');
    expect(screen.getByRole('button', { name: "Jetzt übernehmen" })).toBeDisabled();
    expect(screen.queryByRole('checkbox', { name: /Auswirkungen geprüft/ })).toBeNull();
  });
});
