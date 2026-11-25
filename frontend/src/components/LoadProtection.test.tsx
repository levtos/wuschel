import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, expect, it } from 'vitest';
import { LoadWaitDialog } from './LoadProtection';
import { Dialog } from './ui/dialog';
import { ActionCancelled } from '@/lib/api';
import { loadWait } from '@/lib/load-wait';
import { validateConfig } from '@/lib/queries';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { wrap } from '../../test/harness';

const protectedBody = {
  error: 'load_protected',
  measurements: [{ metric: 'lag_ms', value: 625, threshold: 500 }],
};

afterEach(() => loadWait.finish());

function Validation() {
  const [result, setResult] = useState('');
  return <>
    <Dialog open onOpenChange={() => {}} title="Synthetischer Entwurf">
      <button onClick={() => {
        void validateConfig(sampleConfig(), true).then(() => setResult('Geprüft'), error => {
          setResult(error instanceof ActionCancelled ? 'Abgebrochen' : 'Abgewiesen');
        });
      }}>Prüfen</button>
      <output>{result}</output>
    </Dialog>
    <LoadWaitDialog />
  </>;
}

function mount() {
  const fake = installFakeApi();
  fake.failNext.push({ path: /^registry\/validate$/, status: 503, body: protectedBody });
  let release!: (response: Response) => void;
  fake.routes.push({ match: /^operations\/wait\?budget_ms=\d+$/, respond: () => new Promise(resolve => { release = resolve; }) });
  wrap(<Validation />);
  return { release: (response: Response) => release(response), validations: () => fake.calls.filter(call => call.path === 'registry/validate') };
}

it('shows a cancellable wait above the invoking dialog and retries validation once on recovery', async () => {
  const { validations, release } = mount();
  await userEvent.setup().click(screen.getByRole('button', { name: 'Prüfen' }));
  expect(await screen.findByRole('dialog', { name: 'Wuschel ist kurz beschäftigt' })).toBeVisible();
  expect(validations()).toHaveLength(1);
  await act(async () => release(new Response(null, { status: 204 })));
  expect(await screen.findByText('Geprüft')).toBeVisible();
  expect(validations()).toHaveLength(2);
  expect(validations()[1]?.body).toEqual(validations()[0]?.body);
});

it('shows the measurements and link when protection outlasts the wait', async () => {
  const { validations, release } = mount();
  await userEvent.setup().click(screen.getByRole('button', { name: 'Prüfen' }));
  await screen.findByRole('dialog', { name: 'Wuschel ist kurz beschäftigt' });
  await act(async () => release(reply(503, protectedBody)));
  const blocked = await screen.findByRole('dialog', { name: 'Wuschel ist weiterhin geschützt' });
  expect(within(blocked).getByText('Die Ereignisschleife ist verzögert.')).toBeVisible();
  expect(within(blocked).getByText('Aktuell 625 ms, Schwelle 500 ms')).toBeVisible();
  expect(within(blocked).getByRole('link', { name: 'Zu Einstellungen → Betrieb' })).toHaveAttribute('href', '/settings/operations');
  expect(validations()).toHaveLength(1);
});

it('never submits the action again after cancelling a wait', async () => {
  const { validations, release } = mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Prüfen' }));
  const waiting = await screen.findByRole('dialog', { name: 'Wuschel ist kurz beschäftigt' });
  await user.click(within(waiting).getByRole('button', { name: 'Abbrechen' }));
  await act(async () => release(new Response(null, { status: 204 })));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Wuschel ist kurz beschäftigt' })).not.toBeInTheDocument());
  expect(await screen.findByText('Abgebrochen')).toBeVisible();
  expect(validations()).toHaveLength(1);
});
