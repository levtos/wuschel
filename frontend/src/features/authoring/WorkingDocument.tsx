import { currentDraft } from '@/features/registry/authoring';
import { t as uiText } from '@/i18n';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, describeError, type Issue } from '@/lib/api';
import { clone, deepEqual, getAt, setAt, type Json } from '@/lib/json';
import { useActiveRevision, useDraftMutations, validateConfig } from '@/lib/queries';
import { IssuesProvider } from './issues';
import type { Draft, Registry, ValidationResult } from '@/lib/types';
import { emptyRegistry } from './model';
import { defaultDraftName } from './draftName';
import { Dialog } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Link } from 'react-router';
import type { EditorialEntry } from '@/features/editorial/model';
import { UnsupportedOrigins, removeUnsupportedOrigins } from './UnsupportedOrigins';

/**
 * The single editable registry document ("Arbeits-Draft").
 *
 * - Edits are local, immutable updates with undo/redo; nothing is live.
 * - Saving writes the draft with optimistic concurrency (draft_version).
 *   A conflict never overwrites silently; the user chooses explicitly.
 * - Validation is always the server's: unsaved validation of the edited
 *   document, debounced; issues are addressed by JSON Pointer.
 */

export type ValidationState = 'idle' | 'checking' | 'done' | 'failed';

export interface ValidationView {
  state: ValidationState;
  result: ValidationResult | null;
  issues: Issue[];
  /** The edited document changed after this validation ran. */
  stale: boolean;
  error: string | null;
}

export interface Conflict {
  server: Draft;
  comparison?: { conflicts: { path: string; base: unknown; draft: unknown; current: unknown; digests: string[] }[] };
}

export interface WorkingDocument {
  editing: boolean;
  starting: boolean;
  setEditing: (editing: boolean) => void;
  ensureDraft: () => Promise<Registry>;
  prepareActivation: () => Promise<Draft>;
  draft: Draft | null;
  config: Registry | null;
  displayName: string;
  setDisplayName: (name: string) => void;
  dirty: boolean;
  canUndo: boolean;
  canRedo: boolean;
  saving: boolean;
  conflict: Conflict | null;
  validation: ValidationView;
  autoValidate: boolean;
  setAutoValidate: (value: boolean) => void;
  /** Replaces the working document. Re-opening the draft that is already open is a no-op unless `reload`. */
  open: (draft: Draft, options?: { reload?: boolean }) => void;
  /** Opens a draft, asking before unsaved changes of another draft would be discarded. */
  requestOpen: (draft: Draft) => 'opened' | 'noop' | 'pending';
  switchRequest: Draft | null;
  confirmSwitch: () => void;
  cancelSwitch: () => void;
  close: () => void;
  edit: (updater: (config: Registry) => Registry) => void;
  editAndSave: (updater: (config: Registry) => Registry, editorial?: EditorialEntry[]) => Promise<Draft>;
  setAt: (pointer: string, value: Json | undefined) => void;
  replaceAll: (config: Registry) => void;
  undo: () => void;
  redo: () => void;
  save: () => Promise<'saved' | 'conflict' | 'error'>;
  saveError: string | null;
  validateNow: () => Promise<void>;
  resolveConflict: (choice: 'take-server' | 'merge' | 'cancel', decisions?: Record<string, { digests: string[]; choice: string }>) => Promise<string | void>;
}

const Context = createContext<WorkingDocument | null>(null);
const STORAGE_KEY = 'terra.workingDraft';
const HISTORY_LIMIT = 80;

