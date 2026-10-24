import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { historyPoints, MiniHistory } from '@/features/contracts/ContractSummary';
import { useHistoryWindow, windowHistory } from '@/features/contracts/historyWindow';
import { api } from '@/lib/api';
import type { FieldValue } from '@/lib/types';

vi.mock('@/lib/api', () => ({ api: vi.fn() }));
const field = { status: 'valid', value: 'tilted', quality: 'healthy', reasons: [], evidence: [] } satisfies FieldValue;
const schema = { type: 'enum' as const, required: true, physical_state: true, value_catalog: ['open', 'tilted', 'closed'] };

it('shares the 24 hour or seven day selection between chart instances', () => {
  function Control({ name }: { name: string }) {
    const window = useHistoryWindow();
    return <button aria-label={name} onClick={() => window.select(window.hours === 24 ? 168 : 24)}>{window.until - window.since}</button>;
  }
  render(<><Control name="first" /><Control name="second" /></>);
  expect(screen.getByRole('button', { name: 'first' })).toHaveTextContent('86400000');
  fireEvent.click(screen.getByRole('button', { name: 'first' }));
  expect(screen.getByRole('button', { name: 'first' })).toHaveTextContent('604800000');
  expect(screen.getByRole('button', { name: 'second' })).toHaveTextContent('604800000');
  fireEvent.click(screen.getByRole('button', { name: 'second' }));
  expect(screen.getByRole('button', { name: 'first' })).toHaveTextContent('86400000');
});

it('uses schema order including unseen categories and point titles without fractional enum ticks', () => {
  render(<MiniHistory name="value" schema={schema} points={[{ at: '2026-10-24T00:00:00Z', field }]} />);
  const labels = [...screen.getByRole('img').querySelectorAll('text')].map(text => text.textContent);
  expect(labels).toEqual(['offen', 'gekippt', 'zu']);
  expect(screen.getByRole('img').querySelector('circle title')?.textContent).toMatch(/gekippt.*gültig/);
  expect(screen.queryByText('Unbekannter Zustand')).not.toBeInTheDocument();
});

it('draws enum steps, unknown gaps and fixed 24 hour bounds', () => {
  const since = Date.parse('2026-10-24T00:00:00Z');
  render(<MiniHistory name="value" schema={schema} since={since} until={since + 86400000} points={[
    { at: new Date(since + 3600000).toISOString(), field },
    { at: new Date(since + 7200000).toISOString(), field: { ...field, status: 'unknown', value: null } },
    { at: new Date(since + 10800000).toISOString(), field: { ...field, value: 'closed' } },
  ]} />);
  expect(screen.getByRole('img').querySelectorAll('polyline')).toHaveLength(2);
  expect(screen.getByRole('img').querySelector('rect title')?.textContent).toMatch(/unbekannt/);
  expect(screen.getByRole('img').querySelector('circle')?.getAttribute('cx')).toBe('146.25');
});

it('retains more than sixty samples and retrieves every cursor page and the left boundary', async () => {
  const at = '2026-10-24T00:00:00Z';
  const rows = Array.from({ length: 65 }, (_, index) => ({ id: String(index), contract_id: 'fixture', published_at: at, fields: { value: field } }));
  expect(historyPoints(rows, 'fixture', 'value')).toHaveLength(65);
  vi.mocked(api).mockResolvedValueOnce({ items: rows.slice(0, 60), next_cursor: 'next' }).mockResolvedValueOnce({ items: rows.slice(60), next_cursor: null }).mockResolvedValueOnce({ items: [{ ...rows[0], id: 'boundary' }], next_cursor: null });
  expect(await windowHistory('contracts', 'fixture', Date.parse(at), Date.parse(at) + 86400000)).toHaveLength(66);
  expect(vi.mocked(api).mock.calls[1]?.[0]).toContain('cursor=next');
  expect(vi.mocked(api).mock.calls[2]?.[0]).toContain('limit=1');
});
