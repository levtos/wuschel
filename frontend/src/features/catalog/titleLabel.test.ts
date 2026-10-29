import { expect, it } from 'vitest';
import { titleKeyParts, titleLabel } from './titleLabel';

it.each([
  [['Synthetic Ensemble', 'Synthetic Beacon'], 'Synthetic Ensemble – Synthetic Beacon'],
  [['Synthetic Beacon'], 'Synthetic Beacon'],
  [['Synthetic - Ensemble', 'Synthetic "Beacon" – Ä'], 'Synthetic - Ensemble – Synthetic "Beacon" – Ä'],
  [['Synthetic - Beacon'], 'Synthetic - Beacon'],
])('formats compound titles without JSON syntax: %j', (parts, expected) => {
  const key = JSON.stringify(parts);
  expect(titleLabel({ key, display_name: '' })).toBe(expected);
  expect(titleKeyParts(key)).toEqual({ artist: parts.length === 2 ? parts[0] : '', title: parts.at(-1) });
});

it.each(['Synthetic Plain', '[broken', '[]', '[1,"Synthetic"]', '["a","b","c"]'])('retains unsupported legacy text safely: %s', key => {
  expect(titleLabel({ key })).toBe(key);
  expect(titleKeyParts(key)).toBeNull();
});

it('preserves an explicitly supplied display name and never changes its key', () => {
  const row = { key: '["Synthetic Ensemble","Synthetic Beacon"]', display_name: 'Synthetic editorial label' };
  const before = structuredClone(row);
  expect(titleLabel(row)).toBe(row.display_name);
  expect(row).toEqual(before);
});
