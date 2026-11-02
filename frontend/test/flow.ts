import { screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { expect } from 'vitest';
import { clickReadyButton, gui } from './gui';
import { legacyTarget } from '../src/lib/navigation';

/** Application-level user flows, shared by the in-memory and the real-engine tests. */
export function flow(user: UserEvent) {
  const steps = gui(user);

  const go = async (hash: string) => {
    const [path = '/', query] = hash.slice(1).split('?');
    const target = '#' + legacyTarget(path, query ? '?' + query : '');
    window.location.hash = hash;
    await waitFor(() => expect(window.location.hash).toBe(target));
  };

  const createDraft = async (name: string, origin: 'Leere Konfiguration' | 'Einlesen (JSON)', json?: string) => {
    await user.click((await screen.findAllByRole('button', { name: /Neuer Entwurf|Ersten Entwurf anlegen/ }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: "Neuer Entwurf" });
    await steps.type(within(dialog).getByLabelText("Name (freiwillig)"), name);
    await user.click(within(dialog).getByRole('radio', { name: origin }));
    if (json) {
      await user.click(within(dialog).getByLabelText('oder JSON einfügen'));
      await user.paste(json);
    }
    await user.click(within(dialog).getByRole('button', { name: "Entwurf anlegen" }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: "Neuer Entwurf" })?.textContent ?? null).toBeNull(), { timeout: 65_000 });
  };

  const saveButton = async () => {
    const menu = screen.getByLabelText('Weitere Entwurfsaktionen');
    if (!menu.closest('details')?.open) await user.click(menu);
    const button = await screen.findByRole('button', { name: 'Speichern' }, { timeout: 65_000 });
    return button;
  };

  /** Builds the complete Alpha-4 example registry in the open (empty) draft, by operating controls only. */
  const buildExampleRegistry = async () => {
    // Dependencies, sources, bindings
    await go('#/sources?tab=dependencies');
    await user.click(await screen.findByRole('button', { name: "Verbindung hinzufügen" }));
    await steps.type(await screen.findByLabelText("Kennung der Verbindung"), 'dep.test');
    await go('#/sources/devices');
    for (const [id, key] of [['source.a', 'physical.a'], ['source.b', 'physical.b']] as const) {
      await user.click(await screen.findByRole('button', { name: "Quelle hinzufügen" }));
      await user.click(await screen.findByRole('button', { name: "Ohne Gerät anlegen" }));
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Gerät aus Home Assistant hinzufügen' })).not.toBeInTheDocument());
      await screen.findByLabelText('Name');
      const advanced = (await screen.findAllByRole('button', { name: 'Erweitert' }))[0]!;
      if (advanced.getAttribute('aria-expanded') !== 'true') await user.click(advanced);
      await waitFor(() => expect(screen.getByLabelText("Quellenkennung")).toHaveValue('source.neu'));
      await steps.type(await screen.findByLabelText("Quellenkennung"), id);
      await steps.type(await screen.findByLabelText("Kennung der Messquelle"), key);
    }
    await go('#/sources/bindings');
    for (const [id, source, entity] of [['binding.a', 'source.a', 'sensor.example_1'], ['binding.b', 'source.b', 'input_boolean.cc_alpha_test_b']] as const) {
      await user.click(await screen.findByRole('button', { name: "Verknüpfung hinzufügen" }));
      await waitFor(() => expect(screen.getByLabelText('Kennung der Verknüpfung')).toHaveValue('binding.neu'));
      await steps.type(await screen.findByLabelText("Kennung der Verknüpfung"), id);
      await user.selectOptions(screen.getByLabelText('Quelle'), source);
      await steps.type(await screen.findByLabelText(/^Gerät oder Wert in Home Assistant \*/), entity);
    }

    // Definitions
    const defs: [string, (base: string) => Promise<void>][] = [
      ['test.truth', async () => {}],
      ['test.either', steps.buildEither],
      ['test.condition', steps.buildCondition],
      ['test.timed', steps.buildTimed],
      ['test.flow', steps.buildFlow],
    ];
    for (const [index, [typeId, build]] of defs.entries()) {
      await go('#/definitions');
      await user.click(await screen.findByRole('button', { name: /Neue Vorlage/ }));
      const dialog = await screen.findByRole('dialog', { name: "Neue Vorlage" });
      await user.click(within(dialog).getByRole('button', { name: 'Erweitert' }));
      await steps.type(within(dialog).getByLabelText("Vorlagenkennung"), typeId);
      await user.click(within(dialog).getByRole('button', { name: 'Anlegen' }));
      await screen.findByRole('tab', { name: 'Allgemein' });
      await build(`/definitions/${index}`);
    }

    // Contract instances
    const contracts: [string, string, string[]][] = [
      ['fixture.left', 'test.truth@1', ['source.a']],
      ['fixture.right', 'test.truth@1', ['source.b']],
      ['fixture.either', 'test.either@1', ['fixture.left', 'fixture.right']],
      ['fixture.condition', 'test.condition@1', ['fixture.either']],
      ['fixture.timed', 'test.timed@1', ['fixture.condition']],
      ['fixture.flow', 'test.flow@1', ['fixture.timed']],
    ];
    for (const [id, type, refs] of contracts) {
      await go('#/contracts');
      await user.click(await screen.findByRole('button', { name: /Neuer Vertrag/ }));
      await user.click(await screen.findByRole('button', { name: 'Ohne Gerät anlegen' }));
      const dialog = await screen.findByRole('dialog', { name: "Neuer Vertrag" });
      await steps.type(within(dialog).getByLabelText("Vertragskennung"), id);
      await user.selectOptions(within(dialog).getByLabelText("Vorlage und Version"), type);
      const create = within(dialog).getByRole('button', { name: 'Anlegen' });
      await waitFor(() => expect(create).toBeEnabled(), { timeout: 65_000 });
      await user.click(create);
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Neuer Vertrag' })).not.toBeInTheDocument(), { timeout: 65_000 });
      await user.click(await screen.findByRole('tab', { name: 'Konfiguration' }));
      const selects = await screen.findAllByLabelText("Verknüpfter Wert");
      await waitFor(() => selects.forEach(select => expect(select).toBeEnabled()), { timeout: 65_000 });
      for (const [i, ref] of refs.entries()) await user.selectOptions(selects[i]!, ref);
    }
  };

  /** Activation through the real dialog, after the debounced server validation has accepted the draft. */
  const activateOpenDraft = async (draftId: string) => {
    await screen.findByText('Im Entwurf gespeichert', {}, { timeout: 65_000 });
    await go(`#/changes/drafts/${draftId}`);
    await screen.findByText('Hier kannst du deinen Entwurf prüfen und die Änderungen übernehmen.');
    await waitFor(() => {
      const control = screen.queryByRole('button', { name: 'Übernehmen' }) ?? screen.getByRole('button', { name: 'Änderungen prüfen' });
      expect(control).toBeEnabled();
    }, { timeout: 65_000 });
    const check = screen.queryByRole('button', { name: 'Änderungen prüfen' });
    if (check) await user.click(check);
    const button = await screen.findByRole('button', { name: 'Übernehmen' }, { timeout: 65_000 });
    expect(button.closest('[data-page-header]')).not.toBeNull();
    await clickReadyButton(user, button, 'Übernehmen');
    const dialog = await screen.findByRole('dialog', { name: "Konfiguration übernehmen" }, { timeout: 65_000 });
    await user.click(await within(dialog).findByRole('checkbox', { name: /Auswirkungen geprüft/ }, { timeout: 65_000 }));
    await user.click(within(dialog).getByRole('button', { name: "Jetzt übernehmen" }));
    return dialog;
  };

  return { ...steps, go, createDraft, saveButton, buildExampleRegistry, activateOpenDraft };
}
