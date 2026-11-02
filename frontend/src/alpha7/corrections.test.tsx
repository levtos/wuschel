import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, expect, it } from 'vitest';
import { DraftBar } from '@/features/authoring/DraftBar';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import { DiffView } from '@/features/registry/DiffView';
import type { ValidationResult } from '@/lib/types';
import verdict from '../../test/fixtures/validate-ok.json';
import { delayedBody, installFakeApi, makeDraft, reply, sampleConfig } from '../../test/fakeApi';

const emptyVerdict = (): ValidationResult => ({ ...structuredClone(verdict), diff: { ...structuredClone(verdict.diff), analysis: 'static', predicts_values: false, added: [], affected_contracts: [], objects: {} } });
function Editor() {
  const doc = useWorkingDocument();
  return <><DraftBar /><button onClick={() => void doc.validateNow()}>Prüfen</button><button onClick={() => doc.open(makeDraft('comparison', sampleConfig({ normalized_defaults: true })))}>Open</button><button onClick={() => doc.edit(config => ({ ...config, edited: true }))}>Edit</button>{doc.validation.state === 'done' && !doc.validation.stale && doc.validation.result?.diff && <DiffView diff={doc.validation.result.diff} />}</>;
}
function editor(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(<QueryClientProvider client={client}><MemoryRouter><WorkingDocumentProvider validateDebounceMs={5}><Editor /></WorkingDocumentProvider></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it('uses only the current server object diff, including zero, while active export is still loading', async () => {
  const fake = installFakeApi(); const user = userEvent.setup();
  let respond: ((response: Response) => void) | undefined;
  fake.routes.push({ match: /^registry\/active\/export$/, respond: () => delayedBody(new Promise(() => {})) });
  fake.routes.push({ match: /^registry\/validate$/, respond: () => new Promise(resolve => { respond = resolve; }) });
  editor(); await user.click(screen.getByText('Open'));
  expect(screen.getByLabelText('Anzahl der Änderungen')).toHaveTextContent('…');
  await waitFor(() => expect(respond).toBeDefined()); respond!(reply(200, emptyVerdict()));
  await waitFor(() => expect(screen.getAllByLabelText('Anzahl der Änderungen').map(node => node.textContent)).toEqual(['Keine Änderungen', 'Keine Änderungen']));
  await user.click(screen.getByRole('button', { name: 'Prüfen' }));
  expect(screen.getByLabelText('Anzahl der Änderungen')).toHaveTextContent('…');
  const result = emptyVerdict();
  result.diff!.objects = { contracts: { added: ['a'], changed: ['b'], removed: ['c'], fields: {}, field_paths: {}, field_paths_truncated: [] }, sources: { added: ['s'], changed: [], removed: [], fields: {}, field_paths: {}, field_paths_truncated: [] } };
  result.diff!.added = ['a']; // Repeated top-level contract must not be counted twice.
  respond!(reply(200, result));
  await waitFor(() => expect(screen.getAllByLabelText('Anzahl der Änderungen').map(node => node.textContent)).toEqual(['4 Änderungen', '4 Änderungen']));
  await user.click(screen.getByText('Edit'));
  expect(screen.getByLabelText('Anzahl der Änderungen')).toHaveTextContent('…');
});

it('invalidates a comparison when the active version changes and automatically requests the new server diff', async () => {
  const fake = installFakeApi(); const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let respond: ((response: Response) => void) | undefined;
  fake.routes.push({ match: /^registry\/validate$/, respond: () => new Promise(resolve => { respond = resolve; }) });
  editor(client); await user.click(screen.getByText('Open'));
  await waitFor(() => expect(respond).toBeDefined()); respond!(reply(200, verdict));
  await waitFor(() => expect(screen.getAllByLabelText('Anzahl der Änderungen')[0]).toHaveTextContent('16 Änderungen'));
  respond = undefined;
  fake.revisions.push({ revision: 1, config: sampleConfig(), checksum: 'sum', created_at: '2026-01-01T12:00:00Z' });
  await act(() => client.invalidateQueries({ queryKey: ['registry', 'active'] }));
  await waitFor(() => expect(screen.getByLabelText('Anzahl der Änderungen')).toHaveTextContent('…'));
  await waitFor(() => expect(respond).toBeDefined()); respond!(reply(200, emptyVerdict()));
  await waitFor(() => expect(screen.getAllByLabelText('Anzahl der Änderungen').map(node => node.textContent)).toEqual(['Keine Änderungen', 'Keine Änderungen']));
});

it('shows a dash and correction hint after invalid validation, then a fresh count after repair', async () => {
  const fake = installFakeApi(); const user = userEvent.setup();
  let valid = false;
  fake.routes.push({ match: /^registry\/validate$/, respond: () => valid ? reply(200, emptyVerdict()) : reply(422, { error: 'invalid_registry', issues: [{ code: 'missing_source', path: '/contracts/0/inputs/0', message: 'missing' }] }) });
  editor(); await user.click(screen.getByText('Open'));
  await waitFor(() => expect(screen.getByLabelText('Anzahl der Änderungen')).toHaveTextContent('–erst nach Fehlerkorrektur'));
  expect(screen.getByLabelText('Anzahl der Änderungen')).not.toHaveTextContent('…');
  valid = true;
  await user.click(screen.getByRole('button', { name: 'Prüfen' }));
  await waitFor(() => expect(screen.getAllByLabelText('Anzahl der Änderungen').map(node => node.textContent)).toEqual(['Keine Änderungen', 'Keine Änderungen']));
});
