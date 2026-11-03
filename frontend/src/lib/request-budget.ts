import { t as uiText } from '@/i18n';
/** Shared by all tabs, reloads and Ingress prefixes on this origin. Only timing
 * metadata is persisted. IndexedDB read/write transactions serialize reservations. */
interface Budget { sent: number[]; blockedUntil: number }
const WINDOW = 61_000;
const LIMIT = 100; // Leave headroom below the server's 120/min for WS handshakes.
let database: Promise<IDBDatabase> | undefined;
let waiting = 0;
const waitListeners = new Set<() => void>();
export const budgetWaiting = () => waiting > 0;
export function subscribeBudgetWait(listener: () => void): () => void {
  waitListeners.add(listener);
  return () => { waitListeners.delete(listener); };
}
function notifyWait() { for (const listener of waitListeners) listener(); }

function db(): Promise<IDBDatabase> {
  return database ??= new Promise((resolve, reject) => {
    const request = indexedDB.open('terra.request-budget', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('timing');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { database = undefined; reject(new Error(uiText("lib.request-budget.gemeinsames_anfragebudget_ist_nicht_verfugbar_b99b92"))); };
  });
}

async function timing(cooldown = 0, count = 1): Promise<number> {
  const connection = await db();
  return new Promise((resolve, reject) => {
    const tx = connection.transaction('timing', 'readwrite');
    const store = tx.objectStore('timing');
    const request = store.get('ingress');
    let wait = 0;
    request.onsuccess = () => {
      const now = Date.now();
      const previous = request.result as Budget | undefined;
      const state: Budget = {
        sent: (previous?.sent ?? []).filter((at) => at > now - WINDOW),
        blockedUntil: Math.max(previous?.blockedUntil ?? 0, cooldown ? now + cooldown : 0),
      };
      const excess = state.sent.length + count - LIMIT;
      wait = Math.max(0, state.blockedUntil - now, excess > 0 ? state.sent[excess - 1]! + WINDOW - now : 0);
      if (!cooldown && !wait) state.sent.push(...Array<number>(count).fill(now));
      store.put(state, 'ingress');
    };
    tx.oncomplete = () => resolve(wait);
    tx.onerror = tx.onabort = () => reject(new Error(uiText("lib.request-budget.gemeinsames_anfragebudget_ist_nicht_verfugbar_b99b92")));
  });
}

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  waiting++; notifyWait();
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
  }).finally(() => { waiting--; notifyWait(); });
}

/** Waiting schedules a request once; it never repeats an HTTP operation. */
export async function reserveRequest(signal?: AbortSignal, count = 1): Promise<void> {
  if (!Number.isInteger(count) || count < 1 || count > LIMIT) throw new RangeError('invalid_request_count');
  for (;;) {
    signal?.throwIfAborted();
    const wait = await timing(0, count);
    signal?.throwIfAborted();
    if (!wait) return;
    await pause(wait, signal);
  }
}

/** 429 pauses all tabs. The rejected operation itself is never replayed. */
export async function coolDownRequests(retryAfter: string | null): Promise<void> {
  const seconds = retryAfter === null ? NaN : Number(retryAfter);
  const duration = Number.isFinite(seconds) ? seconds * 1000 : retryAfter ? Date.parse(retryAfter) - Date.now() : 0;
  await timing(Math.max(WINDOW, Number.isFinite(duration) ? duration : 0));
}
