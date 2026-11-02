import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { matchRoutes } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { Workbench, workbenchRoutes } from '@/Workbench';
import { NAVIGATION, legacyTarget } from '@/lib/navigation';
import { emptyRegistry } from '@/features/authoring/model';
import { installFakeApi, makeDraft, reply } from '../../test/fakeApi';
import { idleLive } from '../../test/harness';

function app(hash: string) {
  window.location.hash = hash;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Workbench onSetup={() => {}} live={idleLive()} /></QueryClientProvider>);
}
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

describe('P1: separate module routes and retained links', () => {
  it('assigns every sidebar subpage an explicit route rather than the fallback', () => {
    const routes = workbenchRoutes(() => {});
    const pages = NAVIGATION.flatMap(module => module.children);
    expect(new Set(pages.map(page => page.to)).size).toBe(pages.length);
    for (const page of pages) {
      expect(legacyTarget(page.to), page.to).toBe(page.to);
      const matched = matchRoutes(routes, page.to);
      expect(matched?.at(-1)?.route.path, page.to).not.toBe('*');
      expect(matched?.at(-1)?.route.path, page.to).toBeDefined();
    }
  });
  it.each([
    ['/contracts', '', '/contracts/list/all'],
    ['/contracts/import/json', '?source=backup', '/import-export?source=backup'],
    ['/changes/transfer', '?draft=example', '/import-export?draft=example'],
    ['/contracts/select/contract', '?filter=healthy', '/contracts/list/all?filter=healthy'],
    ['/contracts/mine', '', '/contracts/view/mine'],
    ['/contracts/detail', '', '/contracts/view/detail'],
    ['/contracts/room.temperature', '?focus=%2Fcontracts%2F0', '/contracts/view/room.temperature?focus=%2Fcontracts%2F0'],
    ['/definitions', '', '/templates/own'],
    ['/definitions/test.echo/1', '', '/templates/details/test.echo/1'],
    ['/sources', '?tab=bindings&item=2', '/sources/bindings?tab=bindings&item=2'],
    ['/registry', '?tab=revisions', '/changes/versions?tab=revisions'],
    ['/registry/drafts/abc', '', '/changes/drafts/abc'],
    ['/graph', '', '/contracts/relations/graph'],
    ['/diagnostics', '?tab=evidence', '/settings/diagnostics?tab=evidence'],
    ['/admin', '', '/settings/system'],
  ])('redirects %s and preserves the query', (path, query, target) => expect(legacyTarget(path, query)).toBe(target));
  it('opens modules and separates own from built-in templates without the old cross-module tabs', async () => {
    installFakeApi(); app('#/templates/own');
    await screen.findByRole('heading', { name: 'Eigene Vorlagen', level: 1 });
    const navigation = screen.getByRole('navigation', { name: 'Hauptnavigation' });
    const module = within(navigation).getByRole('button', { name: 'Verträge' });
    await waitFor(() => expect(module).toHaveAttribute('aria-expanded', 'true'));
    await userEvent.click(within(navigation).getByRole('link', { name: 'Eingebaute Vorlagen' }));
    await screen.findByRole('heading', { name: 'Eingebaute Vorlagen', level: 1 });
    expect(screen.queryByRole('heading', { name: 'Eigene Vorlagen', level: 1 })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Einrichtung prüfen' })).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: /Verträge und Vorlagen/ })).not.toBeInTheDocument();
  });
  it('redirects a source selection to the separate binding page', async () => {
    installFakeApi(); app('#/sources?tab=bindings&item=0');
    await screen.findByRole('heading', { name: 'Verknüpfungen', level: 1 });
    expect(window.location.hash).toBe('#/sources/bindings?tab=bindings&item=0');
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });
  it.each(['#/contracts/select/contract', '#/contracts/view/wohnzimmer'])('keeps the contract list selected for %s without a duplicate chooser entry', async hash => {
    installFakeApi(); app(hash);
    const navigation = screen.getByRole('navigation', { name: 'Hauptnavigation' });
    const mine = await within(navigation).findByRole('link', { name: 'Meine Verträge' });
    await waitFor(() => expect(mine).toHaveAttribute('aria-current', 'page'));
    expect(within(navigation).queryByRole('link', { name: 'Vertrag' })).not.toBeInTheDocument();
    if (hash.includes('select')) await waitFor(() => expect(window.location.hash).toBe('#/contracts/list/all'));
  });
  it('previews a file before staging its own draft, preserving the active configuration', async () => {
    const user = userEvent.setup();
    const fake = installFakeApi();
    fake.routes.push({ match: /^portable\/preview$/, method: 'POST', respond: () => reply(200, { config: emptyRegistry(), plan_digest: 'synthetic', added: 0, reused: 0, conflicts: [], unresolved: [], editorial_versions: {}, package: { config: emptyRegistry(), editorial: [] } }) });
    fake.routes.push({ match: /^portable\/stage$/, method: 'POST', respond: () => { const draft = makeDraft('imported', emptyRegistry()); fake.drafts.set(draft.draft_id, draft); return reply(200, draft); } });
    app('#/contracts/import/json?source=backup');
    await screen.findByRole('heading', { name: 'Import & Export', level: 1 });
    expect(window.location.hash).toBe('#/import-export?source=backup');
    const text = JSON.stringify(emptyRegistry());
    const file = new File([text], 'vertraege.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: async () => text });
    await user.upload(screen.getByLabelText('Datei auswählen'), file);
    await user.click(screen.getByRole('button', { name: 'Datei prüfen und Vorschau anzeigen' }));
    await screen.findByText('0 neue Objekte · 0 unverändert · 0 Konflikte');
    expect(fake.drafts.size).toBe(0);
    expect(fake.calls.find(call => call.path === 'portable/preview')?.body).toMatchObject({ package_text: text });
    await user.click(screen.getByRole('button', { name: 'Als Entwurf einlesen' }));
    await waitFor(() => expect(window.location.hash).toBe('#/changes/drafts/imported'));
    expect(fake.drafts.size).toBe(1);
    expect(fake.revisions).toHaveLength(0);
  });
});
