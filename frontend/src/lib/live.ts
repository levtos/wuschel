import type { Envelope, ServiceState } from './types';

/**
 * Live contract state over the Alpha-4 WebSocket (protocol 1).
 *
 * Rules implemented here, all taken from docs/api.md:
 *  - hello → welcome → subscribe(all) → snapshot, then deltas;
 *  - a delta is only applied when it continues the known epoch and publication
 *    sequence (prev_seq); anything else triggers a fresh subscribe (resync);
 *  - resync_required re-subscribes, there is no replay;
 *  - installation_mismatch is terminal, going_away/close reconnect;
 *  - while disconnected the last state stays visible but is flagged `stale`.
 * The browser never derives quality or freshness itself.
 */

export type Connection =
  | 'connecting'
  | 'open'
  | 'resyncing'
  | 'reconnecting'
  | 'offline'
  | 'mismatch'
  | 'stopped';

export interface BrowserConnectionEvent {
  id: string;
  at: string;
  kind: 'ha_connected' | 'ha_disconnected';
}

export interface LiveSnapshot {
  catalogVersion: number;
  connection: Connection;
  /** True while the shown contract state may be older than the server's. */
  stale: boolean;
  service: ServiceState | null;
  contracts: Record<string, Envelope> | null;
  epochId: string | null;
  publicationSeq: number | null;
  registryRevision: number | null;
  publicationConfirmed: boolean | null;
  installationId: string | null;
  lastEventAt: number | null;
  resyncCount: number;
  /** Observed server status transitions in this browser session; never persisted. */
  connectionEvents: BrowserConnectionEvent[];
}

export interface SocketLike {
  onopen: ((event?: unknown) => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: ((event?: unknown) => void) | null;
  onerror: ((event?: unknown) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface LiveOptions {
  createSocket: () => SocketLike;
  expectedInstallationId?: () => string | undefined;
  isOnline?: () => boolean;
  now?: () => number;
  reconnectDelaysMs?: number[];
}

const INITIAL: LiveSnapshot = {
  catalogVersion: 0,
  connection: 'connecting',
  stale: true,
  service: null,
  contracts: null,
  epochId: null,
  publicationSeq: null,
  registryRevision: null,
  publicationConfirmed: null,
  installationId: null,
  lastEventAt: null,
  resyncCount: 0,
  connectionEvents: [],
};

export class LiveStore {
  private state: LiveSnapshot = INITIAL;
  private listeners = new Set<() => void>();
  private socket: SocketLike | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private attempt = 0;
  private stopped = true;
  private subscriptionId = 0;
  private eventSequence = 0;

  constructor(private readonly options: LiveOptions) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): LiveSnapshot => this.state;

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    clearTimeout(this.timer);
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = socket.onmessage = socket.onopen = socket.onerror = null;
      socket.close();
    }
    this.patch({ connection: 'stopped', stale: true });
  }

  private patch(change: Partial<LiveSnapshot>): void {
    const next = { ...this.state, ...change };
    const beforeHa = this.state.service?.ha;
    const afterHa = change.service?.ha;
    if (beforeHa !== undefined && afterHa !== undefined && afterHa !== beforeHa) {
      const kind: BrowserConnectionEvent['kind'] | null = afterHa === 'connected' ? 'ha_connected' : beforeHa === 'connected' && ['disconnected', 'unavailable'].includes(afterHa) ? 'ha_disconnected' : null;
      if (kind) next.connectionEvents = [{ id: `connection-${++this.eventSequence}`, at: new Date(this.options.now?.() ?? Date.now()).toISOString(), kind }, ...this.state.connectionEvents].slice(0, 100);
    }
    this.state = next;
    this.listeners.forEach((listener) => listener());
  }

  private online(): boolean {
    return this.options.isOnline ? this.options.isOnline() : true;
  }

