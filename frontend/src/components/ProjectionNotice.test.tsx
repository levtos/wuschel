import { act, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ProjectionNotice } from './ProjectionNotice';
import { LiveStore, type SocketLike } from '@/lib/live';
import { wrap } from '../../test/harness';

it('keeps the pause visible across pages and clears it only after the service reports resume', () => {
  const socket: SocketLike = { onopen: null, onmessage: null, onclose: null, onerror: null, send() {}, close() {} };
  const store = new LiveStore({ createSocket: () => socket });
  wrap(<ProjectionNotice />, { live: store });
  act(() => store.start());
  const send = (paused: boolean) => act(() => socket.onmessage?.({ data: JSON.stringify({ type: 'service_state', projection_paused: paused, load: { state: 'geschützt' } }) }));
  send(true);
  expect(screen.getByText(/Die Ausgabe an Home Assistant ist pausiert/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Ausgabe an Home Assistant verwalten' })).toHaveAttribute('href', '/settings/diagnostics/system-state');
  expect(screen.getByText(/Wuschel liefert seltener Werte an Home Assistant/)).toBeInTheDocument();
  expect(screen.getByText(/Deine Verwaltungsaktionen warten auf das Ende des Schutzes/)).toBeInTheDocument();
  send(false);
  expect(screen.queryByText(/Die Ausgabe an Home Assistant ist pausiert/)).toBeNull();
  act(() => store.stop());
});
