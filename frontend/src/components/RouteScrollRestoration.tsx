import { useLayoutEffect, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router';

/** Restore the actual scroll container after lazy pages and query results arrive. */
export function RouteScrollRestoration() {
  const location = useLocation(); const navigation = useNavigationType();
  const positions = useRef(new Map<string, number>());
  const previous = useRef('');
  useLayoutEffect(() => {
    const target = document.getElementById('main'); const content = document.getElementById('route-content');
    if (!target || !content) return;
    const desired = navigation === 'POP' ? positions.current.get(location.key) ?? 0 : navigation === 'REPLACE' && previous.current === location.pathname ? target.scrollTop : 0;
    previous.current = location.pathname;
    let pending = true;
    let observer: ResizeObserver | undefined;
    const apply = () => {
      if (!pending || target.scrollHeight - target.clientHeight < desired) return;
      target.scrollTop = desired; pending = false; observer?.disconnect();
    };
    const cancel = () => { pending = false; observer?.disconnect(); };
    if (typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(apply); observer.observe(content); }
    const frame = requestAnimationFrame(apply);
    target.addEventListener('wheel', cancel, { passive: true });
    target.addEventListener('touchstart', cancel, { passive: true });
    const key = (event: KeyboardEvent) => { if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End'].includes(event.key)) cancel(); };
    target.addEventListener('keydown', key);
    const native = history.scrollRestoration; history.scrollRestoration = 'manual';
    return () => {
      positions.current.set(location.key, pending ? desired : target.scrollTop);
      if (positions.current.size > 100) positions.current.delete(positions.current.keys().next().value!);
      cancelAnimationFrame(frame); observer?.disconnect();
      target.removeEventListener('wheel', cancel); target.removeEventListener('touchstart', cancel); target.removeEventListener('keydown', key);
      history.scrollRestoration = native;
    };
  }, [location.key, location.pathname, navigation]);
  return null;
}
