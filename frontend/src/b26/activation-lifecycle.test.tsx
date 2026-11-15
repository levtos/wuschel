import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router';
import { beforeEach, expect, it, vi } from 'vitest';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { ActivateDialog } from '@/features/registry/ActivationDialogs';
import { AppErrorBoundary } from '@/components/AppErrorBoundary';
import { LiveProvider } from '@/lib/live-context';
import { idleLive } from '../../test/harness';
import type { Draft } from '@/lib/types';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';

beforeEach(() => sessionStorage.clear());
const draft = () => makeDraft('synthetic-draft', sampleConfig(), { base_revision: 1 });
function Probe({ candidate, activation = false }: { candidate: Draft; activation?: boolean }) {
  const doc = useWorkingDocument();
  const [open, setOpen] = useState(true);
  return <>
    <button onClick={() => doc.open(candidate)}>open</button>
    <button onClick={() => void doc.ensureDraft().catch(() => {})}>start</button>
    <output data-testid="working">{doc.draft?.draft_id ?? 'closed'}</output>
    {activation && <ActivateDialog draft={candidate} open={open} onOpenChange={setOpen} />}
  </>;
}
function mount(children: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <LiveProvider store={idleLive()}><WorkingDocumentProvider validateDebounceMs={3_600_000}>{children}</WorkingDocumentProvider></LiveProvider>
  </QueryClientProvider>);
}

it('renders the outdated draft chooser without any router', async () => {
  const candidate = draft();
  installFakeApi({ drafts: [candidate], revisions: [{ revision: 2, config: sampleConfig(), checksum: 'synthetic', created_at: '' }] });
  mount(<Probe candidate={candidate} />);
  await userEvent.click(screen.getByText('open'));
  await userEvent.click(screen.getByText('start'));
  const dialog = await screen.findByRole('dialog', { name: 'Entwurf wählen' });
  expect(within(dialog).getByRole('link')).toHaveAttribute('href', '#/changes/drafts/synthetic-draft');
});

it('does not restore a completed draft after reloading the tab', async () => {
  const candidate = { ...draft(), lifecycle: 'completed' as const };
  const fake = installFakeApi({ drafts: [candidate] });
  sessionStorage.setItem('terra.workingDraft', candidate.draft_id);
  mount(<Probe candidate={candidate} />);
  await waitFor(() => expect(fake.calls.some(call => call.path.endsWith(candidate.draft_id))).toBe(true));
  await waitFor(() => expect(sessionStorage.getItem('terra.workingDraft')).toBeNull());
  expect(screen.getByTestId('working')).toHaveTextContent('closed');
});

it.each(['X', 'Esc', 'Schließen', 'Konfiguration ansehen', 'Technische Details'])('closes the completed document before success is left via %s', async action => {
  const candidate = draft();
  installFakeApi({ drafts: [candidate], revisions: [{ revision: 1, config: sampleConfig(), checksum: 'synthetic', created_at: '' }] });
  mount(<MemoryRouter><Probe candidate={candidate} activation /></MemoryRouter>);
  await userEvent.click(screen.getByText('open'));
  const dialog = await screen.findByRole('dialog', { name: 'Konfiguration übernehmen' });
  await userEvent.click(await within(dialog).findByRole('checkbox', { name: /Auswirkungen geprüft/ }));
  await userEvent.click(within(dialog).getByRole('button', { name: 'Jetzt übernehmen' }));
  await within(dialog).findByText('Konfiguration 2 ist übernommen.');
  expect(screen.getByTestId('working')).toHaveTextContent('closed');
  expect(sessionStorage.getItem('terra.workingDraft')).toBeNull();
  if (action === 'Esc') await userEvent.keyboard('{Escape}');
  else if (action === 'X') await userEvent.click(within(dialog).getAllByRole('button', { name: 'Schließen' })[0]!);
  else if (action === 'Schließen') await userEvent.click(within(dialog).getAllByRole('button', { name: 'Schließen' }).at(-1)!);
  else await userEvent.click(within(dialog).getByRole(action === 'Konfiguration ansehen' ? 'link' : 'button', { name: action }));
  expect(screen.getByTestId('working')).toHaveTextContent('closed');
});

it('contains errors outside the router with a German reload action', () => {
  const log = vi.spyOn(console, 'error').mockImplementation(() => {});
  function Broken(): never { throw new Error('synthetic technical details'); }
  render(<AppErrorBoundary><Broken /></AppErrorBoundary>);
  expect(screen.getByRole('alert')).toHaveTextContent('Die Seite konnte nicht angezeigt werden.');
  expect(screen.getByRole('button', { name: 'Neu laden' })).toBeVisible();
  expect(screen.queryByText('synthetic technical details')).toBeNull();
  expect(log).toHaveBeenCalled();
  log.mockRestore();
});
