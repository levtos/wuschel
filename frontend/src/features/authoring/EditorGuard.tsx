import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useBlocker } from 'react-router';
import { ConfirmDialog } from '@/components/ui/dialog';
import { t } from '@/i18n';

interface PendingEdit { discard: () => void; busy: boolean }
interface Registry { editors: Map<symbol, PendingEdit>; changed: () => void }
const GuardContext = createContext<Registry | null>(null);

export function EditorNavigationGuard({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState(false);
  const [registry] = useState<Registry>(() => {
    const editors = new Map<symbol, PendingEdit>();
    return { editors, changed: () => setPending(editors.size > 0) };
  });
  return <GuardContext.Provider value={registry}>
    {children}
    {pending && <NavigationBlocker registry={registry} />}
  </GuardContext.Provider>;
}

function NavigationBlocker({ registry }: { registry: Registry }) {
  const { editors } = registry;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => editors.size > 0 && (
    currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search || currentLocation.hash !== nextLocation.hash
  ));
  const discard = () => {
    if ([...editors.values()].some(edit => edit.busy)) return;
    const pending = [...editors.values()];
    editors.clear();
    for (const edit of pending) edit.discard();
    blocker.proceed?.();
    registry.changed();
  };
  return <DiscardChanges open={blocker.state === 'blocked'} onCancel={() => blocker.reset?.()} onDiscard={discard} />;
}

export function useEditorGuard(dirty: boolean, onClose: () => void, busy = false, onNavigateAway = onClose) {
  const registry = useContext(GuardContext);
  const editor = useRef(Symbol());
  const [confirming, setConfirming] = useState(false);
  const afterClose = useRef<(() => void) | undefined>(undefined);
  const current = useRef({ onNavigateAway, busy });
  current.current = { onNavigateAway, busy };
  useEffect(() => {
    if (!dirty) return;
    const id = editor.current;
    registry?.editors.set(id, { discard: () => current.current.onNavigateAway(), get busy() { return current.current.busy; } });
    registry?.changed();
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload);
    return () => { registry?.editors.delete(id); registry?.changed(); window.removeEventListener('beforeunload', beforeUnload); };
  }, [dirty, registry]);

  const finish = () => {
    registry?.editors.delete(editor.current);
    registry?.changed();
    setConfirming(false);
    const next = afterClose.current;
    afterClose.current = undefined;
    if (next) next();
    else onClose();
  };
  const requestClose = (next?: () => void) => {
    if (busy) return;
    afterClose.current = next;
    if (dirty) setConfirming(true);
    else finish();
  };

  return { finish, requestClose, confirmation: <DiscardChanges open={confirming} onCancel={() => { afterClose.current = undefined; setConfirming(false); }} onDiscard={finish} /> };
}

function DiscardChanges({ open, onCancel, onDiscard }: { open: boolean; onCancel: () => void; onDiscard: () => void }) {
  return <ConfirmDialog open={open} onOpenChange={shown => !shown && onCancel()} title={t('a2.edit.discardTitle')} description={t('a2.edit.discardHelp')} confirmLabel={t('a2.edit.discard')} tone="danger" onConfirm={onDiscard} />;
}
