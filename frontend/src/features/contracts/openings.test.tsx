import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { Stateful, wrap } from '../../../test/harness';
import { asArray, asObject } from '@/features/authoring/model';
import { setAt, type JsonObject } from '@/lib/json';
import { OpeningHeartbeatSettings } from './OpeningHeartbeatSettings';
import { CategoryBadge, CategorySelect } from '@/features/editorial/Editorial';
import { categoryIcons, findMetadata } from '@/features/editorial/model';
import en from '@/i18n/en.json';

it('B1-14 changes the bound source slot in the draft without a shadow parameter', async () => {
  const contract = { contract_id: 'test.opening', type_id: 'opening.single_contact', type_version: 1, inputs: [{ name: 'contact', ref: 'source.test' }], parameters: { grace_s: 600 } };
  const initial: JsonObject = { sources: [{ source_id: 'source.test', display_name: 'Testkontakt', freshness: { mode: 'report_heartbeat', interval_s: 8400 } }, { source_id: 'source.other', freshness: { mode: 'report_heartbeat', interval_s: 99 } }], contracts: [contract] };
  let result: JsonObject = initial;
  wrap(<Stateful initial={initial} onValue={next => { result = next; }}>{(config, set) => <OpeningHeartbeatSettings config={config} contract={contract} onChange={(index, interval) => set(setAt(config, `/sources/${index}/freshness/interval_s`, interval))} />}</Stateful>);
  expect(screen.getByText(/erst, wenn du den Entwurf übernimmst/)).toBeVisible();
  const input = screen.getByLabelText('Zeit bis zur nächsten Meldung von Testkontakt');
  await userEvent.clear(input);
  expect(screen.getByRole('alert')).toBeVisible();
  await userEvent.type(input, '150');
  expect(asObject(asObject(asArray(result.sources)[0]).freshness).interval_s).toBe(9000);
  expect(asObject(asObject(asArray(result.sources)[1]).freshness).interval_s).toBe(99);
  expect(result.contracts).toEqual(initial.contracts);
});

it('renders the approved openings category, icon and English catalog label with legacy fallback', () => {
  wrap(<><CategoryBadge category="openings" /><CategorySelect value="other" onChange={() => {}} /></>);
  expect(screen.getByRole('option', { name: 'Öffnungen' })).toBeVisible();
  expect(categoryIcons.openings).toBe('mdi:window-open-variant');
  expect(en['alpha9.category.openings']).toBe('Openings');
  expect(findMetadata(undefined, { kind: 'contract', id: 'test.old' }).category).toBe('other');
});
