import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { SourceEditor } from './RegistryObjects';
import { Stateful, wrap } from '../../test/harness';
import type { JsonObject } from '@/lib/json';
import { t } from '@/i18n';

it('edits manual limits without JSON and offers only valid manual freshness modes', async () => {
  let result: JsonObject = {};
  wrap(<Stateful initial={{ source_id: 'source.manual', source_type: 'numeric', source_origin: 'local', origin: 'manual', value_schema: { type: 'number', required: true, physical_state: false }, freshness: { mode: 'periodic_ttl', interval_s: 300, future_tolerance_s: 0 } }} onValue={value => { result = value; }}>{(value, onChange) => <SourceEditor value={value} onChange={onChange} pointer="/sources/0" dependencyIds={[]} sourceIds={['source.manual']} />}</Stateful>);
  expect(screen.queryByText('Manual Minimum')).toBeNull();
  expect(screen.queryByTestId('extras-Source')).toBeNull();
  const mode = screen.getByLabelText('Modus');
  expect([...mode.querySelectorAll('option')].map(option => option.value)).toEqual(['periodic_ttl', 'event_stateful']);
  expect(screen.getAllByText('5 Minuten')).toHaveLength(1);
  const user = userEvent.setup();
  const minimum = screen.getByLabelText(t('alpha9.manual.manual_minimum'));
  await user.type(minimum, '-10');
  expect(result.manual_minimum).toBe(-10);
  await user.clear(minimum);
  expect(result.manual_minimum).toBeUndefined();
  await user.click(screen.getByRole('switch', { name: t('alpha9.manual.future') }));
  expect(result.manual_allow_future).toBe(true);
});

it('offers the date-bound form only for a manual object source', async () => {
  let result: JsonObject = {};
  wrap(<Stateful initial={{ source_id: 'source.exception', source_type: 'state', origin: 'manual', value_schema: { type: 'object', required: true, physical_state: false }, freshness: { mode: 'event_stateful', future_tolerance_s: 0 } }} onValue={value => { result = value; }}>{(value, onChange) => <SourceEditor value={value} onChange={onChange} pointer="/sources/0" dependencyIds={[]} sourceIds={['source.exception']} />}</Stateful>);
  await userEvent.selectOptions(screen.getByLabelText(t('fields.manual_structured_schema')), 'wake_exception_v1');
  expect(result.manual_structured_schema).toBe('wake_exception_v1');
  expect(screen.queryByTestId('extras-Source')).toBeNull();
});
