export interface OperationSetting {
  key: string; value: number; active_value: number; default: number;
  minimum: number; maximum: number; unit: string; name: string; explanation: string;
  integer: boolean; restart_required: boolean; pending_restart: boolean;
}
export interface OperationSettings { notice: string | null; settings: OperationSetting[] }
export interface SystemState {
  ready: boolean; publication_confirmed: boolean; publication_seq: number; version: string;
  persistence: string; ha: string; bridge: string; mqtt: string; traber_version: string | null;
  operations_notice: string | null; startup_attempts: number; startup: Record<string, number>;
  queues: Record<string, { depth: number | null; capacity: number; overflows: number }>;
  event_loop: { lag_ms: number | null; max_lag_ms: number | null };
  commits: { count: number; window: number; median_ms: number | null; p95_ms: number | null };
  retries: Record<string, { count: number; consecutive: number; last_error: string | null; last_error_at: string | null }>;
  reasons: string[];
}
