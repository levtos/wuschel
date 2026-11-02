import { screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CatalogPage from './CatalogPage';
import { titleLabel } from './titleLabel';
import { wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';

afterEach(() => vi.unstubAllGlobals());

it('uses scene display metadata without turning its identifier into a title key', () => {
  const row = { key: 'synthetic-scene-42', display_name: 'Synthetic old label', display_metadata: { title: 'Synthetic current label', artist: 'Synthetic person' } };
  expect(titleLabel(row)).toBe('Synthetic current label');
  expect(row.key).toBe('synthetic-scene-42');
  expect(titleLabel({ key: 'studio:synthetic', display_name: 'Synthetic studio' })).toBe('Synthetic studio');
});

it('shows performers as display metadata while preserving the scene identity in actions', async () => {
  const fake = installFakeApi();
  const row = { identity: 'synthetic-hash', key: 'synthetic-scene-42', display_name: '', display_metadata: { title: 'Synthetic current label', artist: 'Synthetic person' }, scope: 'stash', media_type: 'video', signal_type: 'scene', classification: null, master_key: null, note: '', complete: false, effective_classification: 'private', semantic_digest: 'synthetic' };
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [{ catalog_id: 'media.stash', version: 2, classification_schema: { values: ['normal', 'private'] } }]) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [row], content_revision: 1, next_cursor: null }) });
  wrap(<CatalogPage />);
  await screen.findByText('Synthetic current label');
  expect(screen.getByText('Darsteller: Synthetic person')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Titel auswählen: Synthetic current label' })).toBeInTheDocument();
  expect(document.body.textContent).not.toContain('synthetic-scene-42');
});
