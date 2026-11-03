import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { api, ApiError } from './api';
import type { Capabilities } from './capabilities';
import type { Json, JsonObject } from './json';
import type {
  CommandResult,
  ContractDiagnosis,
  Diagnostics,
  Draft,
  HaEntityPage,
  HistoryPage,
  Info,
  Problems,
  Registry,
  RevisionRow,
  TokenRotation,
  ValidationResult,
} from './types';

/**
 * Live values arrive over the WebSocket; HTTP polling is only a slow safety net. The Ingress
 * listener rate-limits to 120 requests per minute for all browser tabs together.
 */
export const POLL_MS = 15_000;

export const keys = {
  info: ['info'] as const,
  capabilities: ['capabilities'] as const,
  active: ['registry', 'active'] as const,
  export: ['registry', 'export'] as const,
  revisions: ['registry', 'revisions'] as const,
  drafts: ['registry', 'drafts'] as const,
  draft: (id: string) => ['registry', 'drafts', id] as const,
  diagnostics: ['diagnostics'] as const,
  problems: ['diagnostics', 'problems'] as const,
  diagnosis: (id: string) => ['diagnostics', 'contracts', id] as const,
  sources: ['sources'] as const,
  bindings: ['bindings'] as const,
  definitions: ['definitions'] as const,
  history: (kind: string, filters: object) => ['history', kind, filters] as const,
  ha: (search: string) => ['ha-entities', search] as const,
};

export const useInfo = () => useQuery({ queryKey: keys.info, queryFn: () => api<Info>('info') });

/** Capability metadata is the contract for every editor; refetched after activation. */
export const useCapabilities = () =>
  useQuery({
    queryKey: keys.capabilities,
    queryFn: () => api<Capabilities>('capabilities'),
    staleTime: 60_000,
  });

export const useActiveRevision = () =>
  useQuery({
    queryKey: keys.active,
    queryFn: () => api<RevisionRow | null>('registry/active'),
  });

/** The active configuration, or `null` when no registry has been activated yet (HTTP 409). */
export const useActiveExport = () =>
  useQuery({
    queryKey: keys.export,
    queryFn: async () => {
      try {
        return await api<Registry>('registry/active/export');
      } catch (error) {
        if (error instanceof ApiError && error.code === 'no_active_registry') return null;
        throw error;
      }
    },
  });

export const useRevisions = () =>
  useQuery({
    queryKey: keys.revisions,
    queryFn: () => api<RevisionRow[]>('registry/revisions'),
  });

export const useDrafts = () =>
  useQuery({ queryKey: keys.drafts, queryFn: () => api<Draft[]>('registry/drafts') });

export const useDiagnostics = (refetchMs: number | false = POLL_MS) =>
  useQuery({
    queryKey: keys.diagnostics,
    queryFn: () => api<Diagnostics>('diagnostics'),
    refetchInterval: refetchMs,
  });

export const useProblems = (refetchMs: number | false = POLL_MS) =>
  useQuery({
    queryKey: keys.problems,
    queryFn: () => api<Problems>('diagnostics/problems'),
    refetchInterval: refetchMs,
  });

export const useContractDiagnosis = (contractId: string | undefined) =>
  useQuery({
    queryKey: keys.diagnosis(contractId ?? ''),
    queryFn: () => api<ContractDiagnosis>(`diagnostics/contracts/${encodeURIComponent(contractId!)}`),
    enabled: !!contractId,
    refetchInterval: POLL_MS,
    retry: (count, error) => !(error instanceof ApiError && error.kind === 'not_found') && count < 2,
  });

export const useSources = () =>
  useQuery({ queryKey: keys.sources, queryFn: () => api<JsonObject[]>('sources') });
export const useBindings = () =>
  useQuery({ queryKey: keys.bindings, queryFn: () => api<JsonObject[]>('bindings') });

export interface HistoryFilters {
  contract_id?: string;
  source_id?: string;
  since?: string;
  until?: string;
  limit?: number;
}

export function historyPath(kind: string, filters: HistoryFilters, cursor?: string | null): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  if (cursor) query.set('cursor', cursor);
  const text = query.toString();
  return `history/query/${encodeURIComponent(kind)}${text ? `?${text}` : ''}`;
}

