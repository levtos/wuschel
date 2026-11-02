import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode, useEffect, useState } from 'react';
import { beforeEach, expect, it } from 'vitest';
import { LayoutPanel, LayoutPreferencesProvider, layoutKey, PanelLayout } from './PanelLayout';
import { Shell } from './Shell';
import { installFakeApi, makeDraft, sampleConfig } from '../../test/fakeApi';
import { idleLive, wrap } from '../../test/harness';
import { WorkingDocumentProvider, useWorkingDocument } from '@/features/authoring/WorkingDocument';
import SourcesPage from '@/features/sources/SourcesPage';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LiveProvider } from '@/lib/live-context';

const user = userEvent.setup();
const adjust = async () => {
  await user.click(screen.getByLabelText('Weitere Seitenaktionen'));
  await user.click(screen.getByRole('button', { name: 'Anordnung anpassen' }));
};
const order = () => [...document.querySelectorAll('[data-layout-panel]')].map((n) => n.getAttribute('data-layout-panel'));
beforeEach(() => localStorage.clear());

let mounted = 0;
function LocalEditor() {
  const [value, set] = useState('');
  useEffect(() => { mounted += 1; }, []);
  return <input aria-label="Unsaved editor" value={value} onChange={(e) => set(e.target.value)} />;
}
function Layout({ installation = 'one', view = 'test' }: { installation?: string | null; view?: string }) {
  return <LayoutPreferencesProvider value={installation}><PanelLayout view={view}>
    <LayoutPanel id="editor" title="Editor" span={2}><LocalEditor /></LayoutPanel>
    <LayoutPanel id="validation" title="Validierung" span={1}><p>Validation</p></LayoutPanel>
    <LayoutPanel id="diff" title="Diff"><p>Diff</p></LayoutPanel>
  </PanelLayout></LayoutPreferencesProvider>;
}

it('moves with keyboard, resizes and resets without remounting a dirty editor or persisting its contents', async () => {
  render(<StrictMode><Layout /></StrictMode>);
  const input = screen.getByLabelText('Unsaved editor');
  await user.type(input, 'private registry text');
  const initialMounts = mounted;
  await adjust();
  const handle = screen.getByRole('button', { name: "Bereich Editor verschieben" });
  handle.focus();
  await user.keyboard('{ArrowDown}');
  expect(order()).toEqual(['validation', 'editor', 'diff']);
  expect(handle).toHaveFocus();
  expect(screen.getByLabelText('Unsaved editor')).toBe(input);
  await user.selectOptions(screen.getByLabelText("Editor: Breite des Bereichs"), '3');
  expect(document.querySelector('[data-layout-panel="editor"]')).toHaveAttribute('data-span', '3');
  await user.click(screen.getByRole('button', { name: "Anordnung zurücksetzen" }));
  expect(order()).toEqual(['editor', 'validation', 'diff']);
  expect(input).toHaveValue('private registry text');
  expect(mounted).toBe(initialMounts);
  const saved = localStorage.getItem(layoutKey('one', 'test')!)!;
  expect(JSON.parse(saved)).toEqual({ order: ['editor', 'validation', 'diff'], spans: { editor: 2, validation: 1, diff: 3 } });
  expect(saved).not.toContain('private registry text');
});

it('persists order and widths across reload, separated by installation and view', async () => {
  const view = render(<Layout />);
  await adjust();
  await user.click(screen.getByRole('button', { name: 'Editor: nach hinten' }));
  await user.selectOptions(screen.getByLabelText("Editor: Breite des Bereichs"), '1');
  view.unmount();
  const reload = render(<Layout />);
  expect(order()).toEqual(['validation', 'editor', 'diff']);
  expect(document.querySelector('[data-layout-panel="editor"]')).toHaveAttribute('data-span', '1');
  reload.rerender(<Layout installation="two" />);
  expect(order()).toEqual(['editor', 'validation', 'diff']);
  expect(document.querySelector('[data-layout-panel="editor"]')).toHaveAttribute('data-span', '2');
  reload.rerender(<Layout view="other" />);
  expect(order()).toEqual(['editor', 'validation', 'diff']);
  expect(localStorage.getItem(layoutKey('two', 'test')!)).toBeNull();
});

it('ignores corrupt metadata and writes nothing before the installation identity is known', async () => {
  localStorage.setItem(layoutKey('one', 'test')!, '{broken');
  const view = render(<Layout />);
  expect(order()).toEqual(['editor', 'validation', 'diff']);
  view.rerender(<Layout installation={null} />);
  await adjust();
  await user.click(screen.getByRole('button', { name: 'Editor: nach hinten' }));
  expect(localStorage.length).toBe(1);
  expect(localStorage.getItem(layoutKey('one', 'test')!)).toBe('{broken');
});

