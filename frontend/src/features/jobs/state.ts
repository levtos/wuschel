import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { SystemState } from '@/features/admin/operations';

export type WorkPhase = 'waiting' | 'running' | 'finished' | 'failed' | 'cancelled';

export interface WorkJob {
  id: string;
  path: string;
  phase: WorkPhase;
  created_at: string;
  error: string | null;
  status: number | null;
}

export interface WorkState {
  available: boolean;
  jobs: WorkJob[];
  workers?: number;
  cpus?: number;
  needed?: number;
  maximum?: number;
}

export function useOperatingState() {
  return useQuery({
    queryKey: ['system-state'],
    queryFn: ({ signal }) => api<SystemState>('system/state', { signal }),
    refetchInterval: 2000,
    retry: false,
  });
}
