# Routen und Operationen für den Arbeitsweg

Inventar für [A1 #180](wahrheit-hat-vorfahrt-a1.md) auf Basis von Wuschel b32
(`baee49c`). Pfade beziehen sich auf `/api/v1/`, soweit kein absoluter Pfad
angegeben ist. Geschweifte Klammern bezeichnen Pfadparameter. In Listen mit
Schrägstrichen und senkrechten Strichen gilt jeweils der vollständige
gemeinsame Präfix. „Übernehmen“ bezeichnet den kleinen atomaren Schritt
im seriellen Wahrheitsweg.

Grundlagen sind [der zentrale API-Dispatch](../../src/core_contracts/api.py),
[die Katalogrouten](../../src/core_contracts/extensions/catalog/catalog_routes.py),
[die Übergangsrouten für TC](../../src/core_contracts/extensions/tc_migration/title_routes.py)
und [der Runtime-Dispatch](../../src/core_contracts/runtime.py).

## Routen mit Arbeit außerhalb des Hauptprozesses

| Methode und Route | Verarbeitung und Ergebnis |
| --- | --- |
| POST `portable/export`, `portable/preview` | Paket lesen und serialisieren bzw. gegen einen unveränderlichen Stand prüfen. Originale Prüfsummen und Auflösungen beibehalten. |
| POST `portable/stage` | Importpaket streng parsen, Plan und Entwurf vorbereiten. Übernehmen schreibt Entwurf und Importbeleg zusammen. Der anschließende Import in den aktiven Stand verwendet den vorhandenen Entwurfsabschluss und die Aktivierung unten. |
| GET `catalogs`, `catalogs/entry`, `catalogs/entries`, `catalogs/history`, `catalogs/revision` | Definitionen, Einträge, Seiten und Revisionen aus derselben Sicht lesen und aufbereiten. |
| GET `catalogs/care`, `catalogs/care/queue`, `catalogs/care/detail`, `catalogs/care/inspect`, `catalogs/care/export` | Pflegeübersichten einschließlich „Alle Kataloge“, Suche, Facetten, Varianten und Strukturexport im Verarbeiter aufbereiten. |
| POST `catalogs/classes/migration` · GET `catalogs/classes/rollback`, `catalogs/classes/outputs` | Paket, Hindernisse, Vorschau, Rücknahmebestand und auswählbare Vertragsausgaben aus einer Sicht bilden. Übernahme und Rücknahme laufen über portable Stage/Entwurfsaktivierung. |
| POST `catalogs/preview`, `catalogs/care/preview`, `catalogs/scope/preview`, `catalogs/cleanup/preview` | Einzelpflege, Pflege im Zusammenhang, Geltungsbereich und Bereinigung vorbereiten. |
| POST `catalogs/entries`, `catalogs/care/write`, `catalogs/scope/apply`, `catalogs/cleanup/apply` | Änderung mit Revisionsbezug vorbereiten. Übernehmen prüft die betroffenen Katalogköpfe, Planbindung und Belege. |
| GET `title-sync/mapping`, `title-sync/page`, `title-sync/diff`, `title-sync/requests` | Zuordnung, Planseiten, Unterschiede und Belege ausarbeiten bzw. lesen. |
| POST `title-sync/preview`, `title-sync/apply`, `title-sync/cancel` | TC lesend abgleichen und Plan versiegeln, bestätigen oder im Auftragsjournal abbrechen. Übernehmen bindet Katalog- und Zuordnungsrevision, Plan und Beleg. |
| POST `title-import/preview`, `title-import/stage` | Übergangsformat im Verarbeiter einlesen und Paket/Entwurf vorbereiten. Übernehmen wie bei `portable/stage`. |
| GET `history/query/{kind}`, `history/rollups`, `history/{id}` | Historienabfragen mit eigenem Lesebudget und eigener Verbindung. `kind` umfasst `contracts`, `observations`, `transitions`, `episodes`, `deadlines`, `commands`, `gaps`, `diagnostics`, `revisions`, `activations`. Bestehende Filter, Cursor und Antwortformate bleiben erhalten. Alte unpaginierte Abfrage intern abschnittsweise streamen. |
| POST `history/retention/preview`, `history/retention/run` | Aufbewahrungsplan bilden. Beim Ausführen Löschabschnitte mit begrenzter Größe unter der bestehenden Aufbewahrungsregel durch den Schreiber übernehmen. |
| GET `diagnostics/problems`, `diagnostics/contracts/{id}`, `sources/calendar-status`, `evidence/{id}`, `evidence/reference/{id}` | Befunde, Eingangsdiagnose, Fingerabdrücke und Belegsuche auf einer festen Sicht erzeugen. Die Diagnose verwendet eine vom Schreiber erfasste Uhrzeit. Beleg- und Aufbewahrungsfehler behalten ihre Bedeutung. |
| GET `definitions`, `types`, `capabilities`, `registry/schema`, `sources`, `bindings` | Umfangreiche Listen, Schema und Merkmale vorbereiten. Statische Ergebnisse kann der Arbeitsweg vorab erzeugen und das HTTP-Frontend ausliefern. |
| GET `ha/entities`, `ha/contexts`; POST `registry/freshness`, `registry/freshness/proposals` | Umfangreiche Suche, Sortierung, Definitionen und Vorschläge aus einem begrenzten Lesebestand vorbereiten. Für HA-Entitätensuche zugelassene Metadaten über den bestehenden Adapter beziehen. Aktualisierung der Gebietsliste geht als geprüfter Schreibauftrag zurück. Die Anmeldung `POST ha/contexts` bleibt im Wahrheitsweg. |
| GET `editorial`; PUT `editorial` | Redaktionelle Liste und Änderung vorbereiten. Übernehmen prüft die redaktionelle Version. |
| GET `registry/active`, `registry/active/export`, `registry/revisions`, `registry/revisions/{id}`, `registry/activation-requests/{id}` | Registerdokumente, Revisionslisten und gespeicherte Belege lesen, aufbereiten und ausliefern. |
| POST `registry/validate`; GET, POST `registry/drafts` | Validierung, Entwurfsliste und Anlegen vorbereiten. Anlegen erhält einen atomaren Entwurfsbeleg. |
| GET, PUT, DELETE `registry/drafts/{id}`; POST `registry/drafts/{id}/validate`, `registry/drafts/{id}/activation-preview`, `registry/drafts/{id}/complete`, `registry/drafts/{id}/reconcile`, `registry/drafts/{id}/continuations` | Entwurf, Vergleich, Validierung und Fortsetzung vorbereiten. Entwurfs-/Sitzungsversion und Bearbeitungsrechte beim Schreiben neu prüfen. |
| GET `registry/continuations/{id}`; PUT `registry/continuations/{id}/decisions`; POST `registry/continuations/{id}/finish`, `registry/continuations/{id}/cancel` | Fortsetzung lesen oder Änderung der Entscheidungen und Abschluss vorbereiten. Übernehmen prüft Sitzungsrolle, -version und Basis. |
| POST `registry/drafts/{id}/activate`, `registry/rollback` | Revision und Auswertungsplan vorbereiten. Nur der Wahrheitsschreiber prüft den aktuellen Stand, schaltet die aktive Revision und aktualisiert davon abhängige Wahrheit. |
| GET `manual-restarts/preview/{id}` | Vorschau bei gleicher Gültigkeitsprüfung aus der unveränderlichen Sicht berechnen. Die tatsächliche Auslösung bleibt ein zeitkritischer Eingang. |
| PUT `operations/settings`; POST `operations/settings/reset` | Bestehende Eingabeprüfung, atomare Dateioperationen und Vorbelegung im Koordinator. Wirksamkeit nach bisheriger Neustartregel. |
| POST `setup/reachability`, `setup/provision`, `setup/import`, `setup/connect`, `setup/bridge_configure`, `setup/finish`; GET `setup/bridge` | Eigenständiger privilegierter Einrichtungsweg ohne Zugriff aus dem allgemeinen Pool. Umfangreiche Vorprüfung und Datei-/Netzarbeit außerhalb der Hauptschleife. Initiale Wahrheit nur über denselben Schreiber. Bestehende Freigaben und Schutzregeln gelten. |

## Interne Operationen und periodische Arbeit

| Operationen aus b32 | Ziel |
| --- | --- |
| `portable_export`, `portable_preview`, `portable_stage`, `title_preview`, `title_stage` | Paketverarbeitung im Verarbeiter. `stage` speichert am Ende einen geprüften Entwurf. Der Klassen-Umzug verwendet Export und Vorschau auf **derselben** Sicht. |
| `catalog_revision`, `catalog_preview`, `catalog_care_preview`, `catalog_care_write`, `catalog_write`, `catalog_scope_preview`, `catalog_scope_apply`, `catalog_cleanup_snapshot`, `catalog_cleanup_preview`, `catalog_cleanup_apply` | Sicht und Aufbereitung im Arbeitsweg. Bei Anwendung prüft der Schreiber Kopf, Eingabeversionen und Beleg. |
| `title_sync_preview`, `title_sync_apply` | Lesender TC-Abruf, Abgleich, Plan und Diff im Arbeitsweg. Schreibschritt mit erneuter Kopf-, Zuordnungs-, Plan- und Belegprüfung. |
| `validate`, `draft_preview`, `draft_complete`, `draft_reconcile`, `draft_create`, `draft_update`, `draft_delete`, `continuation_start`, `continuation_decisions`, `continuation_finish`, `continuation_cancel`, `activate`, `rollback` | Vorbereitung im Arbeitsweg. Entwürfe, Sitzungen und aktive Revision nur seriell übernehmen. `continuation_` darf in der Prüfung keine pauschale Freigabe für neue Operationen werden. |
| `editorial_put`, `editorial_area_refresh` | Redaktion und Gebiete vorbereiten, Änderung und Version seriell übernehmen. |
| `retention_preview`, `retention_run`, planmäßige Historienpflege aus `tick` | Zählen, Auswahl und Trockenlauf in den Arbeitsweg. Der Schreiber überprüft und übernimmt jeweils einen begrenzten Abschnitt. Alarmplanung gibt die Wartung frei und wartet nicht darauf. |
| `manual_restart_preview` | Simulation im Arbeitsweg. |
| `management_snapshot`, `management_commit`, `slots.SUBMIT`, `slots.SUBMIT_PREFIXES` | Heute interne Übergabe und Thread-Vorbereitung. A2 ersetzt sie durch eine feste Sicht und typisierte Übernahmeaufträge. Der Autorisierungspfad darf nicht von der HTTP-Route abhängen. |
| Periodische Katalogpflege ohne Einfluss auf Auswertung, Auftragsartefakte und Bereinigungsplanung | Auswahl und Zusammenfassung im Arbeitsweg. Hintergrundwartung weicht interaktiven Aufträgen. |
| `observation` und `observation_block`, `snapshot`, `snapshot_complete`, `gap`, `recover`, `bridge_state`, `mqtt_state`, `tick` für fachliche Fristen, `catalog_tick` für fällige Katalogbefunde, `command`, `context_register`, `engine_context_register`, `manual_source_enter`, `manual_restart`, `legacy_refresh_prepare`, `service_claim`, `service_complete`, `audit` | Im seriellen Wahrheitsweg. `catalog_tick` schließt die Wiedergabe-Kandidaten fristgerecht ab. `legacy_refresh_prepare` ist der interne Refresh-Plan für eine Quelle im bestehenden Adapter. HA-, MQTT- und Kalenderadapter, Wiederanlauf, fristgebundene Hooks und Publikation bleiben auch bei pausiertem Arbeitsweg aktiv. |

## Erlaubte schlanke Routen im Hauptprozess

| Methode und Route | Grenze |
| --- | --- |
| GET `/health/live`, `/health/ready`, `info`, `system/state`, `diagnostics`, `reasons`, `operations/settings`, `setup/status` | Gesundheitszustand, feste Metadaten und vorbereitete Betriebssicht ausliefern. Metriken begrenzen, keine bestandsabhängigen Scans in diesen Routen. |
| GET `contracts`, `contracts/{id}`, `snapshot`, `/api/v1/ws` | Bestehende Wahrheitsleser behalten dieselbe Sicht und dieselben Antwortformate. Einen festen Publikationsstand referenzieren und große Antworten mit begrenzten Abschnitten ausliefern. Langsame Leser begrenzen, ohne den Schreiber zu blockieren. |
| POST `commands`, `ha/contexts`, `manual-restarts`, `manual-sources/{id}/observations`; GET `commands/{id}`, `manual-sources/{id}/requests/{request_id}` | Direkte, zeitkritische Eingänge und gezielte Belege. Zulassung, Reihenfolge und Gültigkeitsregeln gelten unabhängig von der Belegung des Arbeitswegs. |
| PUT `operations/projection` | Bestehende bewusste manuelle Steuerung. Der Lastschutz ruft sie nie auf. |
| GET `tokens`; POST `tokens/rotate` | Sicherheitskritischer Kontrollpfad. Kurze Autorisierungs-/Audit-Schritte bleiben im Hauptprozess. Dateiarbeit auslagern, Tokenwerte bleiben im dafür vorgesehenen Pfad. |
| GET `operations/wait` | Verträglichkeitsroute für ältere Oberflächen. Für gültige Anfragen `204` ohne Wartezyklus, neue Oberfläche reicht den Auftrag direkt ein. |
| GET `/{path}` für statische App-Dateien sowie generierte HEAD-Routen, HTTP-Fehler und Middleware | Statische Dateien asynchron streamen. Authentifizierung, Begrenzung und Fehlerantworten bleiben schlank. Keine Dateivorschau und kein großer Parser in der Middleware. |

Auf Arbeitsrouten prüft der HTTP-Eingang Identität, Berechtigung, Metadaten und
bestehende Größenlimits. Er streamt Nutzlasten in den Arbeitsweg. Komplexe
Antworten und große Dateien werden dort kodiert. Die Tabelle ist die
Ausgangsliste für die [Architekturprüfung](wahrheit-hat-vorfahrt-a1.md#ci-architekturprüfung).
Die bisherigen Endpunkte für Maschinen und Traber bleiben kompatibel.
