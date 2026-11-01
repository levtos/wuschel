import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LiveStore, type SocketLike } from './live';

class FakeSocket implements SocketLike {
  onopen: SocketLike['onopen'] = null;
  onmessage: SocketLike['onmessage'] = null;
  onclose: SocketLike['onclose'] = null;
  onerror: SocketLike['onerror'] = null;
  sent: Record<string, unknown>[] = [];
  closed = false;
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  drop() {
    this.onclose?.();
  }
}

const envelope = (id: string, value: boolean | null) => ({
  contract_id: id,
  fields: { value: value === null ? { status: 'unknown', value: null, quality: 'degraded', reasons: [], evidence: [] } : { status: 'valid', value, quality: 'healthy', reasons: [], evidence: [] } },
});
const snapshot = (seq: number, epoch = 'e1', contracts: Record<string, unknown> = { a: envelope('a', true) }) => ({
  type: 'snapshot', epoch_id: epoch, publication_seq: seq, registry_revision: 3, publication_confirmed: true, contracts,
});

let sockets: FakeSocket[];
function create(extra: Partial<ConstructorParameters<typeof LiveStore>[0]> = {}) {
  sockets = [];
  const store = new LiveStore({
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    expectedInstallationId: () => 'inst-1',
    reconnectDelaysMs: [1000, 2000],
    ...extra,
  });
  store.start();
  return store;
}
const current = () => sockets[sockets.length - 1]!;

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('live protocol', () => {
  it('refreshes catalog subscribers on a catalog event and after reconnect snapshots', () => {
    const store = create();
    current().open(); current().receive({ type: 'welcome' }); current().receive(snapshot(5));
    const before = store.getSnapshot().catalogVersion;
    current().receive({ type: 'catalog_changed' });
    expect(store.getSnapshot().catalogVersion).toBe(before + 1);
    expect(store.getSnapshot().publicationSeq).toBe(5);
    current().drop(); vi.advanceTimersByTime(1000);
    current().open(); current().receive({ type: 'welcome' }); current().receive(snapshot(5));
    expect(store.getSnapshot().catalogVersion).toBeGreaterThan(before + 1);
    store.stop();
  });
  it('records only observed HA connection transitions and clears them on installation mismatch', () => {
    const store = create({ now: () => Date.parse('2026-10-16T12:00:00Z') });
    const service = (ha: string) => current().receive({ type: 'service_state', ha });
    service('connected');
    expect(store.getSnapshot().connectionEvents).toEqual([]);
    service('unavailable'); service('unavailable'); service('connected');
    expect(store.getSnapshot().connectionEvents.map(event => event.kind)).toEqual(['ha_connected', 'ha_disconnected']);
    service('not_needed');
    expect(store.getSnapshot().connectionEvents).toHaveLength(2);
    current().receive({ type: 'installation_mismatch' });
    expect(store.getSnapshot().connectionEvents).toEqual([]);
    store.stop();
  });

  it('bounds connection observations to the latest 100 events', () => {
    const store = create();
    current().receive({ type: 'service_state', ha: 'connected' });
    for (let i = 0; i < 150; i++) current().receive({ type: 'service_state', ha: i % 2 ? 'connected' : 'disconnected' });
    expect(store.getSnapshot().connectionEvents).toHaveLength(100);
    expect(new Set(store.getSnapshot().connectionEvents.map(event => event.id)).size).toBe(100);
    store.stop();
  });
  it('says hello with the expected installation id, subscribes after welcome and takes the snapshot', () => {
    const store = create();
    current().open();
    expect(current().sent[0]).toEqual({ type: 'hello', protocol_version: 1, expected_installation_id: 'inst-1' });
    current().receive({ type: 'welcome', installation_id: 'inst-1' });
    expect(current().sent[1]).toMatchObject({ type: 'subscribe', contracts: null });
    current().receive(snapshot(5));
    expect(store.getSnapshot()).toMatchObject({ connection: 'open', stale: false, publicationSeq: 5, epochId: 'e1', registryRevision: 3 });
  });

  it('applies a delta only when it continues epoch and sequence', () => {
    const store = create();
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(5));
    current().receive({ type: 'delta', epoch_id: 'e1', prev_seq: 5, publication_seq: 6, registry_revision: 3, contracts: { a: envelope('a', false) } });
    expect(store.getSnapshot().publicationSeq).toBe(6);
    expect(store.getSnapshot().contracts?.a?.fields.value?.value).toBe(false);
  });

  it('treats a sequence gap as resync: stale, re-subscribed, no guessed state', () => {
    const store = create();
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(5));
    const before = current().sent.length;
    current().receive({ type: 'delta', epoch_id: 'e1', prev_seq: 7, publication_seq: 8, registry_revision: 3, contracts: { a: envelope('a', false) } });
    expect(store.getSnapshot()).toMatchObject({ connection: 'resyncing', stale: true, publicationSeq: 5, resyncCount: 1 });
    expect(store.getSnapshot().contracts?.a?.fields.value?.value).toBe(true);
    expect(current().sent.length).toBe(before + 1);
    current().receive(snapshot(8));
    expect(store.getSnapshot()).toMatchObject({ connection: 'open', stale: false, publicationSeq: 8 });
  });

  it('resyncs on an epoch change and on resync_required', () => {
    const store = create();
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(5));
    current().receive({ type: 'delta', epoch_id: 'e2', prev_seq: 5, publication_seq: 6, contracts: {} });
    expect(store.getSnapshot().connection).toBe('resyncing');
    current().receive(snapshot(1, 'e2'));
    current().receive({ type: 'resync_required', reason: 'slow_subscriber' });
    expect(store.getSnapshot()).toMatchObject({ connection: 'resyncing', resyncCount: 2 });
  });

  it('keeps an unknown field unknown, never off/false', () => {
    const store = create();
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(1, 'e1', { a: envelope('a', null) }));
    const field = store.getSnapshot().contracts!.a!.fields.value!;
    expect(field.status).toBe('unknown');
    expect(field.value).toBeNull();
  });

  it('reconnects with backoff after a drop, keeps the last state flagged stale, then resyncs', () => {
    const store = create();
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(5));
    current().drop();
    expect(store.getSnapshot()).toMatchObject({ connection: 'reconnecting', stale: true, publicationSeq: 5 });
    expect(store.getSnapshot().contracts).not.toBeNull();
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(999);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(9, 'e2'));
    expect(store.getSnapshot()).toMatchObject({ connection: 'open', stale: false, publicationSeq: 9, epochId: 'e2', resyncCount: 1 });
  });

  it('backs off progressively while the service stays down', () => {
    create();
    current().drop();
    vi.advanceTimersByTime(1000);
    current().drop();
    vi.advanceTimersByTime(1999);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(3);
  });

  it('shows offline when the browser reports no network', () => {
    const store = create({ isOnline: () => false });
    current().drop();
    expect(store.getSnapshot().connection).toBe('offline');
  });

  it('stops for good on an installation mismatch', () => {
    const store = create();
    current().open();
    current().receive({ type: 'installation_mismatch' });
    current().drop();
    vi.advanceTimersByTime(60_000);
    expect(store.getSnapshot().connection).toBe('mismatch');
    expect(sockets).toHaveLength(1);
  });

  it('records service_state without touching contract values (readiness ≠ quality)', () => {
    const store = create();
    current().open();
    current().receive({ type: 'welcome' });
    current().receive(snapshot(5));
    current().receive({ type: 'service_state', ready: false, publication_confirmed: false, persistence: 'persistence_unavailable', ha: 'connected', bridge: 'connected', mqtt: 'disabled', reasons: ['persistence_unavailable'] });
    expect(store.getSnapshot().service?.ready).toBe(false);
    expect(store.getSnapshot().contracts?.a?.fields.value?.status).toBe('valid');
  });

  it('ignores messages from a replaced socket and cleans up on stop', () => {
    const store = create();
    const old = current();
    old.drop();
    vi.advanceTimersByTime(1000);
    old.receive(snapshot(99));
    expect(store.getSnapshot().publicationSeq).toBeNull();
    store.stop();
    expect(current().closed).toBe(true);
    expect(store.getSnapshot().connection).toBe('stopped');
  });
});
