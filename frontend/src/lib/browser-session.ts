import { focusManager, onlineManager } from '@tanstack/react-query';

export interface BrowserSessionState { visible: boolean; blocked: boolean }
export class SessionExpired extends Error { constructor(readonly status = 401) { super('ingress_session_expired'); } }

/** One document owns one terminal session gate; only a new document resets it. */
export class BrowserSession {
  private state: BrowserSessionState = { visible: this.visible(), blocked: false };
  private listeners = new Set<() => void>();
  private queue: Promise<void> = Promise.resolve();
  private listening = false;
  private documentVersion = 0;
  getDocumentVersion() { return this.documentVersion; }

  private visible() { return typeof document === 'undefined' || document.visibilityState !== 'hidden'; }
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listen(); this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private listen() {
    if (this.listening || typeof document === 'undefined') return;
    this.listening = true;
    document.addEventListener('visibilitychange', this.visibilityChanged);
    window.addEventListener('online', this.visibilityChanged);
    window.addEventListener('offline', this.visibilityChanged);
    this.visibilityChanged();
  }
  private visibilityChanged = () => this.update(this.visible(), this.state.blocked);
  private update(visible: boolean, blocked: boolean) {
    const changed = visible !== this.state.visible || blocked !== this.state.blocked;
    this.state = changed ? { visible, blocked } : this.state;
    focusManager.setFocused(visible && !blocked);
    onlineManager.setOnline(visible && !blocked && (typeof navigator === 'undefined' || navigator.onLine));
    if (changed) this.listeners.forEach(listener => listener());
  }
  expire() { this.update(this.visible(), true); }

  async waitReady(signal?: AbortSignal): Promise<void> {
    this.listen();
      if (!this.visible() && !this.state.blocked) await new Promise<void>((resolve, reject) => {
        const cleanup = () => { unsubscribe(); signal?.removeEventListener('abort', abort); };
        const abort = () => { cleanup(); reject(signal?.reason ?? new DOMException('Aborted', 'AbortError')); };
        const unsubscribe = this.subscribe(() => { if (this.state.visible || this.state.blocked) { cleanup(); resolve(); } });
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
      });
      if (this.state.blocked) throw new SessionExpired();
      signal?.throwIfAborted();
  }

  async run<T>(work: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    const version = this.documentVersion;
    const previous = this.queue;
    let release!: () => void;
    this.queue = new Promise<void>(resolve => { release = resolve; });
    await previous;
    try {
      await this.waitReady(signal);
      if (version !== this.documentVersion) throw new DOMException('old_document', 'AbortError');
      return await work();
    } finally { release(); }
  }

  /** Called by the test browser fixture when it creates a fresh page. */
  newDocument() {
    this.documentVersion++;
    this.queue = Promise.resolve();
    this.update(this.visible(), false);
  }
}

export const browserSession = new BrowserSession();

export function assertIngressResponse(response: Response, version = browserSession.getDocumentVersion()) {
  if (version !== browserSession.getDocumentVersion()) throw new DOMException('old_document', 'AbortError');
  if ([401, 403].includes(response.status) || response.headers.get('Content-Type')?.includes('text/html')) {
    browserSession.expire();
    throw new SessionExpired([401, 403].includes(response.status) ? response.status : 401);
  }
}

/** A new backend process reloads this browser once; a failed reload gets a notice. */
export function reloadAfterRestart(epoch: string, reload: () => void = () => location.reload()) {
  const key = 'wuschel.reloadedEpoch';
  try {
    if (sessionStorage.getItem(key) === epoch) { browserSession.expire(); return; }
    sessionStorage.setItem(key, epoch);
  } catch { browserSession.expire(); return; }
  browserSession.expire();
  reload();
}
