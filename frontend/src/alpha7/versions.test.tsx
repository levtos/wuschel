import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { ActivateDialog, RollbackDialog } from '@/features/registry/ActivationDialogs';
import { DiffView } from '@/features/registry/DiffView';
import type { Diff } from '@/lib/types';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';
import { idleLive, wrap } from '../../test/harness';
import valid from '../../test/fixtures/validate-ok.json';
import { keys } from '@/lib/queries';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function app(hash: string) {
  window.location.hash = hash;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { ...render(<QueryClientProvider client={client}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>), client };
}
const revision = (n: number) => ({ revision: n, checksum: String(n).repeat(64), config: sampleConfig(), created_at: '2026-10-16T12:00:00Z' });

it('keeps the page confirmation mounted when a background configuration update resets validation', async () => {
  const fake = installFakeApi({ revisions: [revision(1)], drafts: [makeDraft('d1', sampleConfig(), { base_revision: 1 })] });
  const { client } = app('#/changes/drafts/d1');
  await screen.findByRole('heading', { name: 'd1', level: 1 }, { timeout: 5000 });
  await userEvent.click(await screen.findByRole('button', { name: 'Änderungen prüfen' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Übernehmen' }));
  const dialog = await screen.findByRole('dialog', { name: 'Konfiguration übernehmen' });
  await within(dialog).findByRole('checkbox', { name: /Auswirkungen geprüft/ });
  fake.revisions.push(revision(2));
  await act(async () => { client.setQueryData(keys.active, revision(2)); });
  await waitFor(() => expect(document.querySelector('[data-page-header] button[data-variant="primary"]')).toHaveTextContent('Auf aktuellen Stand bringen – deine Änderungen bleiben erhalten'));
  await waitFor(() => expect(dialog).toBeVisible());
  expect(fake.calls.some(call => call.path.endsWith('/activate'))).toBe(false);
});

it('shows one global save button on the open draft page', async () => {
  installFakeApi({ drafts: [makeDraft('d1', sampleConfig())] }); app('#/changes/drafts/d1');
  await screen.findByRole('heading', { name: 'd1', level: 1 }, { timeout: 5000 });
  expect(screen.getByRole('button', { name: 'Speichern' }).closest('details')).not.toHaveAttribute('open');
  expect(screen.getAllByText('Im Entwurf gespeichert')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Änderungen prüfen' })).toHaveAttribute('data-variant', 'primary');
  await userEvent.click(screen.getByLabelText('Weitere Entwurfsaktionen'));
  expect(screen.getAllByRole('button', { name: 'Speichern' })).toHaveLength(1);
  await userEvent.click(screen.getByLabelText('Weitere Entwurfsaktionen'));
  await userEvent.click(screen.getByRole('button', { name: 'Änderungen prüfen' }));
  await screen.findByText('Keine Fehler gefunden');
  expect(screen.getAllByText('Keine Fehler')).toHaveLength(1); // only the global check chip
});

it('keeps the draft identifier, semantic checksum and deadline details under Technische Details', async () => {
  const user = userEvent.setup();
  const id = '12345678-1234-4567-8123-123456789abc';
  installFakeApi({ drafts: [makeDraft(id, sampleConfig(), { display_name: 'Neue Geräte' })] }); app(`#/changes/drafts/${id}`);
  await screen.findByRole('heading', { name: 'Neue Geräte', level: 1 }, { timeout: 5000 });
  await user.click(screen.getByRole('button', { name: 'Änderungen prüfen' }));
  expect(await screen.findByText('Der Entwurf ist vollständig und fehlerfrei.')).toBeVisible();
  expect(screen.queryByText(id)).not.toBeInTheDocument();
  expect(screen.queryByText('Semantik-Prüfsumme-Version')).not.toBeInTheDocument();
  expect(screen.queryByText('Fristen bleiben erhalten')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Technische Details' }));
  expect(screen.getByText(id)).toBeVisible();
  expect(screen.getByText('Die Prüfsumme ist ein Fingerabdruck dieses Stands. Damit lassen sich zwei Stände vergleichen. Sie ist keine Kennung.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Technische Details: Änderungen' }));
  expect(screen.getByText('Fristen bleiben erhalten')).toBeVisible();
});

it('shows time, recorded actor and note; exposes rollback only on an older version', async () => {
  installFakeApi({ revisions: [revision(1), { ...revision(2), actor: { kind: 'ingress', role: 'admin' }, note: 'Wohnzimmer ergänzt' }] }); app('#/changes/versions');
  await screen.findByText('Wohnzimmer ergänzt', {}, { timeout: 5000 });
  expect(screen.getByText('Wer: nicht bekannt', { exact: false })).toBeVisible();
  expect(screen.getByText('Administrator · App-Oberfläche', { exact: false })).toBeVisible();
  const old = screen.getByText("Konfiguration 1", { exact: true }).closest('li')!;
  const current = screen.getByText('Wohnzimmer ergänzt').closest('li')!;
  await waitFor(() => expect(within(old).getByRole('button', { name: 'Auf diese Konfiguration zurücksetzen' })).toBeEnabled());
  expect(within(current).queryByRole('button', { name: /zurücksetzen/ })).toBeNull();
  expect(within(current).getByText('aktiv', { exact: true })).toHaveClass('text-info');
  expect(current.querySelector('details[open]')).toBeNull();
  expect(screen.getByText('Wähle zwei Konfigurationen über die Kästchen links aus.')).toBeVisible();
});

it('invalidates the confirmation when the optional note changes and sends the note with both OCC tokens', async () => {
  const user = userEvent.setup(); const draft = makeDraft('d1', sampleConfig());
  const api = installFakeApi({ drafts: [draft], revisions: [revision(4)] });
  wrap(<ActivateDialog draft={draft} open onOpenChange={() => {}} />, { live: idleLive() });
  const confirm = await screen.findByRole('checkbox', { name: /Ich habe die Auswirkungen geprüft/ });
  await user.click(confirm);
  await user.type(screen.getByLabelText('Notiz zur neuen Konfiguration (freiwillig)'), 'Temperatursensor ergänzt');
  expect(screen.getByRole('button', { name: 'Jetzt übernehmen' })).toBeDisabled();
  await user.click(confirm); await user.click(screen.getByRole('button', { name: 'Jetzt übernehmen' }));
  await screen.findByText("Konfiguration 5 ist übernommen.");
  expect(api.calls.find(call => call.path.endsWith('/activate'))?.body).toEqual({ draft_version: 1, expected_active_revision: 4, note: 'Temperatursensor ergänzt', comparison_digest: 'd1:1:4', confirmations: ['impact'], request_id: expect.any(String) });
});

it('cannot confirm a rollback dialog opened for the current version', async () => {
  const api = installFakeApi({ revisions: [revision(4)] });
  wrap(<RollbackDialog revision={revision(4)} open onOpenChange={() => {}} />, { live: idleLive() });
  await screen.findByText('Zurücksetzen ist nur auf eine ältere Konfiguration möglich.');
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.getByRole('button', { name: 'Zurücksetzen' })).toBeDisabled();
  expect(api.calls.some(call => call.path === 'registry/rollback')).toBe(false);
});

it('describes first activation without warning chips or invented loss of existing contexts', () => {
  const diff = structuredClone(valid.diff) as Diff;
  diff.added = ['new.contract']; diff.objects = { contracts: { added: ['new.contract'], removed: [], changed: [], fields: {}, field_paths: {}, field_paths_truncated: [] } };
  diff.fingerprint_version = { before: null, after: 2, context_migration: true };
  diff.possibly_invalidated_contexts = ['new.contract'];
  const view = wrap(<DiffView diff={diff} config={sampleConfig({ contracts: [{ contract_id: 'new.contract' }] })} />);
  expect(screen.getByText(/Ihre Aufzeichnung beginnt nach dem Übernehmen/)).toBeVisible();
  expect(screen.queryByText("Abläufe können neu beginnen")).toBeNull();
  expect(view.container.querySelector('[class*="text-warn"]')).toBeNull();
});

it('keeps the warning for an existing context affected by changed semantics', () => {
  const diff = structuredClone(valid.diff) as Diff;
  diff.added = ['new.contract']; diff.possibly_invalidated_contexts = ['new.contract', 'old.contract'];
  wrap(<DiffView diff={diff} config={sampleConfig({ contracts: [{ contract_id: 'new.contract' }, { contract_id: 'old.contract' }] })} />);
  const row = screen.getByText("Abläufe können neu beginnen").closest('div')!;
  expect(within(row).getByText('old.contract')).toBeVisible();
  expect(within(row).queryByText('new.contract')).toBeNull();
});

it.each([
  [0, 'Du übernimmst eine Konfiguration ohne Verträge.'],
  [1, 'Keine Verträge geändert. Der vorhandene Vertrag bleibt erhalten.'],
  [2, 'Keine Verträge geändert. Alle 2 Verträge bleiben erhalten.'],
] as const)('shows the target contract count with zero affected contracts (%i)', (count, message) => {
  installFakeApi();
  const diff = structuredClone(valid.diff) as Diff;
  diff.snapshot = { revision_changes: true, affected_contracts: [] };
  const contracts = Array.from({ length: count }, (_, index) => ({ contract_id: `test.contract_${index}` }));
  wrap(<DiffView diff={diff} config={sampleConfig({ contracts })} />);
  expect(screen.getByText(message)).toBeVisible();
});
