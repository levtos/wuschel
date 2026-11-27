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

## Pflichtumfang im Arbeitsweg

Portable, Titelübernahme, Klassenpflege, Katalogpflege-Listen und -Vorschauen
einschließlich „Alle Kataloge“, TC-Abgleich, Verlauf mit Aufbewahrung,
Problemliste und Aktualitätsvorschau werden verlagert. Hinzu kommen
Vorbereitung und Validierung von Entwürfen. Andere Routen bleiben im
Hauptprozess. Eine spätere Verlagerung setzt bestandsabhängigen Aufwand
oder einen gemessenen p95 über 50 ms beim doppelten Bestand voraus. Die
CI prüft das Budget für jeden verbleibenden Pfad.

| Methode und Route | Verarbeitung und Ergebnis |
| --- | --- |
| POST `portable/export`, `portable/preview` | Paket lesen und serialisieren bzw. gegen einen unveränderlichen Stand prüfen. Originale Prüfsummen und Auflösungen beibehalten. |
| POST `portable/stage` | Importpaket streng parsen, Plan und Entwurf vorbereiten. Übernehmen schreibt Entwurf und Importbeleg zusammen. Der anschließende Import in den aktiven Stand verwendet den vorhandenen Entwurfsabschluss und die Aktivierung unten. |
| GET `catalogs/entries` | Bestandsabhängige Katalogliste aus einer festen Sicht aufbereiten. |
| GET `catalogs/care`, `catalogs/care/queue`, `catalogs/care/detail`, `catalogs/care/inspect`, `catalogs/care/export` | Pflegeübersichten einschließlich „Alle Kataloge“, Suche, Facetten, Varianten und Strukturexport im Verarbeiter aufbereiten. |
| POST `catalogs/classes/migration` · GET `catalogs/classes/rollback`, `catalogs/classes/outputs` | Paket, Hindernisse, Vorschau, Rücknahmebestand und auswählbare Vertragsausgaben aus einer Sicht bilden. Übernahme und Rücknahme laufen über portable Stage/Entwurfsaktivierung. |
| POST `catalogs/preview`, `catalogs/care/preview`, `catalogs/scope/preview`, `catalogs/cleanup/preview` | Einzelpflege, Pflege im Zusammenhang, Geltungsbereich und Bereinigung vorbereiten. |
| POST `catalogs/entries`, `catalogs/care/write`, `catalogs/scope/apply`, `catalogs/cleanup/apply` | Änderung mit Revisionsbezug vorbereiten. Übernehmen prüft die betroffenen Katalogköpfe, Planbindung und Belege. |
| GET `title-sync/mapping`, `title-sync/page`, `title-sync/diff`, `title-sync/requests` | Zuordnung, Planseiten, Unterschiede und Belege ausarbeiten bzw. lesen. |
| POST `title-sync/preview`, `title-sync/apply`, `title-sync/cancel` | TC lesend abgleichen und Plan versiegeln, bestätigen oder im Auftragsjournal abbrechen. Übernehmen bindet Katalog- und Zuordnungsrevision, Plan und Beleg. |
| POST `title-import/preview`, `title-import/stage` | Übergangsformat im Verarbeiter einlesen und Paket/Entwurf vorbereiten. Übernehmen wie bei `portable/stage`. |
| GET `history/query/{kind}`, `history/rollups`, `history/{id}` | Historienabfragen mit eigenem Lesebudget und eigener Verbindung. `kind` umfasst `contracts`, `observations`, `transitions`, `episodes`, `deadlines`, `commands`, `gaps`, `diagnostics`, `revisions`, `activations`. Bestehende Filter, Cursor und Antwortformate bleiben erhalten. Alte unpaginierte Abfrage intern abschnittsweise streamen. |
| POST `history/retention/preview`, `history/retention/run` | Aufbewahrungsplan bilden. Beim Ausführen Löschabschnitte mit begrenzter Größe unter der bestehenden Aufbewahrungsregel durch den Schreiber übernehmen. |
| GET `diagnostics/problems` | Bestandsabhängige Problemliste auf einer festen Sicht erzeugen. |
| POST `registry/freshness`, `registry/freshness/proposals` | Aktualität und Vorschläge auf einer festen Sicht auswerten. Erforderliche Entitätsmetadaten liefert der bestehende HA-Adapter. |
| POST `registry/validate`, `registry/drafts/{id}/validate`, `registry/drafts/{id}/activation-preview` | Explizite Entwurfsvalidierung und Aktivierungsvorschau vorbereiten. |
| POST `registry/drafts/{id}/complete`, `registry/drafts/{id}/reconcile`, `registry/drafts/{id}/continuations` | Längere Entwurfsprüfung und Vorbereitung. Lesen, Anlegen und direktes Bearbeiten bleiben beim Hauptprozess. Schreibschritte prüfen Sitzung, Entwurf und Basis seriell. |
| PUT `registry/continuations/{id}/decisions`, POST `registry/continuations/{id}/finish` | Entwurfsvorbereitung für geänderte Entscheidungen und Abschluss. Übernehmen prüft Sitzungsrolle, -version und Basis. |

