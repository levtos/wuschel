import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import type { Envelope, FieldValue } from '@/lib/types';
import type { JsonObject } from '@/lib/json';
import type { FieldSchema } from '@/features/authoring/model';
import { installFakeApi, reply } from '../../../test/fakeApi';
import { MiniHistory } from './MiniHistory';
import { ContractHistory } from './ContractHistory';
import { historyIntervals, historyPoints, mergePublications, nearestPoint, publication, windowPoints } from './historyModel';
import { fieldsForContract } from './presentation';

const base = Date.parse('2032-01-05T10:00:00Z');
const at = (minutes: number) => new Date(base + minutes * 60_000).toISOString();
const field = (value: FieldValue['value'], status: FieldValue['status'] = 'valid'): FieldValue & JsonObject => ({ value, status, quality: 'healthy', reasons: [], evidence: [] });
const bool: FieldSchema = { type: 'bool', required: true, physical_state: false };
beforeEach(() => { localStorage.clear(); vi.stubGlobal('PointerEvent', MouseEvent); });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
function plot() {
  const svg = screen.getByRole('img');
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 1000, bottom: 215, width: 1000, height: 215, toJSON: () => ({}) });
  return svg;
}

it('selects the nearest publication between points immediately and exposes the full state interval', () => {
  render(<MiniHistory name="value" schema={bool} since={base} until={base + 20 * 60_000} points={[{ at: at(0), field: field(false) }, { at: at(10), field: field(true) }]} />);
  const svg = plot();
  fireEvent.pointerMove(svg, { clientX: 632, clientY: 80 }); // minute 12, nearest publication is minute 10
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent('11:10:00');
  expect(tooltip).toHaveTextContent('Ja');
  expect(tooltip).toHaveTextContent('seit 11:10:00 bis 11:20:00');
  expect(svg.querySelector('[data-cursor]')?.getAttribute('d')).toContain('M545 ');
  expect(svg.querySelector('title')).toBeNull();
  expect(svg.querySelectorAll('[data-state-band]')).toHaveLength(2);
  fireEvent.keyDown(svg, { key: 'ArrowLeft' });
  expect(screen.getByRole('tooltip')).toHaveTextContent('Nein');
  fireEvent.keyDown(svg, { key: 'End' });
  expect(screen.getByRole('tooltip')).toHaveTextContent('Ja');
  fireEvent.pointerDown(svg, { clientX: 120, clientY: 80, pointerType: 'touch' });
  expect(screen.getByRole('tooltip')).toHaveTextContent('Nein');
  expect(nearestPoint([{ at: at(0), field: field(false) }, { at: at(10), field: field(true) }], base + 5 * 60_000)).toBe(0);
  expect(nearestPoint([{ at: at(0), field: field(false) }, { at: at(0), field: field(true) }], base)).toBe(1);
});

it.each(['process_restart', 'ha_disconnect', 'db_outage', 'mqtt_disconnect'])('shows the recorded %s gap extent and its clear reason without joining the value line', reason => {
  const points = historyPoints([
    { id: 'one', contract_id: 'synthetic', published_at: at(0), fields: { value: field(1) } },
    { id: 'two', contract_id: 'synthetic', published_at: at(15), fields: { value: field(2) } },
  ], 'synthetic', 'value', undefined, [{ id: 'gap', reason, at: at(5) }]);
  render(<MiniHistory name="value" since={base} until={base + 20 * 60_000} points={points} />);
  const svg = plot();
  expect(within(svg).getByText('unbekannt / Lücke')).toBeVisible();
  const band = svg.querySelector('[data-gap]')!;
  expect(Number(band.getAttribute('width'))).toBe(435); // ten of the twenty minutes, no invented fixed width
  expect(band.getAttribute('fill')).toMatch(/^url/);
  expect(svg.querySelectorAll('polyline')).toHaveLength(2);
  fireEvent.pointerMove(svg, { clientX: 545, clientY: 166 });
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent('seit 11:05:00 bis 11:15:00');
  expect(tooltip).toHaveTextContent('10 min');
  expect(tooltip).toHaveTextContent({ process_restart: 'Wuschel-Neustart', ha_disconnect: 'Verbindung zu Home Assistant getrennt', db_outage: 'Datenbank nicht erreichbar', mqtt_disconnect: 'MQTT-Verbindung getrennt' }[reason]!);
});

