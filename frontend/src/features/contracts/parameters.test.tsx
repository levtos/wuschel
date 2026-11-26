import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { Workbench } from '@/Workbench';
import { asArray, asObject, newDefinition } from '@/features/authoring/model';
import { installFakeApi, sampleConfig } from '../../../test/fakeApi';
import { idleLive } from '../../../test/harness';

const declaration = {
  type: 'number', required: true, physical_state: false,
};
const fixture = sampleConfig({
  definitions: [
    {
      ...newDefinition('test.settings', 1),
      display_name: 'Synthetischer Zeitplan',
      language_version: 2,
      parameter_schema: {
        start: { schema: { ...declaration, unit: 'minute_of_day' }, label: 'Schaltzeit', description: 'Zeitpunkt für den synthetischen Start', group: 'Synthetische Zeiten', display: 'time_of_day', default: 420, minimum: 0, maximum: 1439 },
        delay_s: { schema: { ...declaration, unit: 's' }, label: 'Anzeigedauer (s)', group: 'Synthetische Zeiten', display: 'duration', default: 900, minimum: 60, maximum: 3600 },
        monday: { schema: { type: 'bool', required: true, physical_state: false }, label: 'Montag', group: 'Synthetische Tage', display: 'weekday', default: false },
      },
    },
    { ...newDefinition('test.simple', 1), display_name: 'Synthetische Vorlage' },
  ],
  contracts: [
    { contract_id: 'synthetic.settings', type_id: 'test.settings', type_version: 1, enabled: true, inputs: [], parameters: { start: 480 } },
    { contract_id: 'synthetic.simple', type_id: 'test.simple', type_version: 1, enabled: true, inputs: [] },
  ],
});
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

function app(path: string) {
  const api = installFakeApi({ revisions: [{ revision: 1, created_at: '2026-01-01T00:00:00Z', checksum: 'synthetic', config: fixture }] });
  window.location.hash = '#' + path;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
  return api;
}

it('groups contract settings and stores clock minutes, duration seconds and weekday booleans', async () => {
  const api = app('/contracts/view/synthetic.settings?tab=config');
  const user = userEvent.setup();
  const group = await screen.findByRole('heading', { name: 'Synthetische Zeiten' });
  expect(within(group.closest('section')!).getByText('08:00')).toBeVisible();
  expect(within(group.closest('section')!).getByText('07:00')).toBeVisible();
  await user.click(await screen.findByRole('button', { name: 'Bearbeiten' }));
  const editor = within(await screen.findByRole('dialog', { name: 'Vertrag bearbeiten' }));
  expect(editor.getByText(/Was dieser Vertrag berechnet/)).toHaveTextContent('Die Berechnung änderst du über eine neue Vorlagenversion.');
  expect(editor.getByRole('link', { name: 'Vorlage ansehen' })).toHaveAttribute('href', '#/templates/details/test.settings/1');

  const time = editor.getByLabelText('Schaltzeit');
  const timeRow = within(time.closest('li')!);
  expect(time).toHaveAttribute('type', 'time');
  await user.click(timeRow.getByRole('button', { name: 'Auf Standard zurücksetzen' }));
  expect(time).toHaveValue('07:00');
  fireEvent.change(time, { target: { value: '06:30' } });

  const duration = editor.getByLabelText('Anzeigedauer');
  expect(duration).toHaveValue('15');
  await user.selectOptions(editor.getByLabelText('Einheit'), 'hours');
  await user.clear(duration);
  await user.type(duration, '0,5');
  const monday = editor.getByRole('checkbox', { name: 'Montag' });
  expect(monday).not.toBeChecked();
  await user.click(monday);

  expect(api.drafts.size).toBe(0);
  await user.click(editor.getByRole('button', { name: 'In Entwurf speichern' }));
  await waitFor(() => expect(asObject(asArray([...api.drafts.values()][0]?.config.contracts)[0]).parameters)
    .toEqual({ start: 390, delay_s: 1800, monday: true }));
  expect(asObject(asArray(api.revisions[0]?.config.contracts)[0]).parameters).toEqual({ start: 480 });
});

it('links to the output calculation when the contract has no settings', async () => {
  app('/contracts/view/synthetic.simple?tab=config');
  expect(await screen.findByText(/Dieser Vertrag hat keine eigenen Einstellungen/)).toHaveTextContent('Synthetische Vorlage');
  expect(screen.getByRole('link', { name: 'Regel ansehen' })).toHaveAttribute('href', '#/templates/details/test.simple/1?tab=outputs');
});

it('shows the template parameters as a labelled table with technical details collapsed', async () => {
  app('/templates/details/test.settings/1');
  const table = within(await screen.findByRole('table', { name: 'Parameter der Vorlage' }));
  for (const column of ['Name', 'Erklärung', 'Typ', 'Einheit', 'Standard', 'Bereich', 'Gruppe', 'Darstellung']) {
    expect(table.getByRole('columnheader', { name: column })).toBeVisible();
  }
  const row = within(table.getByRole('rowheader', { name: 'Schaltzeit' }).closest('tr')!);
  for (const value of ['Zeitpunkt für den synthetischen Start', '07:00', '00:00 bis 23:59', 'Synthetische Zeiten']) {
    expect(row.getByText(value)).toBeVisible();
  }
  expect(screen.getByRole('button', { name: 'Technische Details' })).toHaveAttribute('aria-expanded', 'false');
});
