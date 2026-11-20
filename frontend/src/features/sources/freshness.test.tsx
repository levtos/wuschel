import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { MemoryRouter } from 'react-router';
import { Stateful } from '../../../test/harness';
import { SourceFreshnessEditor } from './SourceFreshnessEditor';
import { FreshnessSettings } from './FreshnessSettings';
import { Workbench } from '@/Workbench';
import { DurationField, invalidDurations } from '@/components/SecondsField';
import { durationSeconds, durationText } from '@/lib/duration';
import { isObject, type JsonObject } from '@/lib/json';
import { installFakeApi, reply, sampleConfig } from '../../../test/fakeApi';
import { idleLive } from '../../../test/harness';
import { FreshnessAssignment, assignFreshness, type FreshnessChoices } from './FreshnessAssignment';
import { effectiveRule, freshnessChoiceLabel, freshnessLabel, ownRule, upgradeFreshness } from './freshness';
import { freshnessPreset, planDevice } from './devicePlan';

const rule = (seconds: number): JsonObject => ({ mode: 'report_heartbeat', interval_s: seconds, future_tolerance_s: 0 });
const source = (id: string, seconds = 8400): JsonObject => ({ source_id: `source.test.${id}`, display_name: `Synthetic ${id}`, source_type: 'boolean', source_origin: 'local', dependency_id: 'test.mqtt', physical_source_key: id, freshness: rule(seconds) });
const document = (): JsonObject => sampleConfig({ config_schema_version: 3, fingerprint_version: 3, sources: Array.from({ length: 7 }, (_, index) => source(String(index), index > 4 ? 10800 : 8400)) });
const template = (): JsonObject => ({ template_id: 'freshness.contact', display_name: 'Kontaktsensoren', sensor_kind: 'contact', rule: rule(10800) });
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); expect(invalidDurations.size).toBe(0); });

describe('UX-4 exact duration input', () => {
  it.each([[10800, '3 Stunden'], [5400, '90 Minuten'], [8400, '140 Minuten'], [30.125, 'Weniger als 1 Minute'], [0, '0 Minuten']])('preserves %s while displaying %s', (seconds, text) => {
    const changed = vi.fn(); render(<DurationField label="Frist" value={Number(seconds)} onChange={changed} />);
    expect(screen.getByText(new RegExp(String(text)))).toBeVisible(); expect(changed).not.toHaveBeenCalled();
  });
  it('changes units without changing payload and accepts German 1,5 hours', async () => {
    const user = userEvent.setup(); const changed = vi.fn(); render(<DurationField label="Frist" value={10800} onChange={changed} />);
    expect(screen.getByLabelText('Frist')).toHaveValue('3');
    await user.selectOptions(screen.getByLabelText('Einheit'), 'minutes');
    expect(screen.getByLabelText('Frist')).toHaveValue('180'); expect(changed).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText('Einheit'), 'hours');
    await user.clear(screen.getByLabelText('Frist')); await user.type(screen.getByLabelText('Frist'), '1,5');
    expect(changed).toHaveBeenLastCalledWith(5400);
  });
  it('keeps an exact short legacy value until explicitly edited and retains errors', async () => {
    const user = userEvent.setup(); const changed = vi.fn(); render(<DurationField label="Frist" value={17.123456} min={0.000001} onChange={changed} />);
    expect(changed).not.toHaveBeenCalled(); await user.click(screen.getByRole('button', { name: 'Zeit ändern' }));
    await user.clear(screen.getByLabelText('Frist')); await user.type(screen.getByLabelText('Frist'), 'NaN');
    expect(screen.getByRole('alert')).toHaveTextContent('Diese Zeit lässt sich so nicht genau speichern');
    expect(screen.getByLabelText('Frist')).toHaveValue('NaN'); expect(invalidDurations.size).toBe(1); expect(changed).not.toHaveBeenCalled();
  });
  it.each(['', '-1', 'Infinity', 'NaN', '0.0000000000001'])('rejects %s without substitution', value => { expect(durationSeconds(value, 'minutes')).toBeNull(); });
  it('uses exact seconds with singular/plural and does not truncate decimals', () => { expect(durationSeconds('1,5', 'hours')).toBe(5400); expect(durationSeconds('140', 'minutes')).toBe(8400); expect(durationText(60)).toBe('1 Minute'); expect(durationText(3600)).toBe('1 Stunde'); });
});