it('moves through the real native drag adapter in StrictMode and keeps the editor node', async () => {
  render(<StrictMode><Layout /></StrictMode>);
  const input = screen.getByLabelText('Unsaved editor');
  await user.type(input, 'keep');
  await adjust();
  const data = new Map<string, string>();
  const transfer = { types: [] as string[], effectAllowed: 'all', dropEffect: 'move', setData(type: string, value: string) { data.set(type, value); this.types.push(type); }, getData: (type: string) => data.get(type) ?? '', setDragImage() {} };
  const handle = screen.getByRole('button', { name: "Bereich Editor verschieben" });
  const target = document.querySelector('[data-layout-panel="diff"]')!;
  const dragEvent = (element: Element, type: string, y: number) => {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: 10, clientY: y });
    Object.defineProperty(event, 'dataTransfer', { value: transfer });
    fireEvent(element, event);
  };
  act(() => {
    dragEvent(handle, 'dragstart', 10);
    dragEvent(target, 'dragenter', 100);
    dragEvent(target, 'dragover', 100);
    dragEvent(target, 'drop', 100);
    dragEvent(handle, 'dragend', 100);
  });
  await waitFor(() => expect(order()).toEqual(['validation', 'diff', 'editor']));
  expect(screen.getByLabelText('Unsaved editor')).toBe(input);
  expect(input).toHaveValue('keep');
});

it('keeps the actual source editor and dirty draft unchanged; layout actions send no writes', async () => {
  const source = { source_id: 'source.a', source_type: 'boolean', source_origin: 'local', dependency_id: 'dep.test', physical_source_key: 'physical.a', freshness: { mode: 'event_stateful', future_tolerance_s: 1 } };
  const api = installFakeApi({ drafts: [makeDraft('a', sampleConfig({ sources: [source] }))] });
  function Open() {
    const doc = useWorkingDocument();
    return <><button onClick={() => doc.open(api.drafts.get('a')!)}>Open</button><output data-testid="dirty">{String(doc.dirty)}</output><SourcesPage /></>;
  }
  wrap(<LayoutPreferencesProvider value="one"><WorkingDocumentProvider validateDebounceMs={3_600_000}><Open /></WorkingDocumentProvider></LayoutPreferencesProvider>);
  await user.click(screen.getByText('Open'));
  await user.click(await screen.findByRole('button', { name: /source.a/ }));
  await user.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  const input = screen.getByLabelText('Name');
  await user.type(input, 'unsaved');
  await adjust();
  await user.click(screen.getByRole('button', { name: 'Quelle bearbeiten: nach vorne' }));
  await user.selectOptions(screen.getByLabelText("Quelle bearbeiten: Breite des Bereichs"), '3');
  await user.click(screen.getByRole('button', { name: "Anordnung zurücksetzen" }));
  expect(input).toHaveValue('unsaved');
  expect(screen.getByLabelText('Name')).toBe(input);
  expect(screen.getByTestId('dirty')).toHaveTextContent('true');
  expect(api.calls.filter((c) => c.method !== 'GET')).toEqual([]);
  expect(JSON.stringify(api.drafts.get('a'))).not.toContain('unsaved');
});

it('keeps navigation accessible and marks definitions active while collapsed; sidebar survives reload', async () => {
  installFakeApi();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = () => <QueryClientProvider client={client}><MemoryRouter initialEntries={['/templates/own']}><LiveProvider store={idleLive()}><WorkingDocumentProvider><Routes><Route element={<Shell onSetup={() => {}} />}><Route path="/templates/own" element={<p>Vorlagenseite</p>} /></Route></Routes></WorkingDocumentProvider></LiveProvider></MemoryRouter></QueryClientProvider>;
  const view = render(ui());
  await screen.findByText("Vorlagenseite");
  await waitFor(() => expect(client.getQueryData(['info'])).toBeTruthy());
  await user.click(screen.getByRole('button', { name: 'Navigation einklappen' }));
  const nav = within(screen.getByRole('navigation', { name: 'Hauptnavigation' }));
  expect(nav.getByRole('button', { name: 'Verträge' })).toHaveAttribute('aria-current', 'true');
  expect(nav.getAllByRole('button')).toHaveLength(5);
  expect(nav.getAllByRole('link')).toHaveLength(1);
  view.unmount();
  render(ui());
  await screen.findByRole('button', { name: 'Navigation ausklappen' });
  expect(localStorage.getItem(layoutKey('inst-1', 'navigation')!)).toBe('true');
});
