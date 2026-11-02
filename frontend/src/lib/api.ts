import { hasTranslation, t as uiText } from '@/i18n';
import type { Json } from './json';
import { coolDownRequests, reserveRequest } from './request-budget';
import { assertIngressResponse, browserSession, SessionExpired } from './browser-session';

/** One structured server finding; paths are JSON Pointers into the submitted document. */
export interface Issue {
  code: string;
  path: string;
  object_id?: string | null;
  message: string;
  parameters?: Record<string, string | null> | null;
}

export type ErrorKind =
  | 'validation'
  | 'conflict'
  | 'auth'
  | 'persistence'
  | 'not_found'
  | 'unavailable'
  | 'rate_limited'
  | 'network'
  | 'request';

export interface ChangedEntry { identity: string; key: string }

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly issues: Issue[];
  readonly kind: ErrorKind;
  readonly changedEntries: ChangedEntry[];
  constructor(status: number, code: string, issues: Issue[], detail: string, changedEntries: ChangedEntry[] = []) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.issues = issues;
    this.kind = classify(status, code, issues);
    this.changedEntries = changedEntries;
  }
}

function classify(status: number, code: string, issues: Issue[]): ErrorKind {
  if (status === 0) return 'network';
  if (issues.length > 0) return 'validation';
  if (status === 409 || code.endsWith('conflict')) return 'conflict';
  if (status === 429) return 'rate_limited';
  if (status === 401 || status === 403) return 'auth';
  if (code === 'persistence_unavailable') return 'persistence';
  if (status === 404) return 'not_found';
  if (status === 503) return 'unavailable';
  return 'request';
}

interface ErrorBody {
  error?: string;
  issues?: Issue[];
  details?: { location?: (string | number)[]; type?: string }[] | { changed_entries?: ChangedEntry[] };
}

