import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { validationLabel } from '@/i18n/codes';
import { t } from '@/i18n';
import { DiffView } from '@/features/registry/DiffView';
import type { Diff } from '@/lib/types';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';
import { idleLive, wrap } from '../../test/harness';
import verdict from '../../test/fixtures/validate-ok.json';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it('reserves unknown guidance for unknown codes', () => {
  for (const code of ['contract_inputs', 'disabled_dependency', 'input_reference', 'source_unbound', 'binding_source', 'field_reference', 'type_reference', 'refresh_binding', 'catalog_reference', 'contract_graph_depth', 'duplicate_adapter', 'ambiguous_identity', 'dependency_cycle', 'opening_member', 'opening_members', 'schema.int_type', 'parameter.greater_than', 'definition.rule_type_mismatch']) {
    const message = validationLabel({ code, path: '/', message: 'technical' });
    expect(message).not.toBe(t('validation.unknown'));
    expect(message).not.toContain('technical');
  }
  expect(validationLabel({ code: 'synthetic_unknown', path: '/', message: 'technical' })).toBe(t('validation.unknown'));
});

it('names contracts and sources before their identifiers and translates changed field names', () => {
  installFakeApi();
  const config = sampleConfig({ contracts: [{ contract_id: 'synthetic.contract', display_name: 'Heizung' }], sources: [{ source_id: 'synthetic.source', display_name: 'Temperatur' }] });
  const diff = structuredClone(verdict.diff) as Diff;
  diff.added = []; diff.changed = ['synthetic.contract']; diff.removed = [];
  diff.objects = {
    contracts: { added: [], removed: [], changed: ['synthetic.contract'], fields: { 'synthetic.contract': ['display_name'] }, field_paths: { 'synthetic.contract': ['/display_name'] }, field_paths_truncated: [] },
    sources: { added: ['synthetic.source'], removed: [], changed: [], fields: {}, field_paths: {}, field_paths_truncated: [] },
  };
  wrap(<DiffView diff={diff} config={config} />);
  expect(screen.getAllByText('Heizung (synthetic.contract)').length).toBeGreaterThan(0);
  expect(screen.getByText('Temperatur (synthetic.source)')).toBeVisible();
  expect(screen.getByText(/Felder:.*Anzeigename/)).toBeVisible();
  expect(screen.queryByText('display_name')).toBeNull();
});

it('jumps from the change review into the contract editor and focuses its invalid input', async () => {
  const config = sampleConfig({ contracts: [{ contract_id: 'synthetic.contract', display_name: 'Heizung', type_id: 'synthetic.echo', type_version: 1, enabled: true, inputs: [], parameters: {} }], definitions: [{ type_id: 'synthetic.echo', type_version: 1, inputs: [{ name: 'a', schema: { type: 'bool', required: true } }], schema: { fields: { value: { type: 'bool', required: true } } } }] });
  const fake = installFakeApi({ drafts: [makeDraft('d', config)] });
  fake.issuesFor = () => [{ code: 'contract_inputs', path: '/contracts/0/inputs', object_id: 'synthetic.contract', message: 'technical' }];
  sessionStorage.setItem('terra.workingDraft', 'd');
  window.location.hash = '#/changes/drafts/d';
  const scroll = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  await userEvent.click(await screen.findByRole('button', { name: 'Änderungen prüfen' }));
  await userEvent.click(await screen.findByRole('link', { name: 'Vertrag Heizung →' }));
  const field = await screen.findByRole('combobox', { name: 'Verknüpfter Wert' });
  await waitFor(() => expect(field).toBeEnabled());
  await waitFor(() => expect(field).toHaveFocus());
  expect(scroll).toHaveBeenCalled();
  scroll.mockRestore();
});
