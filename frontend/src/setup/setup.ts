import { t as uiText } from '@/i18n';
import { coolDownRequests, reserveRequest } from '@/lib/request-budget';
import { assertIngressResponse, browserSession } from '@/lib/browser-session';

export interface Connection {
  host: string; port: number; database: string; user: string; password?: string;
  sslmode: 'verify-full' | 'require' | 'disable'; ca: string;
}
export interface SetupStatus {
  phase: 'welcome' | 'connecting' | 'completed' | 'configuration_error'; authority: string;
  database_configured: boolean; database: Connection | null; csrf_token: string;
  onboarding_complete: boolean;
  installation_id: string | null; error: string; restart_required: boolean;
  registry_revision?: number | null;
}
const errors: Record<string, string> = {
  database_setup_pending: uiText("setup.setup.datenbankeinrichtung_noch_nicht_abgeschlossen_78e93d"),
  bridge_setup_pending: uiText("setup.setup.bridge_noch_nicht_aktiv_einrichtung_bleibt_offen_e255f6"),
  ha_unavailable: uiText("setup.setup.ha_verbindung_nicht_verfugbar_keine_bridge_konfi_e25bc0"),
  ha_connection_failed: uiText("setup.setup.ha_verbindung_fehlgeschlagen_erreichbarkeit_und__783d6b"),
  bridge_not_discovered: uiText("setup.setup.bridge_noch_nicht_erkannt_hacs_installation_und__6d0651"),
  bridge_flow_changed: uiText("setup.setup.bridge_config_flow_entspricht_nicht_dem_erwartet_2b24e0"),
  bridge_configuration_failed: uiText("setup.setup.bridge_konfiguration_nicht_bestatigt_zustand_in__27f18b"),
  database_connection_failed: uiText("setup.setup.postgresql_nicht_erreichbar_oder_anmeldung_tls_f_4d0581"),
  postgres_version_unsupported: uiText("setup.setup.postgresql_14_oder_neuer_ist_erforderlich_85b1b5"),
  application_role_too_privileged: uiText("setup.setup.die_anwendungsrolle_besitzt_administratorrechte_9d1faf"),
  foreign_database: uiText("setup.setup.die_datenbank_enthalt_fremde_daten_eine_eigene_l_0c9df2"),
  installation_mismatch: uiText("setup.setup.installations_id_passt_nicht_zu_dieser_app_keine_f2dbbb"),
  adopt_installation_id_required: uiText("setup.setup.vorhandene_datenbank_nur_mit_ihrer_ausdrucklich__4ba1fc"),
  initialize_empty_database_required: uiText("setup.setup.initialisierung_der_eigenen_leeren_datenbank_aus_edb871"),
  insecure_tls_confirmation_required: uiText("setup.setup.den_eingeschrankten_tls_schutz_ausdrucklich_best_604357"),
  confirmation_required: uiText("setup.setup.ubernahme_der_verbindung_ausdrucklich_bestatigen_e1e494"),
  provision_request_mismatch: uiText("setup.setup.der_importcode_gehort_nicht_zu_diesem_einrichtun_ad0620"),
  invalid_setup_request: uiText("setup.setup.eingaben_oder_importcode_ungultig_bitte_prufen_4d9ba1"),
};
export class SetupError extends Error {}
export async function setupRequest<T>(path: string, body?: unknown, csrf = ''): Promise<T> {
  const sessionDocument = browserSession.getDocumentVersion();
  const response = await browserSession.run(async () => {
    await reserveRequest();
    await browserSession.waitReady();
    const result = await fetch(`./api/v1/setup/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Setup-CSRF': csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
    });
    assertIngressResponse(result, sessionDocument);
    return result;
  });
  if (response.status === 429) await coolDownRequests(response.headers.get('Retry-After'));
  const value = await response.json().catch(() => ({ error: 'invalid_response' }));
  if (!response.ok) throw new SetupError(errors[value.error] ?? uiText("setup.setup.einrichtung_nicht_verfugbar_ingress_sitzung_erne_14a439"));
  return value as T;
}
export function validConnection(value: Connection, existing: boolean): boolean {
  return /^[a-zA-Z0-9_.:\-]+$/.test(value.host) && value.port >= 1 && value.port <= 65535
    && (existing ? value.database.length > 0 && value.database.length <= 63 && value.user.length > 0 && value.user.length <= 63 : /^[a-z][a-z0-9_]{0,62}$/.test(value.database) && /^[a-z][a-z0-9_]{0,62}$/.test(value.user))
    && !['postgres', 'template0', 'template1'].includes(value.database)
    && (!existing || !!value.password) && (!value.ca || (value.ca.startsWith('/ssl/') && !value.ca.split('/').includes('..')));
}
