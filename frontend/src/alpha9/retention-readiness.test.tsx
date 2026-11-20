import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { ReadinessBadge } from '@/components/ReadinessBadge';
import { programVersion } from '@/lib/programVersion';
import { classifySystem } from '@/lib/status';
import type { ServiceState } from '@/lib/types';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
const service: ServiceState = { ready: false, publication_confirmed: false, persistence: 'available', ha: 'not_needed', bridge: 'not_needed', mqtt: 'disabled', reasons: ['no_active_registry', 'history_retention_required'], conditions: [
  { code: 'active_configuration', satisfied: false }, { code: 'history_retention', satisfied: false }, { code: 'first_ha_state', satisfied: null }, { code: 'publication', satisfied: null },
] };
function app() {
  window.location.hash = '#/settings/retention';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
}

it('lists missing configuration and retention immediately with their own routes and no invented complete result', async () => {
  const user = userEvent.setup();
  const conditions = classifySystem({ service, connection: 'open', hasActiveRegistry: false });
  expect(conditions.map(row => row.kind)).toEqual(['empty_configuration', 'retention_missing', 'condition_unchecked', 'condition_unchecked']);
  render(<MemoryRouter><ReadinessBadge conditions={conditions} ready={false} /></MemoryRouter>);
  expect(screen.getByText('Noch keine Konfiguration')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Nicht bereit · 4' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('Aufbewahrung fehlt')).toBeVisible();
  expect(within(dialog).getByRole('link', { name: 'Aufbewahrung festlegen' })).toHaveAttribute('href', '/settings/retention');
  expect(within(dialog).getByRole('link', { name: 'Entwurf öffnen' })).toHaveAttribute('href', '/changes/drafts');
  expect(within(dialog).getByText('Erster Stand aus Home Assistant: Noch nicht prüfbar')).toBeVisible();
  expect(within(dialog).getByText('Aktueller Stand bestätigt: Noch nicht prüfbar')).toBeVisible();
});

it('shows database-dependent and unrecognized failed conditions as uncheckable without reflecting details', () => {
  const facts = { ...service, persistence: 'persistence_unavailable', conditions: [{ code: 'database', satisfied: false }, { code: 'active_configuration', satisfied: null }, { code: 'history_retention', satisfied: null }, { code: 'future_condition', satisfied: false, details: '<script>raw backend message</script>' }] };
  const conditions = classifySystem({ service: facts, connection: 'open', hasActiveRegistry: false });
  expect(conditions.map(row => row.kind)).toEqual(['persistence_failure', 'condition_unchecked', 'condition_unchecked', 'condition_unchecked']);
  expect(JSON.stringify(conditions)).not.toContain('raw backend message');
});

it('derives Alpha and Beta from the actual program version and adds no stage to other versions', () => {
  expect(programVersion('1.0.0a9')).toBe('Wuschel 1.0.0a9 (Alpha 9)');
  expect(programVersion('1.0.0b1')).toBe('Wuschel 1.0.0b1 (Beta 1)');
  expect(programVersion('1.0.0')).toBe('Wuschel 1.0.0');
  expect(programVersion('1.0.0rc1')).toBe('Wuschel 1.0.0rc1');
});

it('offers 7/730 and creates a draft only at explicit save', async () => {
  const user = userEvent.setup(); const fake = installFakeApi(); app();
  const raw = await screen.findByLabelText('Einzelmessungen aufbewahren (Tage)');
  expect(raw).toHaveValue(7); expect(raw).toBeDisabled();
  expect(screen.getByLabelText('Stundenwerte aufbewahren (Tage)')).toHaveValue(730);
  expect(fake.drafts.size).toBe(0); expect(fake.calls.filter(call => call.method !== 'GET')).toEqual([]);
  await user.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByLabelText('Einzelmessungen aufbewahren (Tage)')).toBeEnabled();
  expect(fake.drafts.size).toBe(0);
  await user.click(within(dialog).getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => expect([...fake.drafts.values()][0]!.config.history_retention).toEqual({ raw_days: 7, rollup_days: 730, batch_size: 1000, interval_s: 3600 }));
  expect(fake.revisions).toHaveLength(0);
  expect(await screen.findByText('Die Regel ist im Entwurf gespeichert. Sie gilt erst, wenn du ihn prüfst und übernimmst.')).toBeVisible();
});

it('checks days and their relation, preserves advanced settings, and saves only the open draft', async () => {
  const user = userEvent.setup(); const fake = installFakeApi({ drafts: [makeDraft('retention', sampleConfig({ history_retention: { raw_days: 7, rollup_days: 730, batch_size: 55, interval_s: 300 } }))] });
  sessionStorage.setItem('terra.workingDraft', 'retention'); app();
  await user.click(await screen.findByRole('button', { name: 'Bearbeiten' }));
  const editor = within(await screen.findByRole('dialog'));
  const raw = editor.getByLabelText('Einzelmessungen aufbewahren (Tage)');
  const rollup = editor.getByLabelText('Stundenwerte aufbewahren (Tage)');
  await user.clear(raw); await user.type(raw, '10'); await user.clear(rollup); await user.type(rollup, '5');
  expect(raw).toHaveAttribute('aria-invalid', 'true');
  expect(rollup).toHaveAttribute('aria-invalid', 'true');
  expect(editor.getAllByText(/muss größer oder gleich/)).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'In Entwurf speichern' })).toBeDisabled();
  await user.clear(rollup); await user.type(rollup, '20');
  await user.click(screen.getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => expect(fake.drafts.get('retention')!.config.history_retention).toEqual({ raw_days: 10, rollup_days: 20, batch_size: 55, interval_s: 300 }));
  expect(fake.revisions).toHaveLength(0);
});
