import { useState } from 'react';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import * as requestBudget from '@/lib/request-budget';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { describeError } from '@/lib/api';
import { installFakeApi, makeDraft, reply, sampleConfig } from '../../test/fakeApi';
import { wrap } from '../../test/harness';

function Probe() {
  const doc = useWorkingDocument();
  const [error, setError] = useState('');
  const [continued, setContinued] = useState(false);
  return <>
    <button onClick={async () => { setError(''); try { await doc.ensureDraft(); setContinued(true); } catch (failure) { setError(describeError(failure)); } }}>Bearbeiten</button>
    {doc.starting && <p role="status" data-testid="preparation">Vorbereitung</p>}
    {error && <p role="alert">{error}</p>}
    <output data-testid="draft">{doc.draft?.draft_id}</output>
    <output data-testid="continued">{String(continued)}</output>
  </>;
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it.each(['close', 'escape'])('cancelling the draft choice with %s is silent, creates nothing and does not continue the action', async action => {
  const fake = installFakeApi({ drafts: [makeDraft('synthetic', sampleConfig())] });
  wrap(<WorkingDocumentProvider><Probe /></WorkingDocumentProvider>);
  await userEvent.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  await screen.findByRole('dialog');
  if (action === 'close') await userEvent.click(screen.getByRole('button', { name: /schließen/i }));
  else await userEvent.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByTestId('preparation')).not.toBeInTheDocument());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByTestId('draft')).toBeEmptyDOMElement();
  expect(screen.getByTestId('continued')).toHaveTextContent('false');
  expect(fake.calls.filter(call => call.method === 'POST')).toHaveLength(0);
});

it('times out preparation and ignores a late response', async () => {
  const fake = installFakeApi();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  fake.routes.push({ match: /^registry\/active$/, method: 'GET', respond: async () => { await gate; return reply(200, null); } });
  wrap(<WorkingDocumentProvider preparationTimeoutMs={30}><Probe /></WorkingDocumentProvider>);
  await userEvent.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Das Vorbereiten dauert zu lange.');
  expect(screen.queryByTestId('preparation')).not.toBeInTheDocument();
  release();
  await waitFor(() => expect(screen.getByTestId('continued')).toHaveTextContent('false'));
  expect(screen.getByTestId('draft')).toBeEmptyDOMElement();
  expect(fake.calls.filter(call => call.method === 'POST')).toHaveLength(0);
});

it.each([1, 4])('excludes shared budget waiting before request %s without consuming its preparation deadline', async blockedCall => {
  const fake = installFakeApi();
  wrap(<WorkingDocumentProvider preparationTimeoutMs={30}><Probe /></WorkingDocumentProvider>);
  await waitFor(() => expect(fake.calls.some(call => call.path === 'registry/active')).toBe(true));
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let waiting = false, calls = 0;
  let changed: (() => void) | undefined;
  const reserve = requestBudget.reserveRequest;
  const state = vi.spyOn(requestBudget, 'budgetWaiting').mockImplementation(() => waiting);
  const subscription = vi.spyOn(requestBudget, 'subscribeBudgetWait').mockImplementation(listener => { changed = listener; return () => { changed = undefined; }; });
  const request = vi.spyOn(requestBudget, 'reserveRequest').mockImplementation(async (signal, count) => {
    if (++calls === blockedCall) { waiting = true; changed?.(); await gate; waiting = false; changed?.(); }
    return reserve(signal, count);
  });
  try {
    await userEvent.click(screen.getByRole('button', { name: 'Bearbeiten' }));
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(blockedCall));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 80)); });
    expect(screen.getByTestId('preparation')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.getByTestId('continued')).toHaveTextContent('true'));
    expect(fake.calls.filter(call => call.path === 'registry/drafts' && call.method === 'POST')).toHaveLength(1);
  } finally { release(); request.mockRestore(); subscription.mockRestore(); state.mockRestore(); }
});