  private connect(): void {
    if (this.stopped) return;
    const socket = this.options.createSocket();
    this.socket = socket;
    this.patch({ connection: this.attempt === 0 ? 'connecting' : 'reconnecting' });
    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: 'hello',
          protocol_version: 1,
          ...(this.options.expectedInstallationId?.()
            ? { expected_installation_id: this.options.expectedInstallationId() }
            : {}),
        }),
      );
    };
    socket.onmessage = (event) => {
      if (socket !== this.socket) return;
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
      this.handle(socket, message);
    };
    socket.onclose = () => {
      if (socket !== this.socket) return;
      this.socket = null;
      if (this.stopped || this.state.connection === 'mismatch') return;
      this.patch({ connection: this.online() ? 'reconnecting' : 'offline', stale: true });
      const delays = this.options.reconnectDelaysMs ?? [1000, 2000, 5000, 10000, 15000];
      const delay = delays[Math.min(this.attempt, delays.length - 1)]!;
      this.attempt += 1;
      this.timer = setTimeout(() => this.connect(), delay);
    };
    socket.onerror = () => {
      /* onclose follows; no separate state needed */
    };
  }

  private subscribeAll(socket: SocketLike): void {
    this.subscriptionId += 1;
    socket.send(JSON.stringify({ type: 'subscribe', id: `workbench-${this.subscriptionId}`, contracts: null }));
  }

  private handle(socket: SocketLike, message: Record<string, unknown>): void {
    const now = this.options.now?.() ?? Date.now();
    switch (message.type) {
      case 'welcome':
        this.attempt = 0;
        this.patch({
          installationId: typeof message.installation_id === 'string' ? message.installation_id : null,
          lastEventAt: now,
        });
        this.subscribeAll(socket);
        break;
      case 'installation_mismatch':
        this.patch({ connection: 'mismatch', stale: true, connectionEvents: [] });
        break;
      case 'service_state':
        this.patch({ service: message as unknown as ServiceState, lastEventAt: now });
        break;
      case 'catalog_changed':
        this.patch({ catalogVersion: this.state.catalogVersion + 1, lastEventAt: now });
        break;
      case 'snapshot':
        if (typeof message.epoch_id !== 'string' || !Number.isSafeInteger(message.publication_seq) || !message.contracts || typeof message.contracts !== 'object') {
          this.resync(socket);
          break;
        }
        this.patch({
          connection: 'open',
          catalogVersion: this.state.catalogVersion + 1,
          resyncCount: this.state.resyncCount + (this.state.epochId !== null && message.epoch_id !== this.state.epochId && this.state.connection !== 'resyncing' ? 1 : 0),
          stale: false,
          contracts: (message.contracts ?? {}) as Record<string, Envelope>,
          epochId: String(message.epoch_id ?? ''),
          publicationSeq: Number(message.publication_seq ?? 0),
          registryRevision: Number(message.registry_revision ?? 0),
          publicationConfirmed: typeof message.publication_confirmed === 'boolean' ? message.publication_confirmed : null,
          lastEventAt: now,
        });
        break;
      case 'delta': {
        const continues =
          this.state.connection === 'open' && !this.state.stale &&
          this.state.contracts !== null &&
          message.epoch_id === this.state.epochId &&
          message.prev_seq === this.state.publicationSeq &&
          Number.isSafeInteger(message.publication_seq) && Number(message.publication_seq) > Number(message.prev_seq) &&
          message.contracts !== null && typeof message.contracts === 'object';
        if (!continues) {
          this.resync(socket);
          break;
        }
        this.patch({
          contracts: (message.contracts ?? {}) as Record<string, Envelope>,
          publicationSeq: Number(message.publication_seq),
          registryRevision: Number(message.registry_revision ?? this.state.registryRevision ?? 0),
          lastEventAt: now,
        });
        break;
      }
      case 'resync_required':
        this.resync(socket);
        break;
      case 'going_away':
        this.patch({ connection: 'reconnecting', stale: true });
        break;
      default:
        break;
    }
  }

  private resync(socket: SocketLike): void {
    if (this.state.connection === 'resyncing') return;
    this.patch({ connection: 'resyncing', stale: true, resyncCount: this.state.resyncCount + 1 });
    this.subscribeAll(socket);
  }
}

export function createBrowserLive(expectedInstallationId?: () => string | undefined): LiveStore {
  return new LiveStore({
    expectedInstallationId,
    isOnline: () => navigator.onLine,
    createSocket: () => {
      const url = new URL('./api/v1/ws', location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      return new WebSocket(url) as unknown as SocketLike;
    },
  });
}
