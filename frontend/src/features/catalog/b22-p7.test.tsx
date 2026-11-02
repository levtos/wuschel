import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { t } from '@/i18n';
import { SchemaForm } from '@/editors/SchemaForm';
import type { JsonObject } from '@/lib/json';
import { asObject } from '@/features/authoring/model';
import { Stateful, wrap } from '../../../test/harness';
import { installFakeApi, reply } from '../../../test/fakeApi';
import CatalogPage from './CatalogPage';
import { CatalogMediaFacts } from './CatalogMediaFacts';

afterEach(() => vi.unstubAllGlobals());

it('edits source and app stability and parsing through the delivered schema without JSON', async () => {
  const generated = JSON.parse(readFileSync(resolve(process.cwd(), '../examples/apple-tv-templates-b22.json'), 'utf8'));
  const schema = generated.settings_schema as unknown as JsonObject;
  const initial = { stability_s: 2, live_stability_s: 60, default_mode: 'standalone', apps: [{ app_id: 'synthetic.app', app_name: 'Synthetic app', mode: 'episode_in_second', stability_s: 4, live_stability_s: 90 }] };
  let result: JsonObject = initial;
  wrap(<Stateful initial={initial} onValue={next => { result = next; }}>{(value, set) => <SchemaForm root={schema} node={schema} value={value} onChange={next => set(asObject(next))} pointer="/title_catalog" label={t('fields.title_catalog')} />}</Stateful>);
  const stability = screen.getAllByLabelText(t('fields.stability_s'));
  fireEvent.change(stability[0]!, { target: { value: '3' } });
  fireEvent.change(stability[1]!, { target: { value: '8' } });
  await userEvent.selectOptions(screen.getByLabelText(/Modus/), 'parent_in_second');
  expect(result).toMatchObject({ stability_s: 3, live_stability_s: 60, apps: [{ app_id: 'synthetic.app', mode: 'parent_in_second', stability_s: 8, live_stability_s: 90 }] });
  expect(screen.queryByRole('textbox', { name: /JSON/ })).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: t('codes.parent_in_second') })).toHaveValue('parent_in_second');
});

it('shows live television and sender as metadata without creating a sender hierarchy', () => {
  wrap(<CatalogMediaFacts metadata={{ app_id: 'synthetic.app', app_name: 'Synthetic app', live_tv: true, sender: 'Synthetic sender' }} />);
  expect(screen.getByText('App: Synthetic app')).toBeInTheDocument();
  expect(screen.getByText('Live-TV')).toBeInTheDocument();
  expect(screen.getByText('Sender: Synthetic sender')).toBeInTheDocument();
  expect(screen.queryByText('Master')).not.toBeInTheDocument();
});

it('sends app live and sender filters to the grouped catalog API and clears them on catalog change', async () => {
  const fake = installFakeApi();
  fake.routes.push({ match: /^catalogs$/, respond: () => reply(200, [
    { catalog_id: 'media.appletv_titles', version: 1, classification_schema: { values: ['normal', 'private'] } },
    { catalog_id: 'synthetic.other', version: 1, classification_schema: { values: ['normal'] } },
  ]) });
  fake.routes.push({ match: /^catalogs\/care\?/, respond: () => reply(200, { items: [], content_revision: 0, next_cursor: null, filter_values: { app: ['synthetic.app'], sender: ['Synthetic sender'] }, app_labels: { 'synthetic.app': 'Synthetic app' } }) });
  wrap(<CatalogPage />);
  const user = userEvent.setup();
  await screen.findByRole('option', { name: 'Synthetic app' });
  await user.selectOptions(screen.getByLabelText(t('b22.apple.app')), 'synthetic.app');
  await user.selectOptions(screen.getByLabelText(t('b22.apple.kind')), 'true');
  await user.selectOptions(screen.getByLabelText(t('b22.apple.sender')), 'Synthetic sender');
  await waitFor(() => {
    const query = new URLSearchParams(fake.calls.filter(call => call.path.startsWith('catalogs/care?')).at(-1)!.path.split('?')[1]);
    expect(query.get('app')).toBe('synthetic.app');
    expect(query.get('live_tv')).toBe('true');
    expect(query.get('sender')).toBe('Synthetic sender');
    expect(query.get('grouped')).toBe('true');
  });
  await user.selectOptions(screen.getByLabelText(t('alpha10.catalog.catalog')), 'synthetic.other@1');
  await waitFor(() => {
    const query = new URLSearchParams(fake.calls.filter(call => call.path.startsWith('catalogs/care?')).at(-1)!.path.split('?')[1]);
    expect(query.get('catalog_id')).toBe('synthetic.other');
    expect(query.get('app')).toBe('');
    expect(query.get('live_tv')).toBe('');
    expect(query.get('sender')).toBe('');
  });
});
