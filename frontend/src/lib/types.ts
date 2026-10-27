import type { Issue } from './api';
import type { Json, JsonObject } from './json';

/** Server shapes as documented in docs/api.md and docs/alpha4/workbench-api.md. */

export interface Info {
  version: string;
  api_protocol: number;
  bridge_protocol: number;
  config_schema_version: number;
  installation_id: string;
  installation_label: string;
  epoch_id: string;
  publication_seq: number;
  server_time: string;
}

export interface ServiceState {
  type?: 'service_state';
  ready: boolean;
  publication_confirmed: boolean;
  persistence: string;
  ha: string;
  bridge: string;
  mqtt: string;
  mqtt_mode?: 'disabled' | 'supervisor' | 'external' | null;
  mqtt_external_settings_present?: boolean | null;
  reasons: string[];
  conditions?: { code: string; satisfied: boolean | null }[];
  ha_disconnected_since?: string | null;
  ha_unbounded_values?: number;
}

export type FieldStatus = 'valid' | 'held' | 'unknown' | 'not_applicable' | 'unresolved';

export interface Reason {
  code: string;
  input: string;
  source_id?: string | null;
  since: string;
  detail?: string | null;
}

export interface ValueOrigin { kind: 'measured' | 'replacement' | 'model' | 'forecast'; provider?: string; issued_at?: string | null; valid_from?: string | null; valid_until?: string | null; based_on?: string[] }
export interface FieldValue {
  value_origin?: ValueOrigin;
  input_origins?: ValueOrigin[];
  valid_from?: string | null;
  valid_until?: string | null;
  plausibility?: 'unchecked' | 'plausible' | 'suspicious';
  attribution?: { kind: 'known_automatic' | 'known_user' | 'unknown'; basis: string; matched_context_id?: string | null };
  configuration?: { complete: boolean; hints: { code: string; message: string }[] };
  status: FieldStatus;
  value: Json;
  quality: 'healthy' | 'degraded';
  held_until?: string | null;
  since_at?: string | null;
  freshness?: string | null;
  reasons: Reason[];
  evidence: string[];
  measured_at?: string | null;
  grace_declared?: boolean;
  positively_not_applicable?: boolean;
}

export interface Envelope {
  contract_id: string;
  type: ({ id: string; version: number } | { type_id: string; type_version: number }) & JsonObject;
  enabled: boolean;
  contract_health: 'healthy' | 'degraded';
  fields: Record<string, FieldValue>;
  available: boolean;
  state_machine?: JsonObject | null;
  computed_at: string;
  published_at: string;
  publication_seq: number;
  registry_revision: number;
  epoch_id: string;
}

export type Registry = JsonObject;

export interface Draft {
  lifecycle?: 'open' | 'completed' | 'continued';
  base_status?: 'known' | 'needs_review';
  base_current?: boolean;
  completed_revision?: number;
  continued_to_draft_id?: string;
  continuation_session_id?: string;
  draft_id: string;
  draft_version: number;
  config: Registry;
  display_name?: string;
  base_revision?: number;
  created_at?: string;
  updated_at?: string;
}

export interface RevisionRow {
  revision: number;
  config: Registry;
  checksum: string;
  created_at: string;
  actor?: JsonObject;
  note?: string;
}

export interface DiffGroup {
  added: string[];
  removed: string[];
  changed: string[];
  fields: Record<string, string[]>;
  field_paths: Record<string, string[]>;
  field_paths_truncated: string[];
}

export interface Diff {
  added: string[];
  removed: string[];
  changed: string[];
  objects: Record<string, DiffGroup>;
  directly_affected_contracts: string[];
  transitively_affected_contracts: string[];
  affected_contracts: string[];
  affected_fields: Record<string, string[]>;
  fingerprint_version: { before: number | null; after: number; context_migration: boolean };
  fingerprint_changes: string[];
  source_identity_changes: string[];
  binding_identity_changes: string[];
  changed_source_fingerprints: string[];
  effective_freshness_changes?: string[];
  freshness_effects?: { source_id: string; before: JsonObject | null; after: JsonObject | null }[];
  possibly_invalidated_contexts: string[];
  restore: { reinitialize_contracts: string[]; retains_absolute_deadlines_if_compatible: boolean };
  snapshot: { revision_changes: boolean; affected_contracts: string[] };
  analysis: 'static';
  predicts_values: false;
}

export interface ValidationResult {
  valid: boolean;
  issues: Issue[];
  diff?: Diff;
  machine_definitions?: Record<string, JsonObject>;
  warnings?: Issue[];
}

export interface HistoryItem extends JsonObject {
  id: string;
}

export interface HistoryPage {
  items: HistoryItem[];
  next_cursor: string | null;
  limit: number;
  order: string;
}

export interface HaEntity {
  entity_id: string;
  display_name: string;
  unit: string | null;
  device_class: string | null;
  state_class: string | null;
  attributes: string[];
  current_value?: string | null;
  area_id?: string | null;
  area_name?: string | null;
  area_icon?: string | null;
  refresh_profiles?: string[];
}

export interface HaEntityPage {
  items: HaEntity[];
  next_cursor: string | null;
  read_only: boolean;
  areas?: { area_id: string; area_name: string; area_icon?: string | null }[];
}

export interface TemporalState {
  id: string;
  state: JsonObject;
  restore_pending: boolean;
}

export interface TraceRecord extends JsonObject {}

export interface ContractDiagnosis {
  contract_id: string;
  registry_revision: number;
  start_case: string | null;
  fingerprint: string | null;
  expected_fingerprint: string;
  fingerprint_compatible: boolean;
  next_due: string | null;
  temporal: TemporalState[];
  trace: JsonObject | null;
  machine: {
    states: string[];
    initial: string;
    commands: JsonObject;
    deadline_seconds: number | null;
    sessions: boolean;
    transitions: {
      source: string;
      target: string;
      event: string;
      reason: string;
      new_episode: boolean;
      ends_episode: boolean;
    }[];
    current: JsonObject | null;
    recovery?: { status: string; condition_met: boolean; inputs: { name: string; status: string; reasons: string[] }[] } | null;
  } | null;
}

export interface Diagnostics extends Info {
  service: ServiceState;
  queue_size: number;
  commit_duration_s: number | null;
  bridge_version: string | number | null;
  bridge_subscriptions: number | null;
  ingest_overflow_count: number;
  refresh_overflow_count: number;
  gaps: JsonObject[];
  dependencies: JsonObject[];
}

export interface Problems {
  service: ServiceState;
  problems: { contract_id: string; field: string; status: string; reasons: Reason[] }[];
}

export interface TokenRotation {
  role: 'admin' | 'consumer';
  token: string;
  status: 'rotated';
  rotation_id: string;
  audit_status: 'complete' | 'completion_unconfirmed';
}

export interface CommandResult extends JsonObject {}