it('retains an unknown field reason after a gap marker and keeps an unrecorded prefix empty', () => {
  const unknown = field(null, 'unknown');
  unknown.reasons = [{ code: 'input_stale', input: 'synthetic', since: at(5) }];
  const points = historyPoints([{ id: 'one', contract_id: 'synthetic', published_at: at(5), fields: { value: unknown } }, { id: 'two', contract_id: 'synthetic', published_at: at(15), fields: { value: field(true) } }], 'synthetic', 'value', undefined, [{ id: 'gap', at: at(5), reason: 'process_restart' }]);
  render(<MiniHistory name="value" schema={bool} since={base} until={base + 20 * 60_000} points={points} />);
  const svg = plot();
  expect(svg.querySelectorAll('[data-gap]').length).toBeGreaterThanOrEqual(2);
  fireEvent.pointerMove(svg, { clientX: 545, clientY: 166 });
  expect(screen.getByRole('tooltip')).toHaveTextContent('Wuschel-Neustart');
  expect(screen.getByRole('tooltip').textContent).toMatch(/alt|aktuell|veraltet/i);
  const clipped = windowPoints(points, base, base + 20 * 60_000);
  expect(clipped[0]).toMatchObject({ at: at(0), field: null, reason: 'no_recording' });
  expect(historyIntervals(clipped, base + 20 * 60_000)[0]).toMatchObject({ start: base, end: base + 5 * 60_000 });
});

it('labels unseen enum values and fits long labels while the tooltip keeps the complete name and week date', () => {
  const schema: FieldSchema = { type: 'enum', required: true, physical_state: false, value_catalog: ['normal', 'headset'], legend: { normal: { display_name: 'Synthetischer besonders langer Anzeigename' }, headset: { display_name: 'Headset' } } };
  render(<MiniHistory name="value" schema={schema} since={base} until={base + 7 * 86400000} points={[{ at: at(0), field: field('normal') }]} />);
  const svg = plot();
  expect(within(svg).getByText('Headset')).toBeVisible();
  const long = svg.querySelector('[data-axis-label="Synthetischer besonders langer Anzeigename"]')!;
  expect(Number(long.getAttribute('textLength'))).toBeLessThanOrEqual(94);
  fireEvent.pointerMove(long, { clientX: 70, clientY: 36 });
  expect(screen.getByRole('tooltip')).toHaveTextContent('Synthetischer besonders langer Anzeigename');
  fireEvent.keyDown(svg, { key: 'Home' });
  expect(screen.getByRole('tooltip').textContent).toMatch(/05\.01\.2032/);
});

it('renders timestamp values as an ordered change list instead of a clipped value axis', () => {
  render(<MiniHistory name="stamp" schema={{ type: 'timestamp', required: true, physical_state: false }} since={base} until={base + 20 * 60_000} points={[{ at: at(0), field: field(at(5)) }, { at: at(10), field: field(at(15)) }]} />);
  expect(screen.getByRole('list', { name: /Änderungen/ })).toBeVisible();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
  expect(screen.getByRole('list').textContent).not.toContain(at(5));
});

it('gets enum display names from the catalog actually referenced by that output and leaves configuration untouched', () => {
  const schema: FieldSchema = { type: 'enum', required: true, physical_state: false, value_catalog: ['normal', 'headset'] };
  const config: JsonObject = { definitions: [{ type_id: 'synthetic.class', type_version: 1, outputs: { value: { op: 'managed_catalog_lookup', catalog_id: 'synthetic.classes', definition_version: 2 } } }], catalogs: [
    { catalog_id: 'synthetic.classes', version: 1, classification_schema: { legend: { normal: { display_name: 'Synthetische alte Legende' } } } },
    { catalog_id: 'synthetic.classes', version: 2, classification_schema: { legend: { normal: { display_name: 'Normal' }, headset: { display_name: 'Headset' } } } },
  ] };
  const before = structuredClone(config);
  const fields = fieldsForContract({ type_id: 'synthetic.class', type_version: 1 }, config, [{ key: 'synthetic.class@1', type_id: 'synthetic.class', type_version: 1, origin: 'active-definition', fields: { value: schema }, inputs: [], declarative: true, machine: false }]);
  expect(fields.value?.legend).toEqual({ normal: { display_name: 'Normal' }, headset: { display_name: 'Headset' } });
  expect(fields.value?.value_catalog).toEqual(['normal', 'headset']);
  expect(schema.legend).toBeUndefined();
  expect(config).toEqual(before);
});

it('keeps every enum tick inside the plot even when no known state has been observed', () => {
  render(<MiniHistory name="value" schema={{ type: 'enum', required: true, physical_state: false, value_catalog: ['open', 'tilted', 'closed'] }} since={base} until={base + 20 * 60_000} points={[]} />);
  const svg = screen.getByRole('img');
  for (const label of ['offen', 'gekippt', 'zu']) {
    const text = within(svg).getByText(label);
    expect(Number(text.getAttribute('y'))).toBeLessThanOrEqual(136);
    expect(Number(text.getAttribute('y'))).toBeGreaterThanOrEqual(32);
  }
  expect(svg.querySelector('polyline')).toBeNull();
  expect(svg.querySelector('[data-gap]')).not.toBeNull();
});

