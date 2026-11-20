import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';
import example from '../../../test/fixtures/example-registry.json';
import { useWorkingDocument, WorkingDocumentProvider } from '@/features/authoring/WorkingDocument';
import { clone, type JsonObject } from '@/lib/json';
import type { Draft } from '@/lib/types';
import { installFakeApi, makeDraft } from '../../../test/fakeApi';
import DefinitionDetailPage from './DefinitionDetailPage';

const user = userEvent.setup();
const registry = () => clone(example) as unknown as JsonObject;

function Harness({ draft, path }: { draft: Draft; path: string }) {
  function Open() {
    const doc = useWorkingDocument();
    const location = useLocation();
    return (
      <>
        <button onClick={() => doc.open(draft)}>open-draft</button>
        <output data-testid="loc">{location.pathname}</output>
        <output data-testid="cfg">{JSON.stringify(doc.config)}</output>
      </>
    );
  }
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <WorkingDocumentProvider validateDebounceMs={3_600_000}>
          <Open />
          <Routes>
          <Route path="/templates/details/:typeId/:version" element={<DefinitionDetailPage />} />
          <Route path="/templates/own" element={<p>Liste</p>} />
          </Routes>
        </WorkingDocumentProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}
const config = () => JSON.parse(screen.getByTestId('cfg').textContent!) as { definitions: JsonObject[]; contracts: JsonObject[] };

describe('definition versions are immutable once activated', () => {
  it('locks an activated version, keeps editorial fields editable and creates a new version with instance migration', async () => {
    const api = installFakeApi({ revisions: [{ revision: 3, config: registry(), checksum: 'c'.repeat(64), created_at: 'x' }], drafts: [makeDraft('d1', registry(), { base_revision: 3 })] });
    render(<Harness draft={api.drafts.get('d1') as unknown as Draft} path="/templates/details/test.truth/1" />);
    await user.click(screen.getByText('open-draft'));
    await screen.findByText(/Übernommene Vorlagenversion/);

    // Logic is locked ...
    await user.click(screen.getByRole('tab', { name: /Ausgaben/ }));
    const operator = (await screen.findAllByRole('combobox', { name: /^Rechenschritt/ }))[0]!;
    expect(operator).toBeDisabled();
    // ... editorial text is not.
    await user.click(screen.getByRole('button', { name: 'Bearbeiten' }));
    const edit = await screen.findByRole('dialog', { name: 'Vorlage bearbeiten' });
    await user.click(within(edit).getByRole('tab', { name: 'Allgemein' }));
    const name = within(edit).getByLabelText(/Anzeigename/);
    expect(name).toBeEnabled();
    await user.type(name, 'Wahrheit');
    expect(config().definitions.find(d => d.type_id === 'test.truth')).not.toHaveProperty('display_name', 'Wahrheit');
    await user.click(within(edit).getByRole('button', { name: 'In Entwurf speichern' }));
    await waitFor(() => expect(config().definitions.find(d => d.type_id === 'test.truth')).toMatchObject({ display_name: 'Wahrheit', type_version: 1 }));

    // New version: copy with version 2, selected instances migrate, others stay on v1.
    await user.click(screen.getByRole('button', { name: /Neue Version/ }));
    const dialog = await screen.findByRole('dialog', { name: /Neue Version/ });
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'In Entwurf speichern' }));
    const migration = await screen.findByRole('dialog', { name: 'Verträge auf Version 2 umstellen' });
    expect(config().contracts.filter(c => c.type_id === 'test.truth').map(c => c.type_version)).toEqual([1, 1]);
    expect(within(migration).getAllByRole('checkbox').every(el => !(el as HTMLInputElement).checked)).toBe(true);
    expect(within(migration).getByText(/laufende Timer/)).toBeVisible();
    await user.click(within(migration).getByRole('checkbox', { name: 'fixture.left' }));
    await user.click(within(migration).getByRole('button', { name: 'In Entwurf speichern' }));
    await waitFor(() => expect(screen.getByTestId('loc').textContent).toBe('/templates/details/test.truth/2'));
    const next = config();
    const versions = next.definitions.filter((d) => d.type_id === 'test.truth').map((d) => d.type_version);
    expect(versions).toEqual([1, 2]);
    expect(next.contracts.find((c) => c.contract_id === 'fixture.left')!.type_version).toBe(2);
    expect(next.contracts.find((c) => c.contract_id === 'fixture.right')!.type_version).toBe(1);
    // The old version's semantics are untouched.
    const v1 = next.definitions.find((d) => d.type_id === 'test.truth' && d.type_version === 1)!;
    expect(v1.outputs).toEqual((registry().definitions as JsonObject[]).find((d) => d.type_id === 'test.truth')!.outputs);
  });

  it('a new, never activated version is editable', async () => {
    const api = installFakeApi({ revisions: [{ revision: 1, config: registry(), checksum: 'c'.repeat(64), created_at: 'x' }] });
    const cfg = registry();
    const defs = cfg.definitions as JsonObject[];
    defs.push({ ...clone(defs[0]!), type_version: 2 });
    api.drafts.set('d2', makeDraft('d2', cfg, { base_revision: 1 }));
    render(<Harness draft={api.drafts.get('d2') as unknown as Draft} path="/templates/details/test.truth/2" />);
    await user.click(screen.getByText('open-draft'));
    await user.click(await screen.findByRole('button', { name: 'Bearbeiten' }));
    const editor = await screen.findByRole('dialog', { name: 'Vorlage bearbeiten' });
    await user.click(within(editor).getByRole('tab', { name: /Ausgaben/ }));
    expect(within(editor).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]).toBeEnabled();
    expect(screen.queryByText(/Aktivierte Version/)).toBeNull();
  });
});
