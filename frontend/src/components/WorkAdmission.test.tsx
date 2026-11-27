import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, it } from 'vitest';
import { ActionCancelled } from '@/lib/api';
import { validateConfig } from '@/lib/queries';
import { installFakeApi, reply, sampleConfig } from '../../test/fakeApi';
import { wrap } from '../../test/harness';

const jobId = '00000000-0000-4000-8000-000000000180';
const result = new RegExp(`^work/jobs/${jobId}/result$`);

function Validation() {
  const [outcome, setOutcome] = useState('');
  return <>
    <button onClick={() => {
      void validateConfig(sampleConfig(), true).then(() => setOutcome('Geprüft'), error => {
        setOutcome(error instanceof ActionCancelled ? 'Abgebrochen' : 'Fehlgeschlagen');
      });
    }}>Prüfen</button>
    <output>{outcome}</output>
  </>;
}

function mount() {
  const fake = installFakeApi();
  const path = `work/jobs/${jobId}`;
  fake.routes.push({ match: /^registry\/validate$/, respond: () => new Response(
    JSON.stringify({ id: jobId, phase: 'waiting' }),
    { status: 202, headers: { Location: path, 'Preference-Applied': 'respond-async' } },
  ) });
  wrap(<Validation />);
  return fake;
}

it('waits for the persisted result without a load dialog or a second validation request', async () => {
  const fake = mount();
  let polls = 0;
  fake.routes.push({ match: result, respond: () => ++polls < 2
    ? reply(202, { phase: 'waiting' }) : reply(200, { valid: true, issues: [] }) });
  await userEvent.setup().click(screen.getByRole('button', { name: 'Prüfen' }));
  await waitFor(() => expect(polls).toBe(1));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(await screen.findByText('Geprüft', undefined, { timeout: 3000 })).toBeVisible();
  expect(fake.calls.filter(call => call.path === 'registry/validate')).toHaveLength(1);
  expect(fake.calls.some(call => call.path.startsWith('operations/wait'))).toBe(false);
});

it('ends the request on server cancellation without sending the action again', async () => {
  const fake = mount();
  fake.routes.push({ match: result, respond: () => reply(409, { error: 'work_cancelled' }) });
  await userEvent.setup().click(screen.getByRole('button', { name: 'Prüfen' }));
  expect(await screen.findByText('Abgebrochen')).toBeVisible();
  expect(fake.calls.filter(call => call.path === 'registry/validate')).toHaveLength(1);
});
