import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { SourceEditor } from '@/editors/RegistryObjects';
import { validationLabel } from '@/i18n/codes';
import { Stateful, wrap } from '../../test/harness';

it('hides unused nullable freshness fields and shows required fields only in the matching mode', async () => {
  const user = userEvent.setup();
  wrap(<Stateful initial={{ source_id: 'source.test', source_type: 'boolean', freshness: { mode: 'event_stateful', interval_s: null, liveness_source: null, future_tolerance_s: 1 } }}>{(value, onChange) => <SourceEditor value={value} onChange={onChange} pointer="/sources/0" dependencyIds={[]} sourceIds={['source.test', 'source.reachable']} />}</Stateful>);
  expect(screen.getAllByText('Aktualität')).toHaveLength(1);
  expect(screen.queryByLabelText(/Intervall/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Quelle für die Erreichbarkeit/)).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Modus'), 'liveness_source');
  expect(screen.getByLabelText(/Intervall/)).toBeVisible();
  expect(screen.getByLabelText(/Quelle für die Erreichbarkeit/)).toBeVisible();
  expect(screen.getByText(/Nur bei diesem Modus nötig/)).toBeVisible();
});

it('explains a source lock with field, old and new value and the historical version', () => {
  const text = validationLabel({ code: 'immutable_source_type', path: '/sources/0/value_schema/unit', object_id: 'source.test', message: 'technical', parameters: { source_name: 'Zimmerthermometer', field: 'value_schema/unit', oldValue: '°C', newValue: '°F', revision: '3' } });
  expect(text).toContain('Quelle „Zimmerthermometer“');
  expect(text).not.toContain('source.test');
  expect(text).toContain('Einheit war in Konfiguration 3 „°C“');
  expect(text).toContain('soll „°F“ werden');
});
