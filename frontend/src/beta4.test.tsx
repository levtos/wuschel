import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { WakeExceptionFields } from '@/features/sources/ManualSourcePanel';
import { declarativeParameterSchema } from '@/features/authoring/catalog';
import { SchemaForm } from '@/editors/SchemaForm';
import { asObject } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';
import { caps, Stateful, wrap } from '../test/harness';

it('edits a date-bound exception without JSON and removes a time when skipping', async () => {
  let result: JsonObject = {};
  wrap(<Stateful initial={{ date: '2032-04-05', wake_time: '08:00' }} onValue={value => { result = value; }}>{(value, set) => <WakeExceptionFields value={value} onChange={next => set(asObject(next))} />}</Stateful>);
  fireEvent.change(screen.getByLabelText('Tag'), { target: { value: '2032-04-06' } });
  fireEvent.change(screen.getByLabelText('Weckzeit'), { target: { value: '09:00' } });
  expect(result).toEqual({ date: '2032-04-06', wake_time: '09:00' });
  await userEvent.click(screen.getByRole('checkbox'));
  expect(result).toEqual({ date: '2032-04-06', skip: true });
  expect(screen.queryByLabelText('Weckzeit')).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: /JSON/ })).not.toBeInTheDocument();
});

it('uses the registered structured schema for profile parameters and edits it as fields', () => {
  const schema = declarativeParameterSchema({ parameter_schema: { wake_settings: { schema: { type: 'object', required: true }, structured_schema: 'wake_planning_v1', label: 'Weckplanung' } } }, caps.structured_parameter_schemas);
  const model = asObject(asObject(schema.properties).wake_settings);
  expect(model.type).toBe('object');
  expect(asObject(schema.$defs).Profile).toBeDefined();
  let result: JsonObject = {};
  const value = { wake_settings: { profiles: { weekday: { id: 'weekday', label: 'Arbeitstag', wake_time: '08:00', wake_window_minutes: 5 }, weekend: { id: 'weekend', label: 'Freier Tag', wake_time: '09:00', wake_window_minutes: 5 } }, rules: [] } };
  wrap(<Stateful initial={value} onValue={next => { result = next; }}>{(current, set) => <SchemaForm root={schema} node={schema} value={current} onChange={next => set(asObject(next))} pointer="/parameters" />}</Stateful>);
  const inputs = screen.getAllByLabelText(/Weckzeit/);
  fireEvent.change(inputs[0]!, { target: { value: '07:30' } });
  expect(asObject(asObject(asObject(asObject(result.wake_settings).profiles).weekday)).wake_time).toBe('07:30');
  expect(screen.queryByRole('textbox', { name: /JSON/ })).not.toBeInTheDocument();
});
