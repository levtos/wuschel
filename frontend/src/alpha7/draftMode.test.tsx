import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { installFakeApi, reply } from '../../test/fakeApi';
import type { UserEvent } from '@testing-library/user-event';
import { idleLive } from '../../test/harness';

function app(hash = '#/sources/devices') {
  window.location.hash = hash;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
}
// #97 P3 replaces the global mode switch with an action at an object.
async function beginDraft(user: UserEvent) {
  await user.click(await screen.findByRole('button', { name: 'Quelle hinzufügen' }));
  const dialog = await screen.findByRole('dialog', { name: 'Gerät aus Home Assistant hinzufügen' });
  await user.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
}
async function draftPage(user: UserEvent) {
  await user.click(await screen.findByRole('link', { name: 'Änderungen ansehen' }));
}
async function review(user: UserEvent) {
  await draftPage(user);
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Übernehmen' }) ?? screen.getByRole('button', { name: 'Änderungen prüfen' })).toBeEnabled());
  const check = screen.queryByRole('button', { name: 'Änderungen prüfen' });
  if (check) await user.click(check);
  await user.click(await screen.findByRole('button', { name: 'Übernehmen' }, { timeout: 5000 }));
}
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it.each([
  ['#/contracts/list/all', 'Neuer Vertrag'],
  ['#/templates/own', 'Neue Vorlage'],
])('opens creation on %s with an automatic draft and no route change', async (hash, name) => {
  const user = userEvent.setup(); const fake = installFakeApi(); app(hash);
  await user.click(await screen.findByRole('button', { name }, { timeout: 5000 }));
  expect(await screen.findByRole('dialog', { name })).toBeVisible();
  expect(fake.drafts.size).toBe(1);
  expect(window.location.hash).toBe(hash);
});

it('makes a successful explicit check visible', async () => {
  const user = userEvent.setup(); installFakeApi(); app();
  await beginDraft(user);
  await draftPage(user);
  await user.click(await screen.findByRole('button', { name: 'Änderungen prüfen' }));
  expect(await screen.findByText('Keine Fehler')).toBeVisible();
});

it('starts a named draft automatically from a creation action and stays on the source page', async () => {
  const user = userEvent.setup(); const fake = installFakeApi(); app();
  await user.click(await screen.findByRole('button', { name: 'Quelle hinzufügen' }));
  await user.click(await screen.findByRole('button', { name: 'Ohne Gerät anlegen' }));
  await screen.findByLabelText('Name');
  expect(fake.drafts.size).toBe(1);
  expect([...fake.drafts.values()][0]?.display_name).toMatch(/^Änderungen vom /);
  expect(window.location.hash).toMatch(/^#\/sources\/devices(?:\?|$)/);
  expect(screen.queryByRole('group', { name: 'Ansicht' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Name')).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Änderungen übernehmen' })).toBeVisible();
  expect(screen.queryByText(/schreibgeschützte Ansicht/)).not.toBeInTheDocument();
});

it('returns to an object with unsaved edits retained and requires its own edit action', async () => {
  const user = userEvent.setup(); const fake = installFakeApi(); app();
  await user.click(await screen.findByRole('button', { name: 'Quelle hinzufügen' }));
  await user.click(await screen.findByRole('button', { name: 'Ohne Gerät anlegen' }));
  const name = await screen.findByLabelText('Name');
  await user.type(name, 'Fenster im Zimmer');
  await user.click(screen.getByRole('link', { name: 'Verknüpfungen' }));
  expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  await user.click(screen.getByRole('link', { name: 'Meine Quellen' }));
  expect(await screen.findByLabelText('Name')).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  expect(await screen.findByLabelText('Name')).toHaveValue('Fenster im Zimmer');
  expect(screen.getByLabelText('Name')).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Ansehen' })).not.toBeInTheDocument();
  expect(fake.drafts.size).toBe(1);
  expect(fake.revisions).toHaveLength(0);
});

it('opens the error list in place and gives visible feedback after an explicit check', async () => {
  const user = userEvent.setup(); const fake = installFakeApi();
  fake.routes.push({ match: /^registry\/validate$/, method: 'POST', respond: () => reply(200, { valid: false, issues: [{ code: 'config_incomplete', path: '/contracts', message: 'raw technical message' }] }) });
  app();
  await beginDraft(user);
  await draftPage(user);
  await user.click(await screen.findByRole('button', { name: 'Änderungen prüfen' }));
  await user.click(await screen.findByRole('button', { name: '1 Fehler' }));
  expect(await screen.findByRole('dialog', { name: 'Prüfergebnis' })).toBeVisible();
  expect(window.location.hash).toMatch(/^#\/changes\/drafts\//);
});

it('saves the candidate and opens confirmation from the single draft action without activating', async () => {
  const user = userEvent.setup(); const fake = installFakeApi(); app();
  await user.click(await screen.findByRole('button', { name: 'Quelle hinzufügen' }));
  await user.click(await screen.findByRole('button', { name: 'Ohne Gerät anlegen' }));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Gerät aus Home Assistant hinzufügen' })).not.toBeInTheDocument());
  await screen.findByLabelText('Name');
  await review(user);
  const dialog = await screen.findByRole('dialog', { name: 'Konfiguration übernehmen' });
  await within(dialog).findByRole('checkbox', { name: /Auswirkungen geprüft/ });
  await waitFor(() => expect([...fake.drafts.values()][0]!.draft_version).toBeGreaterThan(1));
  expect(fake.revisions).toHaveLength(0);
  expect(window.location.hash).toMatch(/^#\/changes\/drafts\//);
});

it('shows the committed activation acknowledgement while cache refreshes are still pending', async () => {
  const user = userEvent.setup(); const fake = installFakeApi(); app();
  await beginDraft(user);
  await review(user);
  const dialog = await screen.findByRole('dialog', { name: 'Konfiguration übernehmen' });
  await user.click(await within(dialog).findByRole('checkbox', { name: /Auswirkungen geprüft/ }));
  let release: ((response: Response) => void) | undefined;
  fake.routes.push({ match: /^registry\/active$/, respond: () => new Promise(resolve => { release = resolve; }) });
  await user.click(within(dialog).getByRole('button', { name: 'Jetzt übernehmen' }));
  expect(await within(dialog).findByText("Konfiguration 1 ist übernommen.")).toBeVisible();
  expect(fake.revisions).toHaveLength(1);
  release?.(reply(200, fake.revisions[0]));
});
