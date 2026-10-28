import { cleanup, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import type { JsonObject } from '@/lib/json';
import { Stateful, wrap } from '../../test/harness';
import { CatalogEditor } from './RegistryObjects';

afterEach(cleanup);
it('keeps legend names and explanations while editing classes and prunes removed keys', async () => {
  let current: JsonObject = { catalog_id: 'synthetic', kind: 'managed', version: 1, classification_schema: { type: 'enum', values: ['normal', 'privat'], legend: { normal: { display_name: 'Normal', description: 'Synthetic explanation.' }, privat: { display_name: 'Privat', description: 'Synthetic private explanation.' } } }, unassigned_value: 'normal', master_depth: 1 };
  wrap(<Stateful initial={current} onValue={value => { current = value; }}>{(value, set) => <CatalogEditor value={value} onChange={set} pointer="/catalogs/0" />}</Stateful>);
  const user = userEvent.setup();
  const group = screen.getByRole('group', { name: 'privat' });
  await user.clear(within(group).getByLabelText('Anzeigename')); await user.type(within(group).getByLabelText('Anzeigename'), 'Persönlich');
  expect((current.classification_schema as JsonObject).legend).toMatchObject({ privat: { display_name: 'Persönlich', description: 'Synthetic private explanation.' } });
  const classes = screen.getByRole('textbox', { name: /Klassen/ });
  await user.clear(classes); await user.type(classes, 'normal');
  expect((current.classification_schema as JsonObject).legend).toEqual({ normal: { display_name: 'Normal', description: 'Synthetic explanation.' } });
});