export function issuesFromBody(body: ErrorBody | null | undefined): Issue[] {
  if (!body) return [];
  if (Array.isArray(body.issues) && body.issues.length > 0) return body.issues;
  // Structural Pydantic failures carry only locations; show them at the same field.
  return (Array.isArray(body.details) ? body.details : [])
    .filter((d) => Array.isArray(d.location))
    .map((d) => ({
      code: `schema.${d.type ?? 'invalid'}`,
      path:
        '/' +
        d.location!.map((p) => String(p).replace(/~/g, '~0').replace(/\//g, '~1')).join('/'),
      message: d.type ?? uiText("lib.api.invalid_f1234d"),
    }));
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

/** Same-origin call below the (relative) Ingress prefix. No credentials are kept in the browser. */
async function request(path: string, options: RequestOptions, reserved = false): Promise<Response> {
  const method = options.method ?? 'GET';
  let response: Response;
  const sessionDocument = browserSession.getDocumentVersion();
  try {
    response = await browserSession.run(async () => {
      if (!reserved) await reserveRequest(options.signal);
      await browserSession.waitReady(options.signal);
      options.signal?.throwIfAborted();
      const result = await fetch(`./api/v1/${path}`, {
      method,
      headers: options.body === undefined ? { 'X-Authoring-Protocol': '2' } : { 'Content-Type': 'application/json', 'X-Authoring-Protocol': '2' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      });
      assertIngressResponse(result, sessionDocument);
      return result;
    }, options.signal);
  } catch (error) {
    if (error instanceof SessionExpired) throw new ApiError(error.status, 'ingress_session_expired', [], uiText('b22.connection.reload'));
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'network_error', [], uiText("lib.api.der_dienst_ist_nicht_erreichbar_793240"));
  }
  if (!response.ok) {
    if (response.status === 429) await coolDownRequests(response.headers.get('Retry-After'));
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    const code = body?.error ?? 'invalid_response';
    throw new ApiError(response.status, code, issuesFromBody(body), `${code} (${response.status})`,
      body?.details && !Array.isArray(body.details) ? body.details.changed_entries ?? [] : []);
  }
  return response;
}

export async function api<T = Json>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await request(path, options);
  if (response.status === 204) return null as T;
  return (await response.json()) as T;
}

/** Reserve both calls before reading the server clock. A short-lived confirmed
 * operation must not spend its validity window waiting for another budget slot.
 * Neither call is replayed; the body callback rechecks the confirmation after waiting. */
export async function apiWithFreshServerTime<T = Json>(path: string, body: (serverTime: string) => unknown, signal?: AbortSignal): Promise<T> {
  await reserveRequest(signal, 2);
  const info = await (await request('info', { signal }, true)).json() as { server_time: string };
  const response = await request(path, { method: 'POST', body: body(info.server_time), signal }, true);
  return (await response.json()) as T;
}

/** Preserve portable format-1 numeric spelling and its historical digests. */
export async function apiText(path: string, options: RequestOptions = {}): Promise<string> {
  return (await request(path, options)).text();
}

const MESSAGES: Record<string, string> = {
  catalog_cleanup_conflict: uiText('b20.cleanup.conflict'),
  catalog_cleanup_invalid: uiText('b20.cleanup.invalid'),
  catalog_cleanup_limit: uiText('b20.cleanup.limit'),
  authoring_upgrade_required: uiText('b3.error.authoring_upgrade_required'),
  draft_base_conflict: uiText('b3.error.draft_base_conflict'),
  draft_base_unknown: uiText('b3.error.draft_base_unknown'),
  draft_closed: uiText('b3.error.draft_closed'),
  import_plan_conflict: uiText('b3.error.import_plan_conflict'),
  catalog_content_conflict: uiText('b3.error.catalog_content_conflict'),
  editorial_version_conflict: uiText('b3.error.editorial_version_conflict'),
  comparison_expired: uiText('b3.error.comparison_expired'),
  confirmation_required: uiText('b3.error.confirmation_required'),
  request_identity_conflict: uiText('b3.error.request_identity_conflict'),
  continuation_version_conflict: uiText('b3.error.continuation_version_conflict'),
  continuation_closed: uiText('b3.error.continuation_closed'),
  continuation_unresolved: uiText('b3.error.continuation_unresolved'),
  continuation_limit: uiText('b3.error.continuation_limit'),
  catalog_definition_conflict: uiText('b3.error.catalog_definition_conflict'),
  invalid_request_id: uiText('b3.error.invalid_request_id'),

  draft_limit: uiText("lib.api.es_sind_bereits_100_entwurfe_vorhanden_entfernen_f89be6"),
  draft_identity_conflict: uiText("lib.api.diese_entwurfs_id_ist_bereits_vergeben_2355ad"),
  sensitive_document: uiText("lib.api.geheimnisse_sind_in_der_konfiguration_nicht_erla_dfcfff"),
  draft_version_conflict:
    uiText("lib.api.der_draft_wurde_zwischenzeitlich_geandert_neu_la_8afab3"),
  active_revision_conflict:
    uiText("lib.api.die_aktive_revision_hat_sich_geandert_stand_neu__b101a1"),
  no_active_registry: uiText("lib.api.es_ist_noch_keine_registry_aktiv_2f47c4"),
  persistence_unavailable: uiText("lib.api.persistenz_derzeit_nicht_erreichbar_es_wurde_nic_1c4a47"),
  ha_discovery_unavailable: uiText("lib.api.home_assistant_entitatsliste_derzeit_nicht_verfu_4a74b1"),
  contract_disabled: uiText("lib.api.der_contract_ist_deaktiviert_225806"),
  revision_not_found: uiText("lib.api.diese_revision_existiert_nicht_011869"),
  evidence_not_found: uiText("lib.api.zu_dieser_referenz_liegt_keine_aufgezeichnete_ev_a4cab9"),
  evidence_outside_retention: uiText('alpha8.retention.evidenceGone'),
  invalid_request: uiText("lib.api.die_anfrage_ist_ungultig_85729c"),
  network_error: uiText("lib.api.der_dienst_ist_nicht_erreichbar_793240"),
  rate_limited: uiText("lib.api.zu_viele_anfragen_weitere_anfragen_warten_diese__545695"),
};

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'ingress_session_expired') return uiText('b22.connection.reload');
    if (error.kind === 'auth') return uiText("lib.api.keine_berechtigung_fur_diese_aktion_a1a527");
    if (error.kind === 'rate_limited') return MESSAGES.rate_limited!;
    return MESSAGES[error.code] ?? (hasTranslation(`codes.${error.code}`) ? uiText(`codes.${error.code}`) : uiText('alpha9.form.unavailable'));
  }
  return uiText('alpha9.form.unavailable');
}
