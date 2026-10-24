import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { historyPath } from '@/lib/queries';
import type { HistoryItem, HistoryPage } from '@/lib/types';

let hours: 24 | 168 = 24;
const listeners = new Set<(value: 24 | 168) => void>();

/** One selection for all contract charts; a fixed duration, never a sample count. */
export function useHistoryWindow() {
  const [selected, setSelected] = useState(hours);
  const [until, setUntil] = useState(() => Math.floor(Date.now() / 60_000) * 60_000);
  useEffect(() => { listeners.add(setSelected); const timer = setInterval(() => setUntil(Math.floor(Date.now() / 60_000) * 60_000), 60_000); return () => { listeners.delete(setSelected); clearInterval(timer); }; }, []);
  return { hours: selected, since: until - selected * 3_600_000, until, select: (next: 24 | 168) => { hours = next; for (const notify of listeners) notify(next); } };
}

export async function windowHistory(kind: string, contractId: string | undefined, since: number, until: number, signal?: AbortSignal): Promise<HistoryItem[]> {
  const filters = { contract_id: contractId, since: new Date(since).toISOString(), until: new Date(until).toISOString(), limit: 200 };
  const rows: HistoryItem[] = [];
  const seen = new Set<string>();
  let cursor: string | null = null;
  do {
    const page: HistoryPage = await api<HistoryPage>(historyPath(kind, filters, cursor), { signal });
    rows.push(...page.items);
    cursor = page.next_cursor;
    if (cursor && seen.has(cursor)) throw new Error('history_cursor_repeated');
    if (cursor) seen.add(cursor);
  } while (cursor);
  // A single boundary sample establishes the left edge; absence remains absence.
  const boundary = await api<HistoryPage>(historyPath(kind, { contract_id: contractId, until: new Date(since - 1).toISOString(), limit: 1 }), { signal });
  return [...rows, ...boundary.items];
}

export const useWindowHistory = (kind: string, contractId: string | undefined, since: number, until: number) => useQuery({ queryKey: ['chart-history', kind, contractId, since, until], queryFn: ({ signal }) => windowHistory(kind, contractId, since, until, signal), retry: false });
