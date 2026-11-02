import { t as uiText } from '@/i18n';
import { createContext, useContext, useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { createBrowserLive, LiveStore, type LiveSnapshot } from './live';
import { useInfo } from './queries';
import { useQueryClient } from '@tanstack/react-query';
import { browserSession } from './browser-session';

const LiveContext = createContext<LiveStore | null>(null);

/** Owns one WebSocket for the whole workbench; torn down with the provider. */
export function LiveProvider({ children, store }: { children: ReactNode; store?: LiveStore }) {
  const info = useInfo();
  const cache = useQueryClient();
  const installationId = useRef<string | undefined>(undefined);
  installationId.current = info.data?.installation_id;
  const live = useMemo(() => store ?? createBrowserLive(() => installationId.current), [store]);
  const session = useSyncExternalStore(browserSession.subscribe, browserSession.getSnapshot);
  useEffect(() => {
    let version = live.getSnapshot().catalogVersion;
    let pending: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = live.subscribe(() => {
      const current = live.getSnapshot().catalogVersion;
      if (current === version) return;
      version = current;
      if (pending) return;
      pending = setTimeout(() => {
        pending = undefined;
        for (const name of ['catalog-entries', 'catalog-masters', 'catalog-details', 'catalog-detail', 'catalog-history']) void cache.invalidateQueries({ queryKey: [name] });
      }, 0);
    });
    return () => { unsubscribe(); if (pending) clearTimeout(pending); };
  }, [live, cache]);
  useEffect(() => {
    if (session.visible && !session.blocked) live.start(); else live.stop();
    return () => live.stop();
  }, [live, session.visible, session.blocked]);
  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveSnapshot {
  const store = useContext(LiveContext);
  if (!store) throw new Error(uiText("lib.live-context.liveprovider_fehlt_509518"));
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}
