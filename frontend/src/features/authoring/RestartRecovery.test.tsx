import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as connection from '@/lib/browser-session';
import * as liveContext from '@/lib/live-context';
import type { Draft } from '@/lib/types';
import { t } from '@/i18n';
import { idleLive } from '../../../test/harness';
import { installFakeApi, makeDraft, reply, sampleConfig } from '../../../test/fakeApi';
import { RestartRecovery } from './RestartRecovery';
import { WorkingDocumentProvider, useWorkingDocument } from './WorkingDocument';

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

function Editor({ draft }: { draft: Draft }) {
  const doc = useWorkingDocument();
  useEffect(() => doc.open(draft), [doc.open, draft]);
  return <>
    <button disabled={!doc.config} onClick={() => doc.edit(config => ({ ...config, synthetic_note: 'Synthetic local edit' }))}>Edit fixture</button>
    <output aria-label="Local fixture">{String(doc.config?.synthetic_note ?? '')}</output>
    <output aria-label="Dirty fixture">{String(doc.dirty)}</output>
    <RestartRecovery />
  </>;
}

function fixture() {
  const draft = makeDraft('synthetic-restart', sampleConfig());
  const fake = installFakeApi({ drafts: [draft] });
  const snapshot = idleLive().getSnapshot();
  const live = vi.spyOn(liveContext, 'useLive').mockReturnValue({ ...snapshot, epochId: 'synthetic-before' });
  const reload = vi.spyOn(connection, 'reloadAfterRestart').mockImplementation(() => {});
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const ui = <QueryClientProvider client={client}><MemoryRouter><WorkingDocumentProvider><Editor draft={draft as Draft} /></WorkingDocumentProvider></MemoryRouter></QueryClientProvider>;
  const view = render(ui);
  const restart = () => { live.mockReturnValue({ ...snapshot, epochId: 'synthetic-after' }); view.rerender(<QueryClientProvider client={client}><MemoryRouter><WorkingDocumentProvider><Editor draft={draft as Draft} /></WorkingDocumentProvider></MemoryRouter></QueryClientProvider>); };
  return { draft, fake, reload, restart, view };
}

it('waits for the dirty working document to be saved before reloading once', async () => {
  const { fake, draft, reload, restart } = fixture();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit fixture' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit fixture' }));
  expect(screen.getByLabelText('Dirty fixture')).toHaveTextContent('true');
  let release!: (response: Response) => void;
  fake.routes.push({ match: /^registry\/drafts\/synthetic-restart$/, method: 'PUT', respond: (_path, body) => new Promise(resolve => { release = response => { fake.drafts.set(draft.draft_id, { ...draft, config: body.config, draft_version: 2 }); resolve(response); }; }) });
  act(restart);
  await waitFor(() => expect(fake.calls.some(call => call.method === 'PUT')).toBe(true));
  expect(reload).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Local fixture')).toHaveTextContent('Synthetic local edit');
  await act(async () => { release(reply(200, { ...draft, config: { ...draft.config, synthetic_note: 'Synthetic local edit' }, draft_version: 2 })); });
  await waitFor(() => expect(reload).toHaveBeenCalledExactlyOnceWith('synthetic-after'));
  expect(fake.drafts.get(draft.draft_id)?.config.synthetic_note).toBe('Synthetic local edit');
  expect(fake.activations).toEqual([]);
  act(restart);
  expect(reload).toHaveBeenCalledTimes(1);
});

it.each(['conflict', 'failure'] as const)('keeps unsaved input on restart %s without a reload or save loop', async mode => {
  const { fake, draft, reload, restart, view } = fixture();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit fixture' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit fixture' }));
  if (mode === 'conflict') fake.touchDraft(draft.draft_id, { config: { ...draft.config, synthetic_note: 'Synthetic other session' } });
  else fake.routes.push({ match: /^registry\/drafts\/synthetic-restart$/, method: 'PUT', respond: () => reply(500, { error: 'storage_unavailable' }) });
  act(restart);
  expect(await screen.findByText(t('b22.connection.keepEdits'))).toBeVisible();
  expect(screen.getByLabelText('Local fixture')).toHaveTextContent('Synthetic local edit');
  expect(screen.getByLabelText('Dirty fixture')).toHaveTextContent('true');
  expect(reload).not.toHaveBeenCalled();
  act(restart);
  expect(fake.calls.filter(call => call.method === 'PUT')).toHaveLength(1);
  expect(fake.activations).toEqual([]);
  view.unmount();
});
