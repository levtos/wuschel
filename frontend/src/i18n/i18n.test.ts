import { describe, expect, it } from 'vitest';
import de from './de.json';
import en from './en.json';
import { hasTranslation, t } from './index';
import { codeLabel, fieldLabel, validationLabel } from './codes';

describe('German platform vocabulary', () => {
  it('uses German labels for reasons, statuses, freshness and fields', () => {
    expect(codeLabel('state_unknown')).toContain('keinen gültigen Zustand');
    expect(codeLabel('event_stateful')).toBe('bis zur nächsten Änderung');
    expect(codeLabel('false')).toBe('Nein');
    expect(fieldLabel('Type Version')).toBe('Vorlagen-Version');
    expect(codeLabel('future_platform_code')).toBe('unbekannt');
  });
  it('interpolates structured validation metadata without server prose', () => {
    expect(validationLabel({ code: 'input_type_mismatch', path: '/contracts/0/inputs/0/ref', object_id: 'fixture.a', message: 'opaque server prose', parameters: { source: 'source.a', input: 'temperature', actual: 'text', expected: 'number', expectedUnit: '°C' } })).toBe('Quelle source.a liefert Text (keine Einheit); Eingang temperature erwartet Zahl (°C).');
  });
  it('keeps an English catalog prepared and fails loudly for missing keys', () => {
    expect(en).toEqual({ 'alpha9.category.openings': 'Openings', 'alpha9.category.time': 'Time, day and sun' });
    expect(hasTranslation('common.unknownCode')).toBe(true);
    expect(() => t('missing.test.key')).toThrow();
    expect(Object.values(de).every((text) => typeof text === 'string')).toBe(true);
  });
});
