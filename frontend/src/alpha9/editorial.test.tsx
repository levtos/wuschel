import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Workbench } from '@/Workbench';
import { mdiPath } from '@/components/MdiIcon';
import { CATEGORIES, NO_ROOM, groupItems, suggestedRoom, type EditorialRow } from '@/features/editorial/model';
import type { HaEntity, Registry } from '@/lib/types';
import { asArray } from '@/features/authoring/model';
import { isObject } from '@/lib/json';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
const entity: HaEntity = { entity_id: 'sensor.temp', display_name: 'Temperatur', unit: '°C', device_class: 'temperature', state_class: null, attributes: [], area_id: 'office', area_name: 'Büro', area_icon: 'mdi:desk' };
const at = '2026-10-18T08:00:00Z';
function app(hash: string) {
  window.location.hash = hash;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench live={idleLive()} onSetup={() => {}} /></QueryClientProvider>);
}
function setup() {
  const config = sampleConfig({ contracts: [{ contract_id: 'temperature', type_id: 'test.echo', type_version: 1, display_name: 'Temperatur', enabled: true, inputs: [{ name: 'value', ref: 'source.temp' }] }], sources: [{ source_id: 'source.temp', source_type: 'numeric', value_schema: { type: 'number', required: true, physical_state: false, unit: '°C' } }], bindings: [{ source_id: 'source.temp', adapter: { kind: 'ha_state', ha_entity_id: entity.entity_id } }] });
  const fake = installFakeApi({ revisions: [{ revision: 1, checksum: 'sum', config, created_at: at }] });
  fake.routes.push({ match: /^ha\/entities/, respond: () => reply(200, { items: [entity], next_cursor: null, read_only: true }) });
  return { config: config as Registry, fake };
}

it('suggests only one fully known direct HA area and never guesses from derived or missing inputs', () => {
  const { config } = setup(); const contract = asArray(config.contracts).filter(isObject)[0]!;
  expect(suggestedRoom(config, contract, [entity]).room?.name).toBe('Büro');
  expect(suggestedRoom(config, contract, []).room).toBeNull();
  expect(suggestedRoom(config, { ...contract, inputs: [{ name: 'value', ref: 'temperature.value' }] }, [entity]).room).toBeNull();
  config.sources = [...asArray(config.sources), { source_id: 'source.other' }]; config.bindings = [...asArray(config.bindings), { source_id: 'source.other', adapter: { kind: 'ha_attribute', ha_entity_id: 'sensor.other' } }];
  const second = { ...entity, entity_id: 'sensor.other', area_id: 'hall', area_name: 'Flur' };
  const multiple = { ...contract, inputs: [...asArray(contract.inputs), { name: 'other', ref: 'source.other' }] };
  expect(suggestedRoom(config, multiple, [entity, second])).toEqual({ room: null, multiple: true });
});

it('loads local area icons and uses the house for unknown icons or external markup without fetching', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  expect(await mdiPath('mdi:desk')).not.toBe(await mdiPath('mdi:home'));
  expect(await mdiPath('https://example.org/icon.svg')).toBe(await mdiPath('mdi:home'));
  expect(await mdiPath('mdi:not-in-the-pinned-set')).toBe(await mdiPath('mdi:home'));
  expect(fetch).not.toHaveBeenCalled();
  expect(CATEGORIES).toHaveLength(9);
  expect(CATEGORIES).toContain('devices');
  expect(CATEGORIES).toContain('time');
  expect(groupItems([{ category: 'climate' }], row => row.category)).toEqual([['climate', [{ category: 'climate' }]]]);
});

it('saves category and a confirmed room directly without creating or adopting a configuration', async () => {
  const user = userEvent.setup(); const { fake, config } = setup(); const before = structuredClone(config);
  app('#/contracts/view/temperature');
  await user.click(await screen.findByText('Kategorie und Bereich ändern'));
  await user.selectOptions(await screen.findByLabelText('Kategorie'), 'climate');
  await user.click(await screen.findByRole('button', { name: 'Vorschlag verwenden' }));
  await user.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(fake.editorial.get('contract:temperature')).toMatchObject({ category: 'climate', room: { mode: 'suggested', area_id: 'office', name: 'Büro' } }));
  expect(fake.revisions).toHaveLength(1); expect(fake.revisions[0]!.config).toEqual(before); expect(fake.drafts.size).toBe(0);
  expect(fake.calls.some(call => call.path === 'registry/rollback' || call.path.endsWith('/activate'))).toBe(false);
});

it('keeps metadata on a new draft contract and offers category and room filters and groups', async () => {
  const user = userEvent.setup(); const { fake } = setup();
  const meta: EditorialRow = { object: { kind: 'contract', id: 'temperature' }, editorial_version: 1, category: 'climate', room: { ...NO_ROOM, mode: 'manual', name: 'Büro' } };
  fake.editorial.set('contract:temperature', meta);
  app('#/contracts/list/all');
  await user.selectOptions(await screen.findByLabelText('Kategorie'), 'media');
  expect(screen.queryByRole('link', { name: 'Temperatur' })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Kategorie'), 'climate');
  expect(await screen.findByRole('link', { name: 'Temperatur' })).toBeVisible();
  await user.selectOptions(screen.getByLabelText('Ordnen'), 'room');
  expect(screen.getByRole('heading', { name: 'Büro', level: 2 })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Neuer Vertrag' }));
  const wizard = await screen.findByRole('dialog', { name: 'Neuer Vertrag' });
  await user.click(await within(wizard).findByRole('radio', { name: 'Temperatur' }));
  await user.selectOptions(within(wizard).getByLabelText('Wann gilt der Wert als aktuell?'), 'periodic_ttl');
  await user.type(within(wizard).getByLabelText('Wie oft meldet sich der Sensor spätestens?'), '60');
  await user.selectOptions(within(wizard).getByLabelText('Kategorie'), 'media');
  await user.click(within(wizard).getByRole('button', { name: 'Vertrag anlegen' }));
  await waitFor(() => expect(fake.pendingEditorial.size).toBe(1));
  const pending = [...fake.pendingEditorial.values()][0]!;
  expect([...pending.values()][0]!.category).toBe('media');
  expect(fake.editorial.size).toBe(1); expect(fake.revisions).toHaveLength(1);
  const update = fake.calls.find(call => call.method === 'PUT' && call.path.startsWith('registry/drafts/'));
  expect(update?.body).toHaveProperty('editorial');
});

it('chooses a category while creating a template and keeps it outside its definition', async () => {
  const user = userEvent.setup(); const { fake } = setup(); app('#/templates/own');
  await user.click(await screen.findByRole('button', { name: 'Neue Vorlage' }, { timeout: 5000 }));
  const dialog = await screen.findByRole('dialog', { name: 'Neue Vorlage' });
  await user.type(within(dialog).getByLabelText(/^Name/), 'Schlafzeit');
  await user.selectOptions(within(dialog).getByLabelText('Kategorie'), 'sleep_wake');
  await user.click(within(dialog).getByRole('button', { name: 'Anlegen' }));
  await waitFor(() => expect(fake.pendingEditorial.size).toBe(1));
  const pending = [...fake.pendingEditorial.values()][0]!;
  expect([...pending.values()]).toEqual([expect.objectContaining({ object: { kind: 'template', id: 'vertrag.schlafzeit' }, category: 'sleep_wake' })]);
  const draft = [...fake.drafts.values()][0]!;
  expect(asArray(draft.config.definitions).filter(isObject)[0]).not.toHaveProperty('category');
  expect(fake.revisions).toHaveLength(1);
});