/** Cursor-paged history; the cursor is opaque and bound to the filter scope. */
export const useHistory = (kind: string, filters: HistoryFilters, enabled = true, refetchMs: number | false = false) =>
  useInfiniteQuery({
    queryKey: keys.history(kind, filters),
    queryFn: ({ pageParam }) => api<HistoryPage>(historyPath(kind, filters, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled,
    refetchInterval: refetchMs,
    placeholderData: keepPreviousData,
  });

export const useHaEntities = (search: string, enabled: boolean, preview = false) =>
  useInfiniteQuery({
    queryKey: keys.ha(`${search}:${preview ? 'preview' : 'metadata'}`),
    queryFn: ({ pageParam }) =>
      api<HaEntityPage>(`ha/entities?limit=200${preview ? '&preview=1' : ''}${pageParam ? `&after=${encodeURIComponent(pageParam)}` : ''}`),
    initialPageParam: '' as string,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
    retry: false,
    staleTime: 15_000,
  });

export function useDraftMutations() {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: keys.drafts });
  return {
    create: useMutation({
      retry: false,
      mutationFn: async (input: { config: Registry; display_name?: string; draft_id?: string; expected_active_revision?: number; from_active?: boolean; signal?: AbortSignal }) => {
        const { from_active, signal, ...body } = input;
        for (let attempt = 0; ; attempt++) {
          const head = from_active || input.expected_active_revision === undefined ? await api<RevisionRow | null>('registry/active', { signal }) : null;
          try {
            return await api<Draft>('registry/drafts', { method: 'POST', signal, body: { ...body, config: from_active && head ? head.config : input.config, expected_active_revision: from_active ? head?.revision ?? 0 : input.expected_active_revision ?? head?.revision ?? 0 } });
          } catch (error) {
            if (!from_active || attempt >= 1 || !(error instanceof ApiError) || error.code !== 'active_revision_conflict') throw error;
          }
        }
      },
      onSuccess: refresh,
    }),
    update: useMutation({
      retry: false,
      mutationFn: (input: { draft: Draft; config: Registry; display_name?: string; editorial?: import('@/features/editorial/model').EditorialEntry[] }) =>
        api<Draft>(`registry/drafts/${encodeURIComponent(input.draft.draft_id)}`, {
          method: 'PUT',
          body: {
            config: input.config,
            draft_version: input.draft.draft_version,
            preservation_save: true,
            ...(input.display_name !== undefined ? { display_name: input.display_name } : {}),
            ...(input.editorial ? { editorial: input.editorial } : {}),
          },
        }),
      onSuccess: (draft) => {
        client.setQueryData(keys.draft(draft.draft_id), draft);
        void client.invalidateQueries({ queryKey: ['editorial'] });
        void refresh();
      },
    }),
    remove: useMutation({
      retry: false,
      mutationFn: (draft: Draft) =>
        api<{ deleted: boolean }>(`registry/drafts/${encodeURIComponent(draft.draft_id)}`, {
          method: 'DELETE',
          body: { draft_version: draft.draft_version },
        }),
      onSuccess: refresh,
    }),
  };
}

/** Validation without saving; the same validator serves drafts and imports. */
export const validateConfig = (config: Registry) =>
  api<ValidationResult>('registry/validate', { method: 'POST', body: config });

export function useActivation() {
  const client = useQueryClient();
  const done = () =>
    Promise.all(
      [keys.active, keys.export, keys.revisions, keys.drafts, keys.capabilities, keys.sources, keys.bindings, keys.diagnostics, ['history'], ['editorial']].map(
        (key) => client.invalidateQueries({ queryKey: key }),
      ),
    );
  return {
    activate: useMutation({
      retry: false,
      mutationFn: (input: { draft: Draft; expectedActiveRevision: number; note?: string; run?: () => Promise<RevisionRow> }) =>
        input.run ? input.run() : api<RevisionRow>(`registry/drafts/${encodeURIComponent(input.draft.draft_id)}/activate`, {
          method: 'POST',
          body: {
            draft_version: input.draft.draft_version,
            expected_active_revision: input.expectedActiveRevision,
            ...(input.note?.trim() ? { note: input.note.trim() } : {}),
          },
        }),
      // The mutation response is the committed acknowledgement. Slow cache reads
      // must not delay its success feedback or make a completed action look stale.
      onSuccess: () => { void done(); },
    }),
    rollback: useMutation({
      retry: false,
      mutationFn: (input: { revision: number; expectedActiveRevision: number; note?: string }) =>
        api<RevisionRow>('registry/rollback', {
          method: 'POST',
          body: { revision: input.revision, expected_active_revision: input.expectedActiveRevision, ...(input.note?.trim() ? { note: input.note.trim() } : {}) },
        }),
      onSuccess: () => { void done(); },
    }),
  };
}

/**
 * Deliberately NOT a react-query mutation: mutation results are retained in the
 * client's mutation cache, which would keep the one-time credential alive after
 * it was dismissed. The caller holds the response in component state only.
 */
export const rotateToken = (role: 'admin' | 'consumer') => api<TokenRotation>('tokens/rotate', { method: 'POST', body: { role } });

export const useSubmitCommand = () =>
  useMutation({
    retry: false,
    mutationFn: (command: JsonObject) => api<CommandResult>('commands', { method: 'POST', body: command }),
  });

export type { Json };
