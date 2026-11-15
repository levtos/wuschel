import { useState } from 'react';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { ObjectActions } from '@/features/authoring/ObjectActions';
import { useWorkingDocument, WorkingDocumentProvider } from '@/features/authoring/WorkingDocument';
import { asArray } from '@/features/authoring/model';
import { isObject } from '@/lib/json';
import type { Draft } from '@/lib/types';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';
import { wrap } from '../../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
function mount() {
  const value = { contract_id: 'root', display_name: 'Wahrheit', enabled: true };
  const config = sampleConfig({ contracts: [value, { contract_id: 'child', type_id: 'synthetic.required', type_version: 1, display_name: 'Folgevertrag', enabled: true, inputs: [{ name: 'a', ref: 'root.value' }] }], definitions: [{ type_id: 'synthetic.required', type_version: 1, inputs: [{ name: 'a', schema: { type: 'bool', required: true } }], schema: { fields: {} } }] });
  const api = installFakeApi({ drafts: [makeDraft('d', config)] });
  function Surface() {
    const doc = useWorkingDocument(); const [editing, setEditing] = useState(false);
    const current = asArray(doc.config?.contracts).filter(isObject).find(row => row.contract_id === 'root') ?? value;
    return <ObjectActions collection="contracts" idKey="contract_id" value={current} toggle editing={editing} onEdit={() => { doc.open(api.drafts.get('d') as unknown as Draft); setEditing(true); }} />;
  }
  wrap(<WorkingDocumentProvider><Surface /></WorkingDocumentProvider>);
  return api;
}
it('shows only one icon action and exposes its tooltip to keyboard users', async () => {
  const user = userEvent.setup(); mount();
  expect(screen.getAllByRole('button')).toHaveLength(1);
  const edit = screen.getByRole('button', { name: 'Bearbeiten' });
  expect(edit.textContent).toBe(''); expect(edit.querySelector('svg')).not.toBeNull();
  await user.tab(); expect(edit).toHaveFocus();
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Bearbeiten');
  await user.keyboard('{Enter}');
  expect(await screen.findByRole('button', { name: 'Umbenennen' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Deaktivieren' })).toBeVisible();
});
it('names the rename consequence and saves the display name only after confirmation', async () => {
  const user = userEvent.setup(); const api = mount();
  await user.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  await user.click(screen.getByRole('button', { name: 'Umbenennen' }));
  const dialog = screen.getByRole('dialog', { name: 'Umbenennen' });
  const input = within(dialog).getByRole('textbox'); await user.clear(input); await user.type(input, 'Neuer Name');
  await user.click(within(dialog).getByRole('button', { name: 'Im Entwurf speichern' }));
  const confirmation = screen.getByRole('alertdialog', { name: 'Umbenennen' });
  expect(confirmation).toHaveTextContent('„Wahrheit“ wird zu „Neuer Name“');
  expect(confirmation).toHaveTextContent('Nur der Anzeigename');
  expect(api.calls.some(call => call.method === 'PUT')).toBe(false);
  await user.click(within(confirmation).getByRole('button', { name: 'Im Entwurf speichern' }));
  await waitFor(() => expect(api.drafts.get('d')?.config.contracts).toMatchObject([{ contract_id: 'root', display_name: 'Neuer Name' }, {}]));
});
it('confirms disabling, names affected contracts and leaves cancellation unchanged', async () => {
  const user = userEvent.setup(); const api = mount();
  await user.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  await user.click(screen.getByRole('button', { name: 'Deaktivieren' }));
  let dialog = screen.getByRole('alertdialog', { name: 'Deaktivieren' });
  expect(dialog).toHaveTextContent('Wahrheit'); expect(dialog).toHaveTextContent('Folgevertrag');
  expect(dialog).toHaveTextContent('Folgevertrag (child)');
  expect(dialog).toHaveTextContent('Solange sie eingeschaltet sind, sperrt die Prüfung das Übernehmen.');
  await user.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
  expect(api.calls.some(call => call.method === 'PUT')).toBe(false);
  await user.click(screen.getByRole('button', { name: 'Deaktivieren' }));
  dialog = screen.getByRole('alertdialog', { name: 'Deaktivieren' });
  await user.click(within(dialog).getByRole('button', { name: 'Im Entwurf speichern' }));
  await waitFor(() => expect(api.drafts.get('d')?.config.contracts).toMatchObject([{ enabled: false }, {}]));
});
