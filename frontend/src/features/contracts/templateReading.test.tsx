import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { RuleReading } from '@/editors/RuleReading';
import { EvidenceTable } from '@/features/diagnostics/EvidenceLookup';
import { newDefinition } from '@/features/authoring/model';
import type { JsonObject } from '@/lib/json';
import { installFakeApi, reply, sampleConfig } from '../../../test/fakeApi';
import { caps, idleLive } from '../../../test/harness';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

function app(path: string) {
  window.location.hash = '#' + path;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <Workbench onSetup={() => {}} live={idleLive()} />
  </QueryClientProvider>);
}

it('reads a packaged opening beside its copy action and writes only a new definition', async () => {
  const api = installFakeApi();
  const original = newDefinition('opening.dual_contact', 1);
  const entry = { type_id: original.type_id, type_version: 1, producer: 'fusion',
    inputs: [{ name: 'open', required: true }, { name: 'tilt', required: true }],
    schema: { fields: { value: { type: 'enum', value_catalog: ['open', 'tilted', 'closed'] } } },
    editable_copy: original,
  };
  api.routes.push({ match: /^capabilities$/, respond: () => reply(200, { ...caps, types: [...caps.types, entry] }) });
  const user = userEvent.setup();
  app('/templates/details/opening.dual_contact/1');
  const table = await screen.findByRole('table', { name: 'Wie die Vorlage entscheidet' });
  expect(within(table).getByText('Unbekannt wegen widersprüchlicher Kontakte')).toBeVisible();
  expect(within(table).getByText('Offen mit eingeschränkter Verlässlichkeit')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Als eigene Vorlage übernehmen' }));
  const dialog = screen.getByRole('dialog', { name: 'Als eigene Vorlage übernehmen' });
  expect(api.drafts.size).toBe(0);
  await user.click(within(dialog).getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => expect([...api.drafts.values()][0]?.config.definitions).toEqual([
    { ...original, type_id: 'vertrag.opening_dual_contact', display_name: 'Öffnung mit zwei Kontakten' },
  ]));
  expect(original.type_id).toBe('opening.dual_contact');
});

it('shows a sentence and readable tables for annual points and parameterized mappings', () => {
  const definition = { parameter_schema: { morning: { label: 'Synthetischer Beginn', schema: { unit: 'minute_of_day' } } } };
  render(<>
    <RuleReading definition={definition} value={{ op: 'annual_curve', at: { op: 'input', name: 'datum' }, points: [
      { day: '01-01', value: 420 }, { day: '07-01', value: { op: 'parameter', name: 'morning' } },
    ] }} />
    <RuleReading definition={definition} value={{ op: 'map', arg: { op: 'input', name: 'datum' }, entries: {
      '"A"': { op: 'parameter', name: 'morning' }, '"B"': 600,
    } }} />
  </>);
  const curve = within(screen.getByRole('table', { name: 'Stichtage der Jahreskurve' }));
  expect(curve.getByRole('columnheader', { name: 'Uhrzeit' })).toBeVisible();
  expect(curve.getByText('07:00')).toBeVisible();
  expect(curve.getByText('Einstellung „Synthetischer Beginn“')).toBeVisible();
  expect(within(screen.getByRole('table', { name: 'Zuordnung von Rohwerten' })).getByText('A')).toBeVisible();
});

it('shows state deadlines, ordered guards and restart information before the sleep editor', async () => {
  const bool = { type: 'bool', required: true, physical_state: false };
  const yes = { op: 'literal', value: true, schema: bool };
  const definition: JsonObject = {
    ...newDefinition('sleep.flow', 1),
    rules: { available: { op: 'input', name: 'signal' } },
    machine: { id: 'synthetic', initial: 'awake', states: ['awake', 'pre_sleep'],
      transition_selection: 'ordered', unknown_deadline: 'keep_and_block_transition',
      required_inputs_by_state: { pre_sleep: ['signal'] }, deadline_by_state: { pre_sleep: { duration: 2700 } },
      reinitialize_when: { op: 'ref', name: 'available' },
      transitions: [
        { source: 'awake', target: 'pre_sleep', event: 'input', guard: { op: 'ref', name: 'available' }, new_episode: true },
        { source: 'pre_sleep', target: 'awake', event: 'deadline', guard: yes, ends_episode: true },
      ],
    },
  };
  installFakeApi({ revisions: [{ revision: 1, created_at: '2026-01-01T00:00:00Z', checksum: 'synthetic', config: sampleConfig({ definitions: [definition] }) }] });
  app('/templates/details/sleep.flow/1?tab=machine');
  const transitions = within(await screen.findByRole('table', { name: 'Übergänge zwischen Zuständen' }));
  expect(transitions.getByText(/Wert von „signal“/)).toBeVisible();
  expect(transitions.getByText('45 min')).toBeVisible();
  expect(transitions.getByText('Beendet die Schlafepisode.')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Was passiert nach einem Neustart?' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Was passiert bei unbekannten Werten?' })).toBeVisible();
});

it('shows calendar events as a list with contextual dates and window labels', () => {
  const start = '2026-01-01T11:00:00Z';
  const end = '2026-01-01T12:00:00Z';
  render(<EvidenceTable observation={{ value_raw: { window_start: start, window_end: end,
    events: [{ summary: 'Synthetischer Termin', start, end, all_day: false }],
  } }} />);
  expect(within(screen.getByRole('list', { name: 'Kalendertermine' })).getByText('Synthetischer Termin')).toBeVisible();
  expect(screen.getByText(/Abgedeckter Kalenderzeitraum/)).toBeVisible();
  expect(screen.queryByText(/Weckfenster|Anfangstag/)).not.toBeInTheDocument();
});
