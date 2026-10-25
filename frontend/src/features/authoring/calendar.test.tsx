import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { RuleEditor, RuleEnvProvider } from '@/editors/RuleEditor';
import { CategoryBadge, CategorySelect } from '@/features/editorial/Editorial';
import { categoryIcons } from '@/features/editorial/model';
import { clone, type JsonObject } from '@/lib/json';
import { caps, Stateful, wrap } from '../../../test/harness';
import { newDefinition, newRule, referencedNames, renameReferences, ruleContext, withMinimumLanguage, type Rule } from './model';

function editor(initial: Rule, changed = (_: Rule) => {}, locked = false) {
  return wrap(<RuleEnvProvider value={{ ctx: ruleContext(newDefinition('test.calendar', 1), false), caps, locked }}><Stateful initial={initial} onValue={v => changed(v as Rule)}>{(value, set) => <RuleEditor value={value as Rule} onChange={set} pointer="/outputs/value" />}</Stateful></RuleEnvProvider>);
}

it('edits annual anchors as a table with numeric parameter references and row errors, preserving untouched data', async () => {
  const user = userEvent.setup(); let current: Rule | undefined;
  const initial: Rule = { op: 'annual_curve', at: { op: 'input', name: 'date' }, points: [{ day: '01-01', value: 0 }, { day: '07-01', value: 10 }], leap_day: 'skip' };
  editor(initial, next => { current = next; });
  expect(screen.getByText(/Diese Regel benötigt Regelsprache 5/)).toBeVisible();
  fireEvent.change(screen.getAllByLabelText('Stützwert')[1]!, { target: { value: '12.5' } });
  expect(current).toEqual({ ...initial, points: [{ day: '01-01', value: 0 }, { day: '07-01', value: 12.5 }] });
  await user.selectOptions(screen.getAllByLabelText('Wert beziehen aus')[1]!, 'parameter');
  await user.type(screen.getByLabelText('Parametername'), 'summer');
  expect(current!.points).toEqual([{ day: '01-01', value: 0 }, { day: '07-01', value: { op: 'parameter', name: 'summer' } }]);
  fireEvent.change(screen.getAllByLabelText('Stichtag (MM-TT)')[1]!, { target: { value: '01-01' } });
  expect(screen.getAllByRole('alert').map(el => el.textContent)).toContain('Diesen Stichtag hast du bereits verwendet.');
  expect(screen.queryByRole('textbox', { name: /JSON/ })).not.toBeInTheDocument();
});

it('edits calendar matching, all-day choice and preserves the list reference without exposing source contents', async () => {
  const user = userEvent.setup(); let current: Rule | undefined;
  const initial: Rule = { op: 'calendar_active', arg: { op: 'input', name: 'calendar' }, pattern: { op: 'literal', schema: { type: 'text', required: true, physical_state: false }, value: '*SYNTHETIC*' }, all_day_only: false };
  editor(initial, next => { current = next; });
  await user.click(screen.getByRole('checkbox', { name: 'Nur ganztägige Termine' }));
  expect(current).toEqual({ ...initial, all_day_only: true });
  expect(screen.getByText(/steht für beliebige Zeichen/)).toBeVisible();
});

it('keeps the new controls disabled for activated templates', () => {
  const changed = vi.fn();
  editor({ op: 'annual_curve', at: { op: 'input', name: 'date' }, points: [{ day: '01-01', value: 0 }, { day: '07-01', value: 1 }] }, changed, true);
  for (const input of screen.getAllByLabelText('Stützwert')) expect(input).toBeDisabled();
  expect(changed).not.toHaveBeenCalled();
});

it('roundtrips language 5, raises it only on edits and traverses date, pattern and numeric parameters', () => {
  const old: JsonObject = { language_version: 2, outputs: { value: { op: 'input', name: 'a' } } };
  expect(withMinimumLanguage(old)).toBe(old);
  const ctx = ruleContext(newDefinition('test.calendar', 1), false);
  const annual = newRule('annual_curve', ctx, { formulas: [], fusion: [], temporal: [] });
  annual.points = [{ day: '01-01', value: { op: 'parameter', name: 'winter' } }, { day: '07-01', value: 1 }];
  const doc: JsonObject = { ...old, outputs: { curve: annual, active: { op: 'calendar_active', arg: { op: 'input', name: 'calendar' }, pattern: { op: 'parameter', name: 'pattern' }, all_day_only: true } } };
  const snapshot = JSON.stringify(doc);
  expect(withMinimumLanguage(doc).language_version).toBe(5);
  expect(JSON.stringify(doc)).toBe(snapshot);
  expect(clone(doc)).toEqual(doc);
  expect(referencedNames(doc, 'parameter').get('winter')).toBe(1);
  const renamed = renameReferences(doc, 'parameter', 'pattern', 'vacation');
  expect(referencedNames(renamed, 'parameter').get('vacation')).toBe(1);
  expect(referencedNames(renamed, 'parameter').has('pattern')).toBe(false);
});

it('offers the time category and its bundled icon', () => {
  wrap(<><CategoryBadge category="time" /><CategorySelect value="other" onChange={() => {}} /></>);
  expect(screen.getByRole('option', { name: 'Zeit, Tag und Sonne' })).toBeInTheDocument();
  expect(categoryIcons.time).toBe('mdi:weather-sunset');
});
