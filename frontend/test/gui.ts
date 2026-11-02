import { screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { expect } from 'vitest';

/** Keep the element found before an asynchronous label change. Presence alone
 * does not acknowledge readiness: validation can temporarily disable/rename it.
 * Only assertions are retried; the click is sent exactly once. */
export async function clickReadyButton(user: UserEvent, button: HTMLElement, name: string | RegExp) {
  await waitFor(() => {
    expect(button).toBeInTheDocument();
    expect(button).toHaveAccessibleName(name);
    expect(button).toBeEnabled();
  });
  await user.click(button);
}

/**
 * User-interaction building blocks shared by the manual-first tests. Everything
 * here operates controls the way a person does (select, type, click); nothing
 * edits the document, pointers or JSON directly.
 */
export const node = (pointer: string): HTMLElement => {
  const el = document.querySelector(`[data-pointer="${pointer}"]`);
  if (!el) throw new Error(`Knoten ${pointer} nicht gefunden`);
  return el as HTMLElement;
};

export function gui(user: UserEvent) {
  const setOp = async (pointer: string, op: string) => {
    await user.selectOptions(within(node(pointer)).getAllByRole('combobox', { name: /^Rechenschritt/ })[0]!, op);
  };
  const first = (pointer: string, label: string | RegExp) => within(node(pointer)).getAllByLabelText(label)[0]!;
  const type = async (el: HTMLElement, text: string) => {
    await waitFor(() => expect(el).toBeEnabled());
    await user.clear(el);
    await user.type(el, text);
  };
  const tab = (name: RegExp | string) => user.click(screen.getByRole('tab', { name }));

  /** test.condition: AND(a, NOT(false), compare(sum(1, 2) > 2)). */
  const buildCondition = async (base: string) => {
    await tab(/Ausgaben/);
    const root = `${base}/outputs/value`;
    await setOp(root, 'and');
    await user.click(within(node(root)).getAllByRole('button', { name: "Eingangswert" })[0]!);
    await setOp(`${root}/args/1`, 'not');
    await setOp(`${root}/args/1/args/0`, 'literal');
    await setOp(`${root}/args/2`, 'compare');
    await user.selectOptions(first(`${root}/args/2`, 'Vergleich'), 'gt');
    const formula = `${root}/args/2/left`;
    await setOp(formula, 'formula');
    await type(first(`${formula}/args/0`, 'Wert'), '1');
    await user.click(within(node(formula)).getAllByRole('button', { name: 'Eingangswert' })[0]!);
    await setOp(`${formula}/args/1`, 'literal');
    await user.click(within(node(`${formula}/args/1`)).getByRole('button', { name: 'Technische Details: Werteart' }));
    await user.selectOptions(first(`${formula}/args/1`, 'Typ'), 'number');
    await type(first(`${formula}/args/1`, 'Wert'), '2');
    await type(first(`${root}/args/2/right`, 'Wert'), '2');
  };

  /** test.either: a second input and a fusion with an explicit strategy. */
  const buildEither = async (base: string) => {
    await tab(/Eingänge/);
    await user.click(screen.getByRole('button', { name: 'Eingang' }));
    await type(screen.getAllByLabelText('Name')[1]!, 'b');
    await user.tab();
    await tab(/Ausgaben/);
    const root = `${base}/outputs/value`;
    await setOp(root, 'fusion');
    await user.selectOptions(first(root, 'Strategie'), 'any_true');
    await user.selectOptions(within(node(`${root}/args/1`)).getByLabelText('Eingang'), 'b');
  };

  /** test.timed: named rules (Grace, stable_for) referenced by the output. */
  const buildTimed = async (base: string) => {
    await tab(/Benannte Regeln/);
    await user.click(screen.getByRole('button', { name: 'Regel' }));
    await type(screen.getAllByLabelText('Name')[0]!, 'grace');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Regel' }));
    await type(screen.getAllByLabelText('Name')[1]!, 'stable');
    await user.tab();
    const grace = `${base}/rules/grace`;
    await setOp(grace, 'temporal');
    await type(first(grace, "Eindeutige Kennung"), 'grace');
    await user.selectOptions(first(grace, 'Operation'), 'grace');
    await type(first(grace, 'Dauer (s)'), '10');
    const stable = `${base}/rules/stable`;
    await setOp(stable, 'temporal');
    await type(first(stable, "Eindeutige Kennung"), 'stable');
    await type(first(stable, 'Dauer (s)'), '2');
    await user.click(within(node(stable)).getByRole('checkbox', { name: /Werte innerhalb ihrer Haltefrist/ }));
    await setOp(`${stable}/args/0`, 'ref');
    await user.selectOptions(within(node(`${stable}/args/0`)).getByLabelText("Benannter Rechenschritt"), 'grace');
    await tab(/Ausgaben/);
    const root = `${base}/outputs/value`;
    await setOp(root, 'ref');
    await user.selectOptions(within(node(root)).getByLabelText("Benannter Rechenschritt"), 'stable');
  };

  /** test.flow: six typed output fields (object value built graphically) and a state machine. */
  const buildFlow = async (base: string) => {
    await tab(/Ausgaben/);
    await user.selectOptions(first(`${base}/schema/fields/value`, 'Typ'), 'enum');
    const catalog = screen.getByLabelText(/Werteliste/);
    for (const entry of ['idle', 'active', 'done']) {
      await type(catalog, entry);
      await user.click(screen.getByRole('button', { name: 'Hinzufügen' }));
    }
    await setOp(`${base}/outputs/value`, 'machine');

    const addField = async (name: string, fieldType: string, rule: () => Promise<void>) => {
      await user.click(screen.getByRole('button', { name: 'Ausgabefeld' }));
      await type(screen.getAllByLabelText('Feldname').at(-1)!, name);
      await user.tab();
      if (fieldType !== 'bool') await user.selectOptions(first(`${base}/schema/fields/${name}`, 'Typ'), fieldType);
      await rule();
    };
    const literal = async (name: string, fieldType: string, value: string) => {
      const path = `${base}/outputs/${name}`;
      await setOp(path, 'literal');
      await user.click(within(node(path)).getByRole('button', { name: 'Technische Details: Werteart' }));
      await user.selectOptions(first(path, 'Typ'), fieldType);
      await type(first(path, 'Wert'), value);
    };
    await addField('truth', 'bool', async () => {});
    await addField('count', 'number', () => literal('count', 'number', '2'));
    await addField('label', 'text', () => literal('label', 'text', 'synthetic'));
    await addField('stamp', 'timestamp', () => literal('stamp', 'timestamp', '2026-01-01T12:00:00+00:00'));
    await addField('metadata', 'object', async () => {
      const path = `${base}/outputs/metadata`;
      await setOp(path, 'literal');
      await user.click(within(node(path)).getByRole('button', { name: 'Technische Details: Werteart' }));
      await user.selectOptions(first(path, 'Typ'), 'object');
      await user.click(screen.getByRole('button', { name: 'Wert: Eigenschaft hinzufügen' }));
      await type(screen.getByLabelText('Wert: Schlüssel 1'), 'fixture');
      await user.tab();
      await user.selectOptions(screen.getByLabelText('Wert.fixture: Typ'), 'boolean');
      await user.selectOptions(screen.getByLabelText('Wert.fixture'), 'true');
    });

    await tab(/Ablauf/);
    await user.click(screen.getByRole('button', { name: "Ablauf hinzufügen" }));
    const m = `${base}/machine`;
    await type(screen.getByLabelText("Kennung des Ablaufs"), 'lifecycle');
    await user.click(screen.getByRole('button', { name: 'Zustand' }));
    await type(screen.getByLabelText('Zustand zustand'), 'done');
    await user.tab();
    await user.click(screen.getByRole('checkbox', { name: /Sitzungen/ }));
    await user.click(screen.getByRole('checkbox', { name: /Frist verwenden/ }));
    await type(screen.getByLabelText(/Frist \(s\)/), '10');
    await setOp(`${m}/reinitialize_when`, 'input');
    await user.click(screen.getByRole('button', { name: 'Befehl' }));
    await type(screen.getByLabelText('Befehlsname'), 'start');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Argument' }));
    await type(screen.getAllByLabelText('Argument').find((el) => (el as HTMLInputElement).value === 'arg')!, 'confirm');
    await user.tab();
    await user.click(screen.getByRole('button', { name: "Übergang" }));
    await user.selectOptions(screen.getAllByLabelText('Nach')[0]!, 'active');
    await user.selectOptions(screen.getAllByLabelText('Ereignis')[0]!, 'start');
    await type(screen.getAllByLabelText('Grund')[0]!, 'requested');
    await user.click(screen.getByRole('checkbox', { name: /confirm =/ }));
    await setOp(`${m}/transitions/0/guard`, 'input');
    await user.click(screen.getByRole('button', { name: "Übergang" }));
    await user.selectOptions(screen.getAllByLabelText('Von')[1]!, 'active');
    await user.selectOptions(screen.getAllByLabelText('Nach')[1]!, 'done');
    await user.selectOptions(screen.getAllByLabelText('Ereignis')[1]!, 'deadline');
    await type(screen.getAllByLabelText('Grund')[1]!, 'elapsed');
    await user.click(screen.getAllByRole('checkbox', { name: /Durchlauf beenden/ })[1]!);
  };

  return { setOp, first, type, tab, buildCondition, buildEither, buildTimed, buildFlow };
}
