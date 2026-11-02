import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { t } from '@/i18n';
import { asArray, asObject } from './model';
import { WorkingDocumentProvider, useWorkingDocument } from './WorkingDocument';
import { removeUnsupportedOrigins } from './UnsupportedOrigins';
import { installFakeApi, makeDraft, sampleConfig } from '../../../test/fakeApi';
import { wrap } from '../../../test/harness';

const legacy = () => sampleConfig({
  sources: [
    { source_id: 'source.synthetic.connection', value_origin: { kind: 'measured', provider: 'local' } },
    { source_id: 'source.synthetic.sensor', value_origin: { kind: 'measured', provider: 'local' } },
    { source_id: 'source.synthetic.clock', value_origin: { kind: 'measured', provider: 'local' } },
  ],
  bindings: [
    { source_id: 'source.synthetic.connection', adapter: { kind: 'connectivity' } },
    { source_id: 'source.synthetic.sensor', adapter: { kind: 'ha_state' } },
    { source_id: 'source.synthetic.clock', adapter: { kind: 'scheduler' } },
  ],
});

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

it('offers explicit correction on opening an invalid draft and saves only the corrected draft', async () => {
  const user = userEvent.setup();
  const original = legacy();
  const draft = makeDraft('synthetic', original);
  const fake = installFakeApi({ drafts: [draft] });
  fake.issuesFor = config => asObject(asArray(config.sources)[0]).value_origin ? [{ code: 'adapter_value_origin_unsupported', path: '/bindings/0/source_id', object_id: 'source.synthetic.connection', message: 'Synthetic origin error' }] : [];
  function Probe() {
    const doc = useWorkingDocument();
    return <><button onClick={() => doc.open(draft)}>Entwurf öffnen</button><output>{doc.dirty ? 'ungespeichert' : 'gespeichert'}</output></>;
  }
  wrap(<WorkingDocumentProvider><Probe /></WorkingDocumentProvider>);
  await user.click(screen.getByRole('button', { name: 'Entwurf öffnen' }));
  expect(await screen.findByRole('button', { name: t('b22.origins.remove') })).toBeEnabled();
  expect(screen.getByText(t('b22.origins.draftOnly'))).toBeInTheDocument();
  expect(fake.calls.some(call => call.method === 'PUT')).toBe(false);
  await user.click(screen.getByRole('button', { name: t('b22.origins.remove') }));
  await waitFor(() => expect(fake.calls.filter(call => call.method === 'PUT')).toHaveLength(1));
  await waitFor(() => expect(screen.queryByRole('button', { name: t('b22.origins.remove') })).not.toBeInTheDocument());
  const saved = fake.drafts.get('synthetic')!;
  const sources = asArray(saved.config.sources).map(asObject);
  expect(sources[0]).not.toHaveProperty('value_origin');
  expect(sources[2]).not.toHaveProperty('value_origin');
  expect(sources[1]?.value_origin).toEqual({ kind: 'measured', provider: 'local' });
  expect(original).toEqual(legacy());
  expect(fake.revisions).toHaveLength(0);
  expect(fake.calls.some(call => call.path.endsWith('/activate'))).toBe(false);
});

it('offers correction from the active configuration without silently editing it', async () => {
  const config = legacy();
  const original = structuredClone(config);
  const fake = installFakeApi({ revisions: [{ revision: 1, config, checksum: 'synthetic', created_at: '2026-01-01T00:00:00Z' }] });
  wrap(<WorkingDocumentProvider><p>Aktualität</p></WorkingDocumentProvider>);
  const correction = await screen.findByRole('button', { name: t('b22.origins.remove') });
  expect(fake.drafts.size).toBe(0);
  await userEvent.click(correction);
  await waitFor(() => expect(fake.drafts.size).toBe(1));
  await waitFor(() => expect(fake.calls.filter(call => call.method === 'PUT')).toHaveLength(1));
  expect(fake.revisions).toHaveLength(1);
  expect(fake.revisions[0]?.config).toEqual(original);
  expect(asObject(asArray([...fake.drafts.values()][0]!.config.sources)[0])).not.toHaveProperty('value_origin');
});

it('preserves supported origins and leaves the input document untouched', () => {
  const config = legacy();
  expect(removeUnsupportedOrigins(config)).toEqual({ ...config, sources: [
    { source_id: 'source.synthetic.connection' },
    asArray(config.sources)[1],
    { source_id: 'source.synthetic.clock' },
  ] });
  expect(config).toEqual(legacy());
});