## Interne Operationen und periodische Arbeit

| Operationen aus b32 | Ziel |
| --- | --- |
| `portable_export`, `portable_preview`, `portable_stage`, `title_preview`, `title_stage` | Paketverarbeitung im Verarbeiter. `stage` speichert am Ende einen geprüften Entwurf. Der Klassen-Umzug verwendet Export und Vorschau auf **derselben** Sicht. |
| `catalog_preview`, `catalog_care_preview`, `catalog_care_write`, `catalog_write`, `catalog_scope_preview`, `catalog_scope_apply`, `catalog_cleanup_snapshot`, `catalog_cleanup_preview`, `catalog_cleanup_apply` | Sicht und Aufbereitung im Arbeitsweg. Bei Anwendung prüft der Schreiber Kopf, Eingabeversionen und Beleg. |
| `title_sync_preview`, `title_sync_apply` | Lesender TC-Abruf, Abgleich, Plan und Diff im Arbeitsweg. Schreibschritt mit erneuter Kopf-, Zuordnungs-, Plan- und Belegprüfung. |
| `validate`, `draft_preview`, `draft_complete`, `draft_reconcile`, `continuation_start`, `continuation_decisions`, `continuation_finish` | Vorbereitung und Validierung im Arbeitsweg. Schreibschritte bleiben seriell. `continuation_` ist keine pauschale Freigabe für neue Operationen. |
| `editorial_put`, `editorial_area_refresh`, `catalog_revision`, `draft_create`, `draft_update`, `draft_delete`, `continuation_cancel`, `manual_restart_preview` | Hauptprozess, mit dem Routenbudget. Direkte Entwurfs- und Redaktionsänderungen bleiben auch ohne Koordinator verfügbar. |
| `activate`, `rollback` | Bestehende b32-Semantik im seriellen Wahrheitsschreiber. Die eigentliche Schreiberarbeit wird gesondert auf Schleifenverzug gemessen. |
| `retention_preview`, `retention_run`, planmäßige Historienpflege aus `tick` | Zählen, Auswahl und Trockenlauf in den Arbeitsweg. Der Schreiber überprüft und übernimmt jeweils einen begrenzten Abschnitt. Alarmplanung gibt die Wartung frei und wartet nicht darauf. |
| `management_snapshot`, `management_commit`, `slots.SUBMIT`, `slots.SUBMIT_PREFIXES` | Schlanke Übergabe und serielle Übernahme. Schwere Fälle erhalten eine feste Sicht und typisierte Aufträge, leichte Fälle bleiben lokal. Der Autorisierungspfad darf nicht von der HTTP-Route abhängen. |
| Periodische Katalogpflege ohne Einfluss auf Auswertung, Auftragsartefakte und Bereinigungsplanung | Auswahl und Zusammenfassung im Arbeitsweg. Hintergrundwartung weicht interaktiven Aufträgen. |
| `observation` und `observation_block`, `snapshot`, `snapshot_complete`, `gap`, `recover`, `bridge_state`, `mqtt_state`, `tick` für fachliche Fristen, `catalog_tick` für fällige Katalogbefunde, `command`, `context_register`, `engine_context_register`, `manual_source_enter`, `manual_restart`, `legacy_refresh_prepare`, `service_claim`, `service_complete`, `audit` | Im seriellen Wahrheitsweg. `catalog_tick` schließt die Wiedergabe-Kandidaten fristgerecht ab. `legacy_refresh_prepare` ist der interne Refresh-Plan für eine Quelle im bestehenden Adapter. HA-, MQTT- und Kalenderadapter, Wiederanlauf, fristgebundene Hooks und Publikation bleiben auch bei pausiertem Arbeitsweg aktiv. |

