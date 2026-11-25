export interface LoadMeasurement {
  metric: string;
  value: number;
  threshold: number;
}

type LoadWaitState =
  | { kind: 'waiting'; cancel: () => void }
  | { kind: 'protected'; measurements: LoadMeasurement[] }
  | null;

let state: LoadWaitState = null;
const listeners = new Set<() => void>();

function publish(next: LoadWaitState) {
  state = next;
  listeners.forEach(listener => listener());
}

export const loadWait = {
  snapshot: () => state,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  start(cancel: () => void) { publish({ kind: 'waiting', cancel }); },
  reject(measurements: LoadMeasurement[]) { publish({ kind: 'protected', measurements }); },
  close() {
    if (state?.kind === 'waiting') state.cancel();
    else publish(null);
  },
  finish() { publish(null); },
};