it('deduplicates a stored and live publication by epoch and sequence without confusing equal timestamps', () => {
  const first = { id: 'stored', epoch_id: 'synthetic', publication_seq: 1, published_at: at(0), fields: { value: field(true) } };
  const second = { ...first, id: 'other', publication_seq: 2, fields: { value: field(false) } };
  expect(mergePublications([first], [{ ...first, id: 'live' }, second])).toHaveLength(2);
  expect(mergePublications([first], [second]).map(item => item.publication_seq)).toEqual([1, 2]);
  expect(mergePublications([{ ...first, contract_id: 'synthetic.one' }], [{ ...first, contract_id: 'synthetic.other' }])).toHaveLength(2);
});

it('does not paint a known state tail after the last confirmed publication when the connection is stale', () => {
  render(<MiniHistory name="value" schema={bool} stale since={base} until={base + 20 * 60_000} points={[{ at: at(0), field: field(true) }, { at: at(5), field: field(true) }]} />);
  const svg = plot();
  const bands = svg.querySelectorAll('[data-state-band]');
  expect(bands).toHaveLength(1);
  expect(Number(bands[0]!.getAttribute('width'))).toBe(217.5);
  fireEvent.pointerMove(svg, { clientX: 545, clientY: 166 });
  expect(screen.getByRole('tooltip')).toHaveTextContent('Verbindung nicht bestätigt');
});

function fixture() {
  const time = new Date(Date.now() - 60_000).toISOString();
  const envelope: Envelope = { contract_id: 'synthetic.history', type: { id: 'test.echo', version: 1 }, enabled: true, fields: { value: field(1) }, available: true, contract_health: 'healthy', computed_at: time, published_at: time, epoch_id: 'synthetic', publication_seq: 1, registry_revision: 1 };
  const fake = installFakeApi();
  fake.routes.push({ match: /^history\/query\/contracts/, respond: path => reply(200, { items: new URLSearchParams(path.split('?')[1]).has('since') ? [publication(envelope)] : [], next_cursor: null }) });
  fake.routes.push({ match: /^history\/query\/gaps/, respond: () => reply(200, { items: [], next_cursor: null }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (next = envelope) => <QueryClientProvider client={client}><ContractHistory envelope={next} stale={false} fields={{ value: { type: 'number', required: true, physical_state: false } }} /></QueryClientProvider>;
  const view = render(ui());
  const requests = () => fake.calls.filter(call => call.path.startsWith('history/query/')).length;
  return { envelope, fake, ui, view, requests };
}

it('does not load a collapsed field, remembers expansion and appends exactly one live point without HTTP reload', async () => {
  const { envelope, ui, view, requests } = fixture();
  expect(requests()).toBe(0);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Verlauf: Wert' }));
  const svg = await screen.findByRole('img');
  await waitFor(() => expect(svg.querySelectorAll('circle')).toHaveLength(1));
  const loaded = requests();
  const next = { ...envelope, publication_seq: 2, published_at: new Date(Date.parse(envelope.published_at) + 30_000).toISOString(), fields: { value: field(2) } };
  view.rerender(ui(next));
  await waitFor(() => expect(svg.querySelectorAll('circle')).toHaveLength(2));
  view.rerender(ui({ ...next }));
  expect(svg.querySelectorAll('circle')).toHaveLength(2);
  expect(requests()).toBe(loaded);
  vi.useFakeTimers();
  await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
  expect(requests()).toBe(loaded);
  vi.useRealTimers();
  view.unmount();
  const restored = render(ui(next));
  expect(screen.getByRole('button', { name: 'Verlauf: Wert' })).toHaveAttribute('aria-expanded', 'true');
  restored.unmount();
});

it('reloads only on explicit request and does not share expansion between different contracts', async () => {
  const { envelope, ui, view, requests } = fixture();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Verlauf: Wert' }));
  await screen.findByRole('img');
  const loaded = requests();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Neu laden' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: 'Neu laden' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Neu laden' })).toBeEnabled());
  expect(requests()).toBe(loaded + 4); // one page plus one boundary for contracts and gaps
  view.unmount();
  render(ui({ ...envelope, contract_id: 'synthetic.other' }));
  expect(screen.getByRole('button', { name: 'Verlauf: Wert' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
