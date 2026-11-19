import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { installFakeApi, reply, sampleConfig } from '../../../test/fakeApi';
import { wrap } from '../../../test/harness';
import { CalendarCoverageWarnings, CalendarSourceStatus } from './CalendarSourceStatus';

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

it('shows a horizon gap as a standalone warning and links to its source', async () => {
  const config = sampleConfig({ sources: [{ source_id: 'source.synthetic', calendar_coverage_months: 12 }] });
  const fake = installFakeApi({ revisions: [{ revision: 1, checksum: 'test', created_at: '2026-10-26T12:00:00Z', config }] });
  const message = 'Feiertagskalender: Lücke vom 01.05.2027 bis 01.10.2027.';
  const row = { binding_id: 'binding.synthetic', source_id: 'source.synthetic', fetched_at: '2026-10-26T12:00:00Z', expires_at: '2026-10-26T13:00:00Z', error_code: null };
  fake.routes.push({ match: /^sources\/calendar-status$/, respond: () => reply(200, [
    { ...row, coverage: { covered: false, gap: { start: '2027-05-01T00:00:00+02:00', end: '2027-10-01T00:00:00+02:00' }, message } },
    { ...row, binding_id: 'binding.other', coverage: { covered: false, gap: null, message: 'Prüfung läuft.' } },
  ]) });
  wrap(<CalendarCoverageWarnings />);
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Quelle prüfen' })).toHaveAttribute('href', '/sources/devices?source_id=source.synthetic');
  expect(screen.queryByText('Prüfung läuft.')).not.toBeInTheDocument();
});