function storedDraftId(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
function rememberDraftId(id: string | null): void {
  try {
    if (id) sessionStorage.setItem(STORAGE_KEY, id);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* optional convenience only; never holds credentials or document content */
  }
}

interface Snapshot {
  draft: Draft | null;
  config: Registry | null;
  name: string;
  past: Registry[];
  future: Registry[];
}

const IDLE: ValidationView = { state: 'idle', result: null, issues: [], stale: false, error: null };

export function WorkingDocumentProvider({ children, validateDebounceMs = 2000 }: { children: ReactNode; validateDebounceMs?: number }) {
  const [candidates, setCandidates] = useState<Draft[] | null>(null);
  const [selectedDraft, setSelectedDraft] = useState<string | null>(null);
  const choose = useRef<((draft: Draft | null | undefined) => void) | null>(null);
  const { create, update } = useDraftMutations();
  const active = useActiveRevision();
  const activeRevision = active.data === undefined ? undefined : active.data?.revision ?? 0;
  const validatedRevision = useRef<number | undefined>(undefined);
  const [snap, setSnap] = useState<Snapshot>({ draft: null, config: null, name: '', past: [], future: [] });
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [starting, setStarting] = useState(false);
  const startingRef = useRef<Promise<Registry> | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [validation, setValidation] = useState<ValidationView>(IDLE);
  const [autoValidate, setAutoValidate] = useState(true);
  const autoValidateRef = useRef(autoValidate);
  autoValidateRef.current = autoValidate;
  const validatedFor = useRef<Registry | null>(null);
  const runId = useRef(0);
  const documentId = useRef(0);
  const savingRef = useRef(false);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const latest = useRef(snap);
  latest.current = snap;

  const dirty = snap.draft !== null && snap.config !== null && (!deepEqual(snap.config, snap.draft.config) || snap.name !== (snap.draft.display_name ?? ''));

  const open = useCallback((draft: Draft, options?: { reload?: boolean }) => {
    // Selecting the draft that is already open must never drop its local edits or undo history.
    if (latest.current.draft?.draft_id === draft.draft_id && !options?.reload) { setEditing(true); return; }
    documentId.current += 1;
    clearTimeout(retryTimer.current);
    rememberDraftId((draft.lifecycle ?? 'open') === 'open' ? draft.draft_id : null);
    validatedFor.current = null;
    runId.current += 1;
    setValidation(IDLE);
    setSaveError(null);
    setConflict(null);
    setEditing((draft.lifecycle ?? 'open') === 'open');
    const opened = { draft, config: clone(draft.config), name: draft.display_name ?? '', past: [], future: [] };
    latest.current = opened;
    setSnap(opened);
  }, []);

  /** Creating a draft is shared by all pages and never navigates or activates. */
  const ensureDraft = useCallback((): Promise<Registry> => {
    if (startingRef.current) return startingRef.current;
    const owner = ++documentId.current;
    setStarting(true);
    const request = (async () => {
      const head = await api<import('@/lib/types').RevisionRow | null>('registry/active');
      const current = latest.current;
      if (current.draft && current.config) {
        const saved = await api<Draft>(`registry/drafts/${current.draft.draft_id}`);
        if (currentDraft(saved, head?.revision ?? 0)) {
          if (owner !== documentId.current || latest.current.draft?.draft_id !== current.draft.draft_id || !latest.current.config) throw new Error(uiText('alpha7.draft.changedDuringAction'));
          setEditing(true); return clone(latest.current.config);
        }
      }
      if (current.draft && current.config && (!deepEqual(current.config, current.draft.config) || current.name !== (current.draft.display_name ?? ''))) {
        throw new Error(uiText('alpha7.draft.saveBeforeReview'));
      }
      const all = await api<Draft[]>('registry/drafts');
      const available = (Array.isArray(all) ? all : []).filter(d => currentDraft(d, head?.revision ?? 0));
      if (available.length || current.draft) {
        const selected = await new Promise<Draft | null | undefined>(resolve => { choose.current = resolve; setSelectedDraft(null); setCandidates(available); });
        if (selected === undefined) throw new Error(uiText('alpha7.draft.changedDuringAction'));
        if (owner !== documentId.current) throw new Error(uiText('alpha7.draft.changedDuringAction'));
        if (selected) { open(selected); return clone(selected.config); }
      }
      const config = head?.config ?? emptyRegistry();
      if (owner !== documentId.current) throw new Error(uiText('alpha7.draft.changedDuringAction'));
      const created = await create.mutateAsync({ config, from_active: true, display_name: defaultDraftName() });
      if (owner !== documentId.current) throw new Error(uiText('alpha7.draft.changedDuringAction'));
      open(created);
      return clone(created.config);
    })();
    const pending = request.finally(() => { if (startingRef.current === pending) { startingRef.current = null; setStarting(false); } });
    startingRef.current = pending;
    return pending;
  }, [create, open]);

  const [switchRequest, setSwitchRequest] = useState<Draft | null>(null);
  const requestOpen = useCallback(
    (draft: Draft) => {
      const current = latest.current;
      if (current.draft?.draft_id === draft.draft_id) { setEditing(true); return 'noop' as const; }
      const isDirty = current.draft !== null && current.config !== null && (!deepEqual(current.config, current.draft.config) || current.name !== (current.draft.display_name ?? ''));
      if (isDirty) {
        setSwitchRequest(draft);
        return 'pending' as const;
      }
      open(draft);
      return 'opened' as const;
    },
    [open],
  );
  const confirmSwitch = useCallback(() => {
    setSwitchRequest((target) => {
      if (target) open(target);
      return null;
    });
  }, [open]);
  const cancelSwitch = useCallback(() => setSwitchRequest(null), []);

  const close = useCallback(() => {
    documentId.current += 1;
    rememberDraftId(null);
    validatedFor.current = null;
    runId.current += 1;
    clearTimeout(retryTimer.current);
    setValidation(IDLE);
    setConflict(null);
    setSwitchRequest(null);
    setEditing(false);
    setSnap({ draft: null, config: null, name: '', past: [], future: [] });
  }, []);

  // Restore the previously opened draft after a reload (id only; content is re-read from the server).
  useEffect(() => {
    const id = storedDraftId();
    if (!id) return;
    const owner = documentId.current;
    api<Draft>(`registry/drafts/${encodeURIComponent(id)}`)
      .then((draft) => {
        if (owner === documentId.current && latest.current.draft === null) open(draft);
      })
      .catch(() => { if (owner === documentId.current) rememberDraftId(null); });
  }, [open]);

  const edit = useCallback((updater: (config: Registry) => Registry) => {
    setSnap((current) => {
      if (!current.config || !current.draft || !currentDraft(current.draft, activeRevision)) return current;
      const changed = updater(current.config);
      const next = current.config.config_schema_version === 4 ? upgradeFreshness(changed) : changed;
      if (next === current.config || deepEqual(next, current.config)) return current;
      return { ...current, config: next, past: [...current.past, current.config].slice(-HISTORY_LIMIT), future: [] };
    });
  }, [activeRevision]);

  const setDisplayName = useCallback((name: string) => setSnap((c) => (c.draft ? { ...c, name } : c)), []);
  const setAtPointer = useCallback((pointer: string, value: Json | undefined) => edit((config) => setAt(config, pointer, value) as Registry), [edit]);
  const replaceAll = useCallback((config: Registry) => edit(() => config), [edit]);

  const undo = useCallback(() => {
    setSnap((c) => {
      const previous = c.past[c.past.length - 1];
      if (!previous || !c.config) return c;
      return { ...c, config: previous, past: c.past.slice(0, -1), future: [c.config, ...c.future] };
    });
  }, []);
  const redo = useCallback(() => {
    setSnap((c) => {
      const next = c.future[0];
      if (!next || !c.config) return c;
      return { ...c, config: next, past: [...c.past, c.config], future: c.future.slice(1) };
    });
  }, []);

  const validateNow = useCallback(async () => {
    clearTimeout(retryTimer.current);
    const config = latest.current.config;
    if (!config) return;
    const id = ++runId.current;
    setValidation((v) => ({ ...v, state: 'checking', error: null }));
    try {
      const result = await validateConfig(config);
      if (id !== runId.current) return;
      validatedFor.current = config;
      validatedRevision.current = activeRevision;
      setValidation({ state: 'done', result, issues: result.issues ?? [], stale: latest.current.config !== config, error: null });
    } catch (error) {
      if (id !== runId.current) return;
      validatedFor.current = config;
      validatedRevision.current = activeRevision;
      if (error instanceof ApiError && error.kind === 'validation') {
        setValidation({ state: 'done', result: { valid: false, issues: error.issues }, issues: error.issues, stale: latest.current.config !== config, error: null });
      } else {
        const limited = error instanceof ApiError && error.kind === 'rate_limited';
        setValidation({ state: 'failed', result: null, issues: [], stale: false, error: describeError(error) });
        // Too many requests is transient: look again later instead of leaving the document unchecked.
        if (limited) retryTimer.current = setTimeout(() => { if (id === runId.current && autoValidateRef.current) void validateNow(); }, 60_000);
      }
    }
  }, [activeRevision]);

  // A comparison belongs to both its candidate and its active server version.
  useEffect(() => {
    clearTimeout(retryTimer.current);
    runId.current += 1;
    validatedFor.current = null;
    setValidation(IDLE);
  }, [activeRevision]);

  // Debounced unsaved validation after every edit.
  useEffect(() => {
    if (!snap.config) return;
    if (validatedFor.current !== null && validatedFor.current !== snap.config) {
      setValidation((v) => (v.stale ? v : { ...v, stale: true }));
    }
    if (!autoValidate || validatedFor.current === snap.config) return;
    const timer = setTimeout(() => void validateNow(), validateDebounceMs);
    return () => clearTimeout(timer);
  }, [snap.config, autoValidate, validateNow, validateDebounceMs, activeRevision]);

  useEffect(() => () => { runId.current += 1; clearTimeout(retryTimer.current); }, []);

  // Unsaved changes: warn on leaving the page.
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const save = useCallback(async () => {
    if (invalidDurations.size) { setSaveError(uiText('b4.time.precision')); return 'error' as const; }
    const { draft, config, name } = latest.current;
    if (!draft || !config || savingRef.current) return 'error' as const;
    const owner = documentId.current;
    savingRef.current = true;
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await update.mutateAsync({ draft, config, display_name: name });
      if (owner === documentId.current) setSnap((c) => ({ ...c, draft: saved }));
      return 'saved' as const;
    } catch (error) {
      if (owner !== documentId.current) return 'error' as const;
      if (error instanceof ApiError && error.code === 'draft_version_conflict') {
        try {
          const server = await api<Draft>(`registry/drafts/${encodeURIComponent(draft.draft_id)}`);
          if (owner !== documentId.current) return 'error' as const;
          const comparison = await api<NonNullable<Conflict['comparison']>>(`registry/drafts/${encodeURIComponent(draft.draft_id)}/reconcile`, { method: 'POST', body: { draft_version: draft.draft_version, expected_server_version: server.draft_version, config, display_name: name } });
          setConflict({ server, comparison });
          return 'conflict' as const;
        } catch {
          /* fall through to a plain error */
        }
      }
      setSaveError(describeError(error));
      return 'error' as const;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [update]);

  /** Save a local edit atomically, with pending metadata when creating objects. */
  const editAndSave = useCallback(async (updater: (config: Registry) => Registry, editorial: EditorialEntry[] = []) => {
    if (invalidDurations.size) throw new Error(uiText('b4.time.precision'));
    const current = latest.current;
    if (!current.draft || !current.config || savingRef.current) throw new Error(uiText('alpha7.draft.saveBeforeReview'));
    const owner = documentId.current;
    const changed = updater(current.config);
    const next = current.config.config_schema_version === 4 ? upgradeFreshness(changed) : changed;
    savingRef.current = true; setSaving(true); setSaveError(null);
    try {
      const saved = await update.mutateAsync({ draft: current.draft, config: next, display_name: current.name, editorial });
      if (owner !== documentId.current) throw new Error(uiText('alpha7.draft.changedDuringAction'));
      // Preserve concurrent typing: the committed candidate never overwrites newer local edits.
      setSnap(c => c.config === current.config && c.name === current.name
        ? { ...c, draft: saved, config: next, past: [...c.past, current.config!].slice(-HISTORY_LIMIT), future: [] }
        : { ...c, draft: saved });
      if (latest.current.config !== current.config || latest.current.name !== current.name) throw new Error(uiText('alpha7.draft.changedDuringAction'));
      latest.current = { ...current, draft: saved, config: next, past: [...current.past, current.config].slice(-HISTORY_LIMIT), future: [] };
      return saved;
    } catch (failure) {
      if (owner === documentId.current) setSaveError(describeError(failure));
      throw failure;
    } finally { savingRef.current = false; setSaving(false); }
  }, [update]);

  /** Confirmation always receives the exact saved candidate, even after an async save. */
  const prepareActivation = useCallback(async (): Promise<Draft> => {
    if (invalidDurations.size) throw new Error(uiText('b4.time.precision'));
    const current = latest.current;
    const owner = documentId.current;
    if (!current.draft || !current.config) throw new Error(uiText('alpha7.draft.noDraft'));
    if (!deepEqual(current.config, current.draft.config) || current.name !== (current.draft.display_name ?? '')) {
      if (await save() !== 'saved') throw new Error(uiText('alpha7.draft.saveBeforeReview'));
    }
    const saved = await api<Draft>(`registry/drafts/${encodeURIComponent(current.draft.draft_id)}`);
    if (owner !== documentId.current || latest.current.config !== current.config || latest.current.name !== current.name || !deepEqual(saved.config, current.config) || (saved.display_name ?? '') !== current.name) {
      throw new Error(uiText('alpha7.draft.changedDuringAction'));
    }
    return saved;
  }, [save]);

  const resolveConflict = useCallback(
    async (choice: 'take-server' | 'merge' | 'cancel', decisions: Record<string, { digests: string[]; choice: string }> = {}) => {
      const current = conflict;
      const { config, draft } = latest.current;
      if (!current || !config || !draft || current.server.draft_id !== draft.draft_id || savingRef.current) return;
      const owner = documentId.current;
      if (choice === 'cancel') return setConflict(null);
      if (choice === 'take-server') {
        setConflict(null);
        return open(current.server, { reload: true });
      }
      try {
        const merged = await api<{ config: Registry; display_name: string; conflicts: NonNullable<Conflict['comparison']>['conflicts'] }>(`registry/drafts/${encodeURIComponent(draft.draft_id)}/reconcile`, { method: 'POST', body: { draft_version: draft.draft_version, expected_server_version: current.server.draft_version, config, display_name: latest.current.name, decisions } });
        if (merged.conflicts.length) { setConflict({ ...current, comparison: merged }); return; }
        const saved = await update.mutateAsync({ draft: current.server, config: merged.config, display_name: merged.display_name });
        if (owner !== documentId.current) return;
        setConflict(null);
        open(saved, { reload: true });
      } catch (error) {
        if (owner !== documentId.current) return;
        setSaveError(describeError(error));
        setConflict(null);
      }
    },
    [conflict, open, update],
  );

  const value = useMemo<WorkingDocument>(
    () => ({
      editing,
      starting,
      setEditing,
      ensureDraft,
      prepareActivation,
      draft: snap.draft,
      config: snap.config,
      displayName: snap.name,
      setDisplayName,
      dirty,
      canUndo: snap.past.length > 0,
      canRedo: snap.future.length > 0,
      saving,
      conflict,
      validation: { ...validation, stale: validation.stale || activeRevision === undefined || (validation.result !== null && (validatedFor.current !== snap.config || validatedRevision.current !== activeRevision)) },
      autoValidate,
      setAutoValidate,
      open,
      requestOpen,
      switchRequest,
      confirmSwitch,
      cancelSwitch,
      close,
      edit,
      editAndSave,
      setAt: setAtPointer,
      replaceAll,
      undo,
      redo,
      save,
      saveError,
      validateNow,
      resolveConflict,
    }),
    [snap, dirty, saving, editing, starting, ensureDraft, prepareActivation, conflict, validation, activeRevision, autoValidate, open, requestOpen, switchRequest, confirmSwitch, cancelSwitch, close, edit, editAndSave, setAtPointer, setDisplayName, replaceAll, undo, redo, save, saveError, validateNow, resolveConflict],
  );

  return (
    <Context.Provider value={value}>
      <IssuesProvider value={validation.issues}>
        <UnsupportedOrigins config={snap.draft && (snap.draft.lifecycle ?? 'open') === 'open' ? snap.config : active.data?.config ?? null} onCorrect={async () => { await ensureDraft(); return editAndSave(removeUnsupportedOrigins); }} />
        {children}
      </IssuesProvider>
      <Dialog open={candidates !== null} onOpenChange={open => { if (!open) { choose.current?.(undefined); setCandidates(null); } }} title={uiText('beta3.6b56c9782abb')} description={uiText('beta3.df29192a8ae6')} footer={<><Button onClick={() => { choose.current?.(null); setCandidates(null); }}>{uiText('beta3.eeb949e516a4')}</Button>{!!candidates?.length && <Button variant="primary" disabled={candidates.length > 1 && selectedDraft === null} onClick={() => { const chosen = candidates.length === 1 ? candidates[0] : candidates.find(d => d.draft_id === selectedDraft); if (chosen) { choose.current?.(chosen); setCandidates(null); } }}>{candidates.length === 1 ? uiText('beta3.a6832204437f') : uiText('beta3.8ec4b93a19e1')}</Button>}</>}>
        {snap.draft && !currentDraft(snap.draft, activeRevision) && <Link to={`/changes/drafts/${snap.draft.draft_id}`} onClick={() => { choose.current?.(undefined); setCandidates(null); }}>{uiText('beta3.2159c0ff29be')}</Link>}
        {candidates?.map(draft => <label key={draft.draft_id} className="my-3 flex min-h-11 items-center gap-2">{candidates.length > 1 && <input type="radio" name="working-draft" checked={selectedDraft === draft.draft_id} onChange={() => setSelectedDraft(draft.draft_id)} />}<span>{draft.display_name} {uiText('beta3.d06ef8770435')}{draft.base_revision} · {draft.updated_at}</span></label>)}
      </Dialog>
    </Context.Provider>
  );
}

export function useWorkingDocument(): WorkingDocument {
  const value = useContext(Context);
  if (!value) throw new Error(uiText("features.authoring.WorkingDocument.workingdocumentprovider_fehlt_950e4e"));
  return value;
}

export function useOptionalWorkingDocument(): WorkingDocument | null {
  return useContext(Context);
}

/** Read a subtree of the working document. */
export function useDocAt<T extends Json | undefined>(pointer: string): T {
  const { config } = useWorkingDocument();
  return getAt(config, pointer) as T;
}
import { invalidDurations } from '@/components/SecondsField';
import { upgradeFreshness } from '@/features/sources/freshness';
