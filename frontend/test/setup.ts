import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';
import { browserSession } from '@/lib/browser-session';

// General component suites focus on their feature. The shared real IndexedDB
// scheduler is exercised separately with two React clients and controlled time.
vi.mock('@/lib/request-budget', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/request-budget')>(), reserveRequest: async () => {}, coolDownRequests: async () => {} }));

// jsdom lacks these browser APIs used by Base UI and React Flow.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}
Element.prototype.scrollIntoView ??= () => {};

// Each fixture starts a new browser document; a prior terminal Ingress response
// must not leak into the next independent scenario.
beforeEach(() => browserSession.newDocument());

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
