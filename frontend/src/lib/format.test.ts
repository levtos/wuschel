import { expect, it } from 'vitest';
import { formatDuration, formatNumber, formatTime } from './format';
import { displayValue } from './status';
import { validIdentifier } from './identifiers';

it('formats numbers, units, booleans and durations in German', () => {
  expect(formatNumber(0.6000000000000014)).toBe('0,6');
  expect(formatDuration(90)).toBe('1,5 min');
  expect(displayValue({ status: 'valid', value: 20.6, quality: 'healthy', evidence: [], reasons: [] }, '°C')).toBe('20,6 °C');
  expect(displayValue({ status: 'valid', value: true, quality: 'healthy', evidence: [], reasons: [] })).toBe('Ja');
});
it('shows timestamp values and nested timestamps in German while preserving the instant', () => {
  const at = '2026-01-01T12:00:00Z';
  expect(formatTime(at)).toBe('01.01.2026, 13:00');
  expect(formatTime('2026-07-01T12:00:00+00:00')).toBe('01.07.2026, 14:00');
  const field = { status: 'valid' as const, value: at, quality: 'healthy' as const, evidence: [], reasons: [] };
  expect(displayValue(field)).toBe('01.01.2026, 13:00');
  expect(displayValue({ ...field, value: { stamp: at } })).not.toContain(at);
  expect(field.value).toBe(at);
});
it('accepts only bounded canonical IDs', () => {
  expect(validIdentifier('wohnzimmer.temperatur')).toBe(true);
  expect(validIdentifier('a'.repeat(128))).toBe(true);
  for (const id of ['a'.repeat(129), 'ende.', 'küche.wärme', 'a..b']) expect(validIdentifier(id)).toBe(false);
});
