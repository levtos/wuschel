import { useRef } from 'react';
import { useSearchParams } from 'react-router';

/** Replace the list entry, so Back returns to the same filters. One ref composes
 * several filter resets in the same event without dropping adjacent parameters. */
export function useRouteFilters() {
  const [params, setParams] = useSearchParams();
  const latest = useRef(params); latest.current = params;
  const update = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(latest.current);
    for (const [key, value] of Object.entries(patch)) { if (!value) next.delete(key); else next.set(key, value); }
    latest.current = next;
    setParams(next, { replace: true });
  };
  const field = <T extends string = string>(key: string, fallback: NoInfer<T>): [T, (value: T) => void] => [
    (params.get(key) ?? fallback) as T,
    value => update({ [key]: value === fallback ? undefined : value }),
  ];
  return { params, field, update };
}