describe('UX-3 conscious shared and own selections', () => {
  it('describes whole own/shared conflict choices in German with minutes and hours', () => {
    const config = upgradeFreshness(document()); config.freshness_templates = [template()];
    const own = freshnessChoiceLabel(config, { kind: 'own', rule: rule(9000) });
    const shared = freshnessChoiceLabel(config, { kind: 'shared', template_id: 'freshness.contact' });
    expect(own).toContain('Eigene Regel'); expect(own).toContain('150 Minuten');
    expect(shared).toContain('Gemeinsame Vorlage: Kontaktsensoren'); expect(shared).toContain('3 Stunden');
    expect(`${own} ${shared}`).not.toMatch(/report_heartbeat|interval_s|template_id/);
  });
  it('requires an explicit interval after choosing a method for a previously blank rule', async () => {
    const user = userEvent.setup(); let result: JsonObject = {};
    render(<Stateful initial={{}} onValue={value => { result = value; }}>{(value, onChange) => <FreshnessSettings value={value} onChange={onChange} template />}</Stateful>);
    await user.selectOptions(screen.getByLabelText('Wann gilt der Wert als aktuell?'), 'periodic_ttl');
    expect(result.interval_s).toBeUndefined();
    const interval = screen.getByLabelText('Wie oft meldet sich der Sensor spätestens?');
    expect(interval).toHaveValue(''); expect(invalidDurations.size).toBe(1);
    await user.type(interval, '15');
    expect(result.interval_s).toBe(900); expect(invalidDurations.size).toBe(0);
  });
  it('edits a conscious own deviation, displays both rules, and returns completely to shared', async () => {
    const user = userEvent.setup(); const config = upgradeFreshness(document()); config.freshness_templates = [template()];
    const initial: JsonObject = { ...(config.sources as JsonObject[])[0]!, sensor_kind: 'contact', sensor_kind_origin: 'confirmed', freshness_selection: { kind: 'shared', template_id: 'freshness.contact' } };
    let result = initial;
    render(<MemoryRouter><Stateful initial={initial} onValue={value => { result = value; }}>{(value, onChange) => <SourceFreshnessEditor value={value} onChange={onChange} sources={[]} config={config} />}</Stateful></MemoryRouter>);
    await user.click(screen.getByRole('button', { name: 'Für diese Quelle abweichen' }));
    expect(screen.getByText(/zurzeit gleicher Wert/)).toBeVisible();
    const input = screen.getByLabelText('Wie oft meldet sich der Sensor spätestens?');
    await user.clear(input); await user.type(input, '4');
    expect(screen.getByText('Diese Quelle: 4 Stunden · Gemeinsame Vorlage: 3 Stunden')).toBeVisible();
    expect(freshnessLabel(config, result)).toContain('weicht ab');
    await user.selectOptions(screen.getByLabelText('Gemeinsame Vorlage verwenden'), 'freshness.contact');
    expect(result.freshness_selection).toEqual({ kind: 'shared', template_id: 'freshness.contact' });
    expect(effectiveRule(config, result).interval_s).toBe(10800);
  });
  it('preserves damaged drafts by rejecting an ambiguous conversion rather than dropping objects', () => {
    expect(() => upgradeFreshness(sampleConfig({ sources: [null] }))).toThrow();
    expect(() => upgradeFreshness(sampleConfig({ freshness_templates: [null] }))).toThrow();
  });
  it('does not assign seven legacy sources or introduce active defaults on upgrade', () => {
    const original = document(); const upgraded = upgradeFreshness(original);
    expect(original.config_schema_version).toBe(3); expect(upgraded.freshness_templates).toEqual([]);
    const rows = upgraded.sources as JsonObject[];
    expect(rows).toHaveLength(7); expect(rows.every(row => (row.freshness_selection as JsonObject).kind === 'own')).toBe(true);
    expect(rows.map(row => effectiveRule(upgraded, row).interval_s)).toEqual([8400, 8400, 8400, 8400, 8400, 10800, 10800]);
  });
  it('keeps own equality independent, shows deviation and switches back completely', () => {
    const config = upgradeFreshness(document()); config.freshness_templates = [template()];
    const row = { ...(config.sources as JsonObject[])[0]!, freshness_selection: { kind: 'shared', template_id: 'freshness.contact' } };
    const own = ownRule(row, rule(10800));
    expect(freshnessLabel(config, own)).toContain('zurzeit gleicher Wert');
    (config.freshness_templates[0] as JsonObject).rule = rule(14400);
    expect(freshnessLabel(config, own)).toContain('weicht ab'); expect(effectiveRule(config, own).interval_s).toBe(10800);
    expect(effectiveRule(config, row).interval_s).toBe(14400);
  });
  it('offers no guessed type for bool/name and groups seven explicit choices', async () => {
    const user = userEvent.setup(); const config = upgradeFreshness(document()); config.freshness_templates = [template()];
    function Assignment() { const [choices, setChoices] = useState<FreshnessChoices>({}); return <><FreshnessAssignment config={config} choices={choices} onChange={setChoices} /><output data-testid="choices">{JSON.stringify(choices)}</output></>; }
    render(<Assignment />);
    expect(screen.getByLabelText('Sensortyp bestätigen')).toHaveValue('');
    expect(screen.getByTestId('choices')).toHaveTextContent('{}');
    await user.selectOptions(screen.getByLabelText('Sensortyp bestätigen'), 'contact');
    await user.selectOptions(screen.getByLabelText('Aktualität zuordnen'), 'freshness.contact');
    const choices = JSON.parse(screen.getByTestId('choices').textContent!);
    expect(Object.keys(choices)).toHaveLength(7);
    const assigned = assignFreshness(config, choices);
    expect((assigned.sources as JsonObject[]).every(row => effectiveRule(assigned, row).interval_s === 10800)).toBe(true);
    expect((config.sources as JsonObject[])[0]!.freshness_selection).toEqual({ kind: 'own', rule: rule(8400) });
  });
  it('leaves temperature/motion/counter blank and requires explicit freshness for a new device', () => {
    for (const kind of ['temperature', 'motion', 'counter', 'other'] as const) expect(freshnessPreset(kind)).toEqual({});
    expect(freshnessPreset('contact')).toMatchObject({ mode: 'report_heartbeat', interval_s: 10800 });
    expect(() => planDevice(sampleConfig(), { entity_id: 'binary_sensor.synthetic_contact_name', display_name: 'Synthetic contact', attributes: [], unit: null, device_class: null, state_class: null })).toThrow();
  });
});

