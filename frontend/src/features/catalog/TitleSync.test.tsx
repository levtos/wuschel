import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { TitleSync } from './TitleSync';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';
import { emptyRegistry } from '@/features/authoring/model';

afterEach(() => vi.unstubAllGlobals());
const mapping = { mappings: { '["synthetic",""]': { catalog_id: 'synthetic.music', definition_version: 1, scope: 'synthetic', profile: 'music' } }, standalone: [] };
const preview = { plan_id: 'synthetic-plan', plan_digest: 'synthetic-digest', at: '2032-01-01T00:00:00Z',
  counts: { unchanged: 1 }, can_apply: true, mapping, mapping_version: 1, changed_targets: 0,
  contexts: [{ key: '["synthetic",""]', context: 'synthetic', source_app: '' }], errors: [],
  affected_contracts: [], context_effects: [] };

it('reconciles again without reentering a saved mapping and applies only the approved plan', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, preview) });
  fake.routes.push({ match: /^title-sync\/apply$/, respond: () => reply(200, { changed: 0 }) });
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup();
  const button = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(button).toBeEnabled()); await user.click(button);
  const apply = await screen.findByRole('button', { name: 'Abgleich übernehmen' });
  await waitFor(() => expect(apply).toBeEnabled());
  expect(fake.calls.find(call => call.path === 'title-sync/preview')?.body).toEqual({ expected_active_revision: 4 });
  await user.click(apply);
  await screen.findByText('Der Titelkatalog entspricht diesem TC-Stand.');
  expect(fake.calls.find(call => call.path === 'title-sync/apply')?.body).toMatchObject({ plan_id: preview.plan_id, plan_digest: preview.plan_digest });
  await user.click(screen.getByRole('button', { name: 'Neu abgleichen' }));
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'title-sync/preview')).toHaveLength(2));
  expect(fake.calls.filter(call => call.path === 'title-sync/preview')[1]?.body).not.toHaveProperty('mapping');
});

it('blocks apply after deliberate mapping edits until a new preview binds the mapping version', async () => {
  const fake = installFakeApi({ revisions: [{ revision: 4, config: emptyRegistry(), checksum: 'synthetic', created_at: preview.at }] });
  fake.routes.push({ match: /^title-sync\/preview$/, respond: () => reply(200, preview) });
  wrap(<TitleSync catalogs={[{ catalog_id: 'synthetic.music', version: 1 }]} />);
  const user = userEvent.setup(); const button = screen.getByRole('button', { name: 'Mit Title Classifier abgleichen' });
  await waitFor(() => expect(button).toBeEnabled()); await user.click(button);
  const apply = await screen.findByRole('button', { name: 'Abgleich übernehmen' });
  await waitFor(() => expect(apply).toBeEnabled());
  await user.click(screen.getByText('Kontextabbildung pflegen'));
  const scope = screen.getByLabelText('Geltungsbereich');
  await user.clear(scope); await user.type(scope, 'synthetic-new');
  expect(apply).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Geänderte Abbildung prüfen' }));
  await waitFor(() => expect(fake.calls.filter(call => call.path === 'title-sync/preview')).toHaveLength(2));
  expect(fake.calls.filter(call => call.path === 'title-sync/preview')[1]?.body).toMatchObject({ expected_mapping_version: 1, mapping: { mappings: { '["synthetic",""]': { scope: 'synthetic-new' } } } });
});
