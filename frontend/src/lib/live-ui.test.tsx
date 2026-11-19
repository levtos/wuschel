import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { ContractLiveState } from '@/components/ContractLiveState';
import { StatusChips } from '@/components/Shell';
import { useLive } from './live-context';
import { LiveStore, type SocketLike } from './live';
import { wrap } from '../../test/harness';
import { installFakeApi, sampleConfig } from '../../test/fakeApi';

it('keeps unknown visible and stale through a resync; only a full snapshot restores the view', async () => {
  const socket: SocketLike = { onopen: null, onmessage: null, onclose: null, onerror: null, send() {}, close() {} };
  const store = new LiveStore({ createSocket: () => socket });
  function View() {
    const live = useLive();
    return <><button onClick={() => store.start()}>Connect</button><output>{live.connection}</output><output data-testid="confirmed">{JSON.stringify(live.publicationConfirmed)}</output>{live.contracts?.c && <ContractLiveState envelope={live.contracts.c} stale={live.stale} />}</>;
  }
  const send = (value: unknown) => act(() => socket.onmessage?.({ data: JSON.stringify(value) }));
  wrap(<View />, { live: store });
  await userEvent.setup().click(screen.getByText('Connect'));
  const unknown = { contract_id: 'c', contract_health: 'degraded', available: false, computed_at: '2026-10-13T12:00:00Z', published_at: '2026-10-13T12:00:00Z', fields: { value: { status: 'unknown', value: null, quality: 'degraded', reasons: [], evidence: [] } } };
  send({ type: 'snapshot', epoch_id: 'e', registry_revision: 1, publication_seq: 5, contracts: { c: unknown } });
  expect(screen.getByTestId('value-value')).toHaveTextContent('unbekannt');
  expect(screen.getByTestId('confirmed')).toHaveTextContent('null');
  send({ type: 'delta', epoch_id: 'e', prev_seq: 7, publication_seq: 8, contracts: {} });
  expect(screen.getByText('resyncing')).toBeInTheDocument();
  expect(screen.getByText('Stand möglicherweise veraltet')).toBeInTheDocument();
  send({ type: 'delta', epoch_id: 'e', prev_seq: 5, publication_seq: 6, contracts: {} });
  expect(screen.getByTestId('value-value')).toHaveTextContent('unbekannt');
  send({ type: 'snapshot', epoch_id: 'new', registry_revision: 2, publication_seq: 1, publication_confirmed: true, contracts: { c: unknown } });
  expect(screen.getByText('open')).toBeInTheDocument();
  expect(screen.queryByText('Stand möglicherweise veraltet')).toBeNull();
  expect(screen.getByTestId('confirmed')).toHaveTextContent('true');
  act(() => store.stop());
});

it('shows neutral connection status on tab return, with a deadline and immediate service faults', async () => {
  installFakeApi({ revisions: [{ revision: 1, checksum: 'test', created_at: '2026-11-19T10:00:00Z', config: sampleConfig() }] });
  const socket: SocketLike = { onopen: null, onmessage: null, onclose: null, onerror: null, send() {}, close() {} };
  let releaseReconnect!: () => void;
  const beforeConnect = vi.fn<() => Promise<void>>()
    .mockResolvedValueOnce(undefined)
    .mockImplementation(() => new Promise(resolve => { releaseReconnect = resolve; }));
  const store = new LiveStore({ createSocket: () => socket, beforeConnect });
  const showTab = (visible: boolean) => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: visible ? 'visible' : 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  };
  const ready = { type: 'service_state', ready: true, publication_confirmed: true, persistence: 'available', ha: 'not_needed', bridge: 'not_needed', mqtt: 'disabled', reasons: [] };
  const send = (message: unknown) => act(() => socket.onmessage?.({ data: JSON.stringify(message) }));
  const view = wrap(<StatusChips />, { live: store });
  try {
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    send(ready);
    send({ type: 'snapshot', epoch_id: 'test', registry_revision: 1, publication_seq: 1, contracts: {} });
    await screen.findByRole('button', { name: 'Bereit' });

    vi.useFakeTimers();
    act(() => showTab(false));
    act(() => showTab(true));
    expect(screen.getByRole('button', { name: 'Verbinde …' })).toBeVisible();
    expect(socket.onmessage).toBeNull();
    await act(async () => releaseReconnect());
    send({ ...ready, ready: false, persistence: 'persistence_unavailable' });
    expect(screen.getByRole('button', { name: 'Nicht bereit · 1' })).toBeVisible();
    send(ready);
    send({ type: 'resync_required' });
    act(() => vi.advanceTimersByTime(9_999));
    expect(screen.getByRole('button', { name: 'Verbinde …' })).toBeVisible();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole('button', { name: 'Nicht bereit · 1' })).toBeVisible();
    send({ type: 'snapshot', epoch_id: 'test', registry_revision: 1, publication_seq: 2, contracts: {} });
    expect(screen.getByRole('button', { name: 'Bereit' })).toBeVisible();
  } finally {
    view.unmount();
    showTab(true);
    Reflect.deleteProperty(document, 'visibilityState');
    vi.useRealTimers();
  }
});