it('maintains one page primary action and saves a proposed contact template only in the draft', async () => {
  const user = userEvent.setup(); const activeConfig = document();
  const api = installFakeApi({ revisions: [{ revision: 2, config: activeConfig, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  api.routes.push({ match: /^registry\/freshness$/, respond: () => reply(200, { sources: {}, templates: {} }) });
  window.location.hash = '#/sources/freshness';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  expect(await screen.findByRole('heading', { name: 'Aktualität', level: 1 })).toBeVisible();
  const header = screen.getByRole('heading', { level: 1 }).closest('[data-page-header]')!;
  expect(within(header as HTMLElement).getAllByRole('button')).toHaveLength(1);
  expect(api.drafts.size).toBe(0);
  await user.click(within(header as HTMLElement).getByRole('button', { name: 'Bearbeiten' }));
  const dialog = await screen.findByRole('dialog', { name: 'Kontaktsensoren' });
  expect(within(dialog).getByLabelText('Wie oft meldet sich der Sensor spätestens?')).toHaveValue('3');
  expect(within(dialog).getAllByLabelText('Einheit')[0]).toHaveValue('hours');
  await user.click(within(dialog).getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => expect([...api.drafts.values()][0]!.config.freshness_templates).toHaveLength(1));
  expect(api.revisions).toHaveLength(1); expect(activeConfig.config_schema_version).toBe(3);
  expect((api.drafts.values().next().value!.config.sources as JsonObject[]).every(row => isObject(row.freshness_selection) && row.freshness_selection.kind === 'own')).toBe(true);
});

it('shows and edits every imported template even when two templates have the same sensor kind', async () => {
  const user = userEvent.setup(); const config = upgradeFreshness(document());
  config.freshness_templates = [template(), { ...template(), template_id: 'freshness.imported', display_name: 'Synthetic imported contacts', rule: rule(14400) }];
  const api = installFakeApi({ revisions: [{ revision: 2, config, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  api.routes.push({ match: /^registry\/freshness$/, respond: () => reply(200, { sources: {}, templates: {} }) });
  window.location.hash = '#/sources/freshness';
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  const imported = await screen.findByRole('heading', { name: 'Synthetic imported contacts' });
  expect(screen.getByRole('heading', { name: 'Kontaktsensoren' })).toBeVisible();
  await user.click(within(imported.parentElement!).getByRole('button', { name: 'Bearbeiten' }));
  const dialog = await screen.findByRole('dialog', { name: 'Kontaktsensoren' });
  const interval = within(dialog).getByLabelText('Wie oft meldet sich der Sensor spätestens?');
  expect(interval).toHaveValue('4'); await user.clear(interval); await user.type(interval, '5');
  await user.click(within(dialog).getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => {
    const rows = [...api.drafts.values()][0]!.config.freshness_templates as JsonObject[];
    expect(rows.find(row => row.template_id === 'freshness.contact')!.rule).toEqual(rule(10800));
    expect(rows.find(row => row.template_id === 'freshness.imported')).toMatchObject({ display_name: 'Synthetic imported contacts', rule: { interval_s: 18000 } });
  });
});
