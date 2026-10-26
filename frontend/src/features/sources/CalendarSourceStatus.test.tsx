import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { installFakeApi, reply } from '../../../test/fakeApi';
import { CalendarSourceStatus } from './CalendarSourceStatus';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it.each([null, 'ha_unavailable'])('shows cache dates and the fixed failure code %s without event contents', async error_code => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^sources\/calendar-status$/, respond: () => reply(200, [{ binding_id: 'binding.synthetic', source_id: 'source.synthetic', fetched_at: '2026-10-26T12:00:00Z', expires_at: '2026-10-26T13:00:00Z', error_code }]) });
  render(<QueryClientProvider client={new QueryClient()}><CalendarSourceStatus sourceId="source.synthetic" /></QueryClientProvider>);
  expect(await screen.findByText('Zuletzt gelesen')).toBeVisible();
  expect(screen.getByText('Gültig bis')).toBeVisible();
  if (error_code) expect(screen.getByText('Kalender konnte nicht gelesen werden · ha_unavailable')).toBeVisible();
  else expect(screen.queryByText(/Kalender konnte nicht gelesen werden/)).not.toBeInTheDocument();
});
