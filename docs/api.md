# HTTP/WS API, protocol 1

Alpha-4-Erweiterung (Implementierung, noch keine Releasefreigabe):
[Manual-first Workbench API](alpha4/workbench-api.md) und
[ADR 0003](adr/0003-declarative-platform.md). Die bisherigen Protokoll-1-Routen
bleiben erhalten; deklarative Definitionen, Verwaltung und Diagnose sind additiv.

Phase-1A-Ergänzung: Das Envelope-Feld `fields` enthält mehrere unabhängig validierte
Felder. `contract_health` aggregiert nur Pflichtfelder, `available` nur die deklarierten
Projektionsfelder. `/types` beschreibt bei neuen Typversionen optionale `commands`
(JSON-Schema je Command) und `refresh_inputs`; v1-Typen bleiben kompatibel.
Command-Argumente werden strikt validiert: Fehler → `rejected/invalid_value`, falsche
erwartete Registry-Revision → `rejected/active_revision_conflict`. Wiederholungen gleicher
ID/gleichen Originalinhalts liefern unverändert die persistierte Antwort.
Diagnostics ergänzen `bridge_version`, `bridge_subscriptions`, `ingest_overflow_count`
und `refresh_overflow_count` (Zähler seit Prozessstart).
Readiness benötigt einen initialen Bridge-Snapshot ausschließlich für tatsächlich
benötigte HA-Quellen. Siehe [ADR 0002](adr/0002-phase1a-producer-and-ownership.md).

## Ingress-only setup (before installation identity)

`/api/v1/setup/status` and `/api/v1/setup/bridge` are read-only Ingress routes.
`POST /api/v1/setup/reachability`, `/provision`, `/import`, `/connect` require
JSON plus `X-Setup-CSRF` from status; cross-site requests are rejected. These
routes do not exist on the consumer listener, even with an admin bearer token.
Ordinary API/WS stays unavailable until initialization; UI and health remain
accessible within their original trust boundaries. Responses use static errors,
never credential/exception reflection. No setup endpoint activates a Registry,
installs files in HA, creates a HA helper or restarts HA/App.
See [First Run](first-run.md) for payload confirmations, persistence and gates.

`POST /api/v1/setup/bridge_configure` uses the fixed `core_contracts_bridge`
HA config flow after explicit confirmation; existing entries remain untouched.
It shares the same Ingress/JSON/CSRF boundary and grants no file-install/restart access.

`POST /api/v1/setup/finish` requires `{"confirm":true}`, initialized database and
an active protocol-1 Bridge. It persists only onboarding completion, independently
of Registry/Snapshot readiness, under the same Ingress/JSON/CSRF boundary.

Consumer: Port 8787, `Authorization: Bearer <consumer_token>`; Admin-Token erlaubt
zusätzlich Administration. Ingress: Port 8099, ausschließlich Supervisor-Peer.
Health-Endpunkte auf 8787 benötigen kein Token. JSON-Zeiten sind RFC3339 UTC.
Der Client installiert sich aus `client/` und importiert keinen Plattformcode.

| Methode | `/api/v1/`-Pfad | Ergebnis |
|---|---|---|
| GET | info, types | Installation/Versionen; registrierte Schemas und Parameter |
| GET | contracts, contracts/{id} | Envelopes; disabled ergibt 409 `contract_disabled` |
| GET | snapshot?contracts=id1,id2 | konsistenter Stand einer Publication |
| GET | diagnostics/problems | Service-Zustand und Feldprobleme |
| POST / GET | commands / commands/{id} | persistiertes Command-Ergebnis |
| GET | diagnostics | Admin: Komponenten, Queues, Epoche, Gaps |
| GET | sources, bindings, evidence/{source_id}, history/{contract_id} | Admin-Diagnose |
| GET | registry/active, registry/active/export | Revision bzw. reine Konfiguration |
| GET | registry/revisions, registry/revisions/{n} | unveränderliche Historie |
| GET / POST | registry/drafts | auflisten / JSON-Konfiguration als Draft importieren |
| GET / PUT | registry/drafts/{id} | lesen / `{config, draft_version}` mit OCC speichern |
| POST | registry/drafts/{id}/validate | Validierung und Diff zur aktiven Revision |
| POST | registry/drafts/{id}/activate | `{draft_version, expected_active_revision}` |
| POST | registry/rollback | `{revision, expected_active_revision}`; neue Revisionsnummer |
| GET / POST | tokens / tokens/rotate | Rollen / `{role}` → einmaliger neuer Token |

Bereich 5 (#75) ergänzt die administrativen POST-Endpunkte
`title-import/preview` und `title-import/stage`. Beide erhalten `source_text`
(strikter v3-Export als JSON-Text), `expected_active_revision`, `mappings`
(Schlüssel = kanonisches JSON-Paar aus context/source_app; Wert = `exclude`
oder `{catalog_id,definition_version,scope,profile}`), optional `include_hidden`,
`music_keys` und `resolutions`. Profile: game, music, private. `music_keys`
ordnet einer einbasierten Zeilennummer `[Interpret,Titel]` oder `exclude` zu.
Stage verlangt außerdem `plan_digest`, UUID `request_id` und
`X-Authoring-Protocol: 2`. Es erzeugt ausschließlich einen Ersatzentwurf;
die bestehende versionierte Aktivierung übernimmt atomar. Vorschau schreibt
nichts. Grenzwerte, private Berichte, OCC und Wiederholung:
[Bereich 5 A2](beta/bereich-5/A2.md).

Commands: `command_id`, `contract_id`, `command`, `args`, `origin {kind,actor,client_name}`,
`issued_at`, `valid_until` (Pflicht), optional `expected_registry_revision`.
In Alpha 1 fordert nur `request_b` an `test.state_machine` eine Transition an.
Der Guard entscheidet. Ergebnisse: accepted, rejected mit Reason, expired,
not_supported, command_id_conflict. Gleiche ID/Inhalt liefert das gespeicherte
Ergebnis; bei DB-Ausfall kein Ack. Beliebige Wertmutation ist nicht vorgesehen.

## WebSocket

`GET /api/v1/ws` mit Bearer-Header. Zuerst
`{type:"hello", protocol_version:1, expected_installation_id:"..."}`.
Antwort `welcome` oder `installation_mismatch`. Anschließend
`{type:"subscribe", id:"...", contracts:["fixture.echo"]}`; `contracts:null`
abonniert alle aktiven Instanzen. `snapshot` liefert Epoche, Sequenz und Envelopes.
`delta` enthält `prev_seq`, `publication_seq`, Epoche und den konsistenten neuen
Stand. Gelöschte/deaktivierte Instanzen fehlen im neuen Stand.

Lücken, Epochenwechsel und langsame Subscriber ergeben `resync_required`.
Es gibt kein Replay; neu subscriben liefert einen atomaren Snapshot. `service_state`
zeigt Readiness und `publication_confirmed`; alte Quality wird bei DB-Ausfall
nicht umgeschrieben. Ping/Pong und `going_away` sind Transportereignisse.

`CoreContractsClient(base_url, token, expected_installation_id)` wird als async
Context Manager verwendet. Methoden: info, snapshot, contract, subscribe
(Async-Iterator), command. Der Client prüft die Installations-ID, resynchronisiert
automatisch und fragt nach verlorener Command-Antwort die stabile ID ab.
Er verändert weder Status noch Quality.