## Routen im Hauptprozess

| Methode und Route | Grenze |
| --- | --- |
| GET `/health/live`, `/health/ready`, `info`, `system/state`, `diagnostics`, `reasons`, `operations/settings`, `setup/status` | Gesundheitszustand, feste Metadaten und vorbereitete Betriebssicht ausliefern. Metriken begrenzen, keine bestandsabhängigen Scans in diesen Routen. |
| GET `definitions`, `types`, `capabilities`, `registry/schema`, `sources`, `bindings` | Listen, Schema und Merkmale lokal ausliefern. Bestandsabhängige Vorarbeit vermeiden und das Routenbudget prüfen. |
| GET `editorial`; PUT `editorial` | Redaktionelle Liste und Änderung lokal. Die redaktionelle Versionsprüfung gilt weiter. |
| GET `registry/active`, `registry/active/export`, `registry/revisions`, `registry/revisions/{id}`, `registry/activation-requests/{id}` | Registerdokumente, Revisionslisten und gespeicherte Belege lokal ausliefern. |
| POST `registry/drafts/{id}/activate`, `registry/rollback` | Die bestehende b32-Aktivierung und das Register-Rollback laufen unverändert im seriellen Wahrheitsschreiber. Die Vorbereitung erfolgt über die expliziten Entwurfsrouten oben. |
| GET `manual-restarts/preview/{id}` | Bestehende lokale Einzelvorschau. Die Auslösung bleibt ein zeitkritischer Eingang. |
| PUT `operations/settings`; POST `operations/settings/reset` | Bestehende Eingabeprüfung, atomare Dateioperationen und Vorbelegung im Hauptprozess. Wirksamkeit nach bisheriger Neustartregel. |
| POST `setup/reachability`, `setup/provision`, `setup/import`, `setup/connect`, `setup/bridge_configure`, `setup/finish`; GET `setup/bridge` | Bestehender privilegierter Einrichtungsweg im Hauptprozess. Initiale Wahrheit nur über denselben Schreiber. Freigaben und Schutzregeln gelten weiter. |
| GET `catalogs`, `catalogs/entry`, `catalogs/history`, `catalogs/revision` | Bestehende Katalogdefinitionen, Einzelansichten und Revisionsseiten lokal ausliefern. |
| GET `diagnostics/contracts/{id}`, `sources/calendar-status`, `evidence/{id}`, `evidence/reference/{id}` | Gezielte Diagnose und Belegsuche lokal. Die bestehenden Beleg- und Aufbewahrungsfehler gelten weiter. |
| GET `ha/entities`, `ha/contexts` | Entitätensuche und Herkunftsübersicht lokal. Zulässige HA-Metadaten liefert der vorhandene Adapter. |
| GET, POST `registry/drafts`, GET, PUT, DELETE `registry/drafts/{id}`, GET `registry/continuations/{id}`, POST `registry/continuations/{id}/cancel` | Bedienung, Bearbeiten und Abbruch bleiben bei ausgefallenem Koordinator verfügbar. |
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
