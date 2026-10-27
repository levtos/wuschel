# Changelog

## 1.0.0b14 — Beobachtungen erhalten, Schlaf und Wecken lesbar anzeigen

- Unveränderte HA-/MQTT-Verknüpfungen bleiben bei einer Übernahme im laufenden Prozess verbunden. Erhaltene Beobachtungen behalten Messzeitpunkt, Herkunft und Ablauf; geänderte Eingänge, Neustart und bekannte HA-Lücken verlangen weiter frische Meldungen.
- Schlafzustand und Weckplan zeigen nur die Kernwerte in der Liste, sämtliche Angaben bleiben im Detail. Deutsche Feld-/Werttexte und Vorlagennamen; interner Schlafablauf eindeutig gekennzeichnet.
- Reine Anzeigenamen-Änderungen sind sichtbare Metadaten-Korrekturen ohne fachlichen Importkonflikt. Das private Bereich-4-Korrekturpaket enthält ausschließlich solche Namens-/Organisationsänderungen; Benni liest es selbst ein.
- Ausschließlich amd64, wiederverwendbare Docker-Stufen, betroffene CI-Prüfungen und ein vollständiges Release-Gate. Fehlgeschlagene Jobs einzeln wiederholen; volle Suites ausschließlich in CI.
- Reguläre GitHub-Releases ohne Vorab-Markierung für HACS; Beta-Status weiterhin in der Versionsnummer. Keine DB-Migration; Traber unverändert auf 1.0.0b11.
- [Umsetzung und Nachweise](docs/beta/b14-observations-and-ci.md), [Veröffentlichung](docs/publishing.md).

## 1.0.0b13 — Zahlendarstellung bei Bearbeitung erhalten

- Gleichwertige JSON-Zahlen wie `840` und `840.0` werden beim Validieren, Speichern, Importieren und Übernehmen auf die gespeicherte Darstellung zurückgeführt. Bereits veröffentlichte Vorlagen bleiben dadurch auch nach dem Browser-Rundlauf unverändert gültig.
- Unveränderlichkeits- und Importvergleich sowie „Was sich ändert“ verwenden dieselbe typbewusste Gleichheit. Echte Wertänderungen, Bool- und Textwechsel bleiben verschieden; die Schema-Prüfung bleibt erhalten.
- `canonical`, `checksum`, gespeicherte Fingerabdrücke und Restore-Identitäten bleiben unverändert. Keine Datenbankmigration; Traber bleibt auf 1.0.0b11.
- [Verhalten und Nachweise](docs/beta/b13-numeric-equivalence.md). Der vorhandene Importentwurf benötigt keinen Neuimport; tatsächliches Übernehmen bleibt Benni vorbehalten.

## 1.0.0b12 — Importvorschau verständlich entscheiden

- Die Importvorschau bietet unten dauerhaft „Vorschau neu prüfen“ und erklärt direkt beim Einlesen, welche Entscheidung oder Prüfung noch fehlt. Offene Konflikte sind über einen Sprung erreichbar.
- Vollständige Konfigurationspakete können vorhandene Objekte unter derselben Kennung ausdrücklich aus der Datei übernehmen. Plan-Prüfsumme, Revisionsbindung und bewusste Übernahme des Entwurfs bleiben erhalten.
- Kategorie und Raum erscheinen nur für neue oder abweichende Angaben; unveränderte Angaben bleiben automatisch erhalten. Vertrag und Vorlage gleichen Namens sind unterscheidbar.
- Wuschel 1.0.0b12; Traber bleibt unverändert auf 1.0.0b11. Keine Datenbankmigration, Änderung der Aktualitätslogik oder Lockerung von Prüfsummen.

## 1.0.0b11 — Schlaf und Weckplanung (Kandidat)

- Regelsprache 6 ergänzt geordnete Übergänge, einzeln belegte Ereignisse, lokale Zeitspeicher und datumsabhängige Kalenderregeln. Alte Sprachversionen bleiben erhalten.
- Importierbare Schlaf- und Weckvorlagen samt originalgleichem Weckplan-Port; Mindestschlaf und manuelle Tagesausnahme werden darüber ausgewertet.
- Traber bietet `send_command` für einzelne HA-Befehle mit Wiederholungsschutz und ohne Offline-Warteschlange. Profile und Regeln sind über dieselben strukturierten Parameterformulare änderbar.
- Programm und Traber sind als b11-Kandidat vorbereitet; das Store-Angebot bleibt b10. Registry/Fingerabdruck 4, Paket/Protokoll 2 bleiben; keine Datenbankmigration.
- [A2 und Nachweise](docs/beta/bereich-4/A2.md). Zweites Review und Benni-Go vor Merge und Veröffentlichung.

## 1.0.0b10 — Kalenderabruf und Importdialog

- Traber übersetzt Kalenderfenster an der HA-Grenze in `start_date_time` und `end_date_time`; das Drahtprotokoll bleibt unverändert. Regression gegen den echten HA-Kalenderdienst.
- Fehlgeschlagene Abrufe melden einmal je Fehlerserie einen statischen Code; die Quellenansicht zeigt Abrufzeit, Ablaufzeit und Fehler ohne Termininhalt.
- Aktualitätsauswahl aktualisiert die Importvorschau automatisch. Unveränderte Quellen bleiben implizit erhalten; Sensortyp und Zuordnung sind optional.
- Wuschel und Traber tragen 1.0.0b10. Keine Datenbankmigration, Vorlagen- oder Paketänderung.

## 1.0.0b9 — Zeit, Tag und Sonne (Kandidat)

- Regelsprache 5 ergänzt Jahreskurven mit echten Kalendertagen und Kalendertreffer mit genauer Terminplanung ohne Abfragetakt.
- Stützpunkte lassen sich als Tabelle bearbeiten; Kalendertitel bleiben aus Ergebnissen, Begründungen und Traces heraus.
- Sieben importierbare Datenvorlagen und die Kategorie „Zeit, Tag und Sonne“. Private Verknüpfungen und Schwellen gehören ausschließlich zum lokalen Paket.
- Alte Sprachversionen und Fingerabdrücke bleiben erhalten. Traber bleibt auf 1.0.0b5; keine Datenbankmigration.

## 1.0.0b8 — HA-Neuverbindung nach Übernahme

- Ausstehende Projektionsantworten einer beendeten HA-Verbindung lösen keine weitere Neuverbindung auf der Ersatzverbindung aus. Der erste Snapshot nach einer Konfigurationsübernahme wird wieder verarbeitet.
- Zeitüberschreitungen der Projektion auf der aktuellen Verbindung lösen weiterhin eine Neuverbindung aus; beide Fälle sind synthetisch abgesichert.
- Traber bleibt unverändert auf 1.0.0b5. Keine Änderung an Vorlagen, Wahrheitslogik oder Datenbankschema.

## 1.0.0b7 — Anwesenheitsvorlagen

- Allgemeine Zusammenführung `first_usable`: Vorrang nach Argumentreihenfolge, ohne fehlenden Nachweis durch nicht zutreffende Fälle.
- Explizite HA-Gerätezeiten unterstützen Unix-Sekunden; fehlende oder ungültige Gerätezeit wird auch als Lebenszeichen abgewiesen.
- Neun importierbare Anwesenheitsvorlagen einschließlich freiem Ortsnamen; Zuhause und Eltern zählen für Ankunft, Abfahrt, Durchfahrt und Haushalt gleichwertig.
- Neue Eingangsübergänge beenden die Halteanzeige bei erneutem Start und verhindern verpasste Folgezyklen.
- Synthetischer Paketnachbau und Prüfungen; private Verknüpfungen und Fahrtmessung gehören ausschließlich in den lokalen Ausgabeordner.
- Traber bleibt unverändert auf 1.0.0b5. Kein automatischer Import oder Übernahme.
- [Design und Freigabe](docs/beta/bereich-2/A1.md). Release und Installation erst nach zweitem Review und Benni-Go.

## 1.0.0b2 — HA-Verbindung (A1+A2, unveröffentlicht)

- WebSocket-Anfragen verwenden streng steigende IDs je Verbindung; Reconnect beginnt neu.
- Snapshot-Events werden über die tatsächlich gesendete Subscription-ID zugeordnet.
- Strenges FakeHA und Regressionen für Connectivity, Snapshot, Reconnect, Discovery und Dienstaufrufe.
- Fehlerlogs ergänzen Anfragetyp und sicheren HA-Fehlercode ohne Nutzdaten.
- Traber bleibt unverändert bei 1.0.0b1; Release-Gate belegt seine Code-Identität zum Versionstag.
- [A1/A2 und Review-Gate](docs/beta/ha-connection-b2.md). Nach PR STOPP für Benni.

## 1.0.0b1 — Bereich 1 Öffnungen (A2, unveröffentlicht)

- Produktive Opening-Pakettypen mit asymmetrischer Kontaktfusion, Konfliktgründen und begrenzter Grace.
- Belegte MQTT-Geräteberichte behalten beim Prozessneustart ihre ursprüngliche Alterungsfrist; kein neuer Hold durch Restore.
- Zonen liefern getrennte Fakten mit eigenem Beginn; gehaltenes open bleibt offen, held ist nie all_closed.
- Lebenszeichen der gebundenen Quellen am Vertrag im Entwurf ändern; Kategorie Öffnungen ergänzt.
- Connectivity verwendet den vorhandenen Adapter. Keine Consumer-/Policy-Migration oder Live-Änderung.
- A2 endet am Review-PR; Veröffentlichung, Installation und Bennis Übernahme bleiben getrennte Gates.

## 1.0.0a8 — Wuschel (vorbereitet, 2026-10-17)

- Neuer App-Slug wuschel und Traber-Domain traber; eigene Installation, Terra bleibt erhalten.
- Instanzparameter, lokale Uhr/Kalender, n-Klassen, Textregeln und benannte Formeleingänge.
- Lebenszyklus für Versionen, eigene Vollständigkeitsachse und normale Freshness bei HA-Ausfall.
- Referenzbewusste Aufbewahrung/Verdichtung und begrenzte Teilbewertung mit Kapazitätsnachweis.
- Verständliche Texte, fortgeführter Ja/Nein-Verlauf und Quellennamen bei Typsperren.
- [Alpha-8-Nachweise](https://github.com/levtos/wuschel/blob/main/docs/alpha8/ABNAHME.md).
  Noch kein Release oder Installationsnachweis; Teil B erst nach Bennis Go.

## 1.0.0a7.post1 — Befunde aus dem UX-Live-Test (vorbereitet, 2026-10-17)

- Einheitlicher Serverzustand in Übersicht, Vertragsplakette und Filter; konkrete historische Typ-/Einheitskonflikte.
- Fehlerhafte Entwürfe mit erklärtem Strich im Änderungszähler; einheitenabhängige Rundung.
- Quellennamen, ein Hinzufügen-Einstieg, passende Aktualitätsfelder und Minutenhilfe.
- Breiter Verlauf mit beschrifteten Achsen und Ja/Nein-Stufen; betroffener Eingang und Wertalter in „Warum?“.
- Du-Form, verständliche Entwurfs-/Versions-/Konfliktsprache, 179 ungenutzte deutsche Schlüssel entfernt; App-Beschreibung aktualisiert.
- [Korrekturen und acht Firefox-Nachweise](docs/workbench/alpha7-post1/KORREKTUR.md), [Release-Vorbereitung](docs/releases/1.0.0a7.post1.md).
  Kein Merge, Release, Image-Publish oder Installation vor neuem Go. Alpha 8 bleibt getrennt.

## 1.0.0a7 — Terra nach Aufgaben bedienen (vorbereitet, 2026-10-17)

- UX-Nachprüfung K.01–K.06: Server-Änderungszähler, schlanke Entwurfsleiste, Vertragsnavigation, Alltagssprache, deutsche Zeitwerte, Minutenhilfe und Statusdarstellung korrigiert; 18 neue Chrome-/Firefox-Nachweise.

- Zweistufige Module und eigene Unterseiten, alte Links mit Weiterleitung; Bereitschaft
  mit Zähler und Lösungslinks; eine Übersicht mit Schnellaktionen, Vertragsproblemen und Ereignissen.
- Live ansehen / Bearbeiten, automatischer Entwurf am aktuellen Ort, sichtbares Prüfen und
  Übernehmen; Name freiwillig; ein Speichern. Versionen mit Zeit, aufgezeichnetem Akteur und Notiz.
- Vertragsliste mit Wert, Einheit und einem Zustandschip; Details mit Warum, Herkunft,
  Verlauf und Ablaufbefehlen. Import auf eigener Seite mit Prüfung vor Übernahme.
- Geräteführung aus Home Assistant samt Abhängigkeit, Quelle, Verknüpfung und ausdrücklicher
  Typumwandlung; veränderbare Aktualitätsvorlagen; einfache typkonsistente neue Vorlagen.
- Geprüfter Quellenumzug unter neuer Kennung; alte Quelle und Geschichte bleiben erhalten.
- Angenommene ADR 0004: administrativer Ablauf-Neustart nur bei Freigabe, mit Bestätigung,
  Auswirkungen, Grund, Audit, Versions-/Zustandsprüfung und manueller Kennzeichnung bis zum Datennachweis.
- Lesbare Zusammenhänge, Nachweis-Quellenauswahl und Tabellen; gedämpfte Farben,
  graues Unbekannt, geprüfte AA-Kontraste und deutsche Texte aus `de.json`.
- [Umfang, Upgrade und Grenzen](docs/releases/1.0.0a7.md),
  [vollständige Abnahmematrix](docs/workbench/alpha7/ABNAHME.md).
  Merge, Tag, Release, Image-Publish und Installation bleiben bis zu Bennis neuem Go gesperrt.

## 1.0.0a6 — Korrekturen nach UX12 (2026-10-16)

- HA-/Bridge-Status entspricht dem Bedarf der aktiven Konfiguration, auch nach Zurücksetzen.
- Typ und Einheit von Quellen bleiben ab der ersten Übernahme unveränderlich; Wertelisten
  dürfen wachsen. HA-Verknüpfungen erhalten eine ausdrückliche passende Typumwandlung;
  Altbestand kann sie per „Umwandlung ergänzen“ hinzufügen. Prüfung nennt Quelle und Eingang.
- Abläufe zeigen fehlende Neustart-Bedingungen und Warnungen bei konstant Nein. Nach einem
  Kontextwechsel erfolgt Wiederherstellung ausschließlich mit gültigen Daten und erfüllter
  Bedingung; zustandslose Befehle melden `state_unknown`. B2 bleibt ADR-0004-Entwurf.
- Sichtbare Dialogbestätigung, korrekte Konflikt-Kopie, aktuelle Version/Assistentenaussage,
  Neu-Synchronisierungszähler, Feldsprünge und benannte Kreisverweise korrigiert.
- ID-Grenzen, verständliche Import-/Entwurfslimit-Meldungen, angemessene Prüffehler-Logs,
  passende Auswahllisten und deutsche Zahlen-/Zeit-/Einheitenanzeige.
- Oberfläche vollständig über `de.json`/`t()`, vorbereitete leere `en.json`, deutsche
  Code-Tabellen, Terra/Einstellungen; AST-Sprachscan und JSX-Lint in CI. Keine neue Navigation.
- [Alpha-6-Umfang und Nachweise](docs/releases/1.0.0a6.md). K7 wird ausschließlich als
  Runbook vorgelegt. Merge, Tag, Pre-release und beide Images wurden nach Bennis Go
  veröffentlicht und anonym geprüft; Installation, DB-Reset und Live-Abnahme bleiben getrennte Gates.

## 1.0.0a5 — Terra Workbench (2026-10-14)

- New React 19 / TypeScript / Vite 8 / Tailwind 4 administration UI replacing the Svelte engineering UI:
  manual-first, capability-driven editors for all Alpha-4 definitions, rules, state machines, sources,
  bindings, catalogs and contracts; Registry Studio (drafts, validation, diff/impact, activation,
  rollback, OCC conflict handling); graph; diagnostics, history, explain, commands; administration.
- First-run assistant ported without functional or security changes.
- Frontend only: no backend, API, schema or legacy change. `dev/workbench_demo.py` is a local review aid.
- Independent review fixes T1B-01 through T1B-07 and UX-01: packaged frontend fixtures,
  safe draft selection, exact validated activation candidates, typed nested JSON values and
  catalogs, dynamic/recursive schemas, token cache isolation and direct text-catalog authoring.
- Additional corrections C1–C8: preserve edits across delayed saves; bind activation to draft
  version and active revision; shared request budget and 429 handling without write retries;
  typed enums/ref/allOf/null/open-object editing; honest incomplete explain data; sequenced
  live/resync/unknown states; member order/unknown metadata/node-ID preservation; saved freshness defaults.
- Flexible panels: collapsible fixed navigation, drag-and-drop and keyboard ordering, panel widths,
  installation-scoped layout persistence and reset without losing editor state or writing registry data.
- Release authorized by Benni; publication evidence is recorded in the GitHub Alpha pre-release.
  Visible Firefox/Touch, real HA-Ingress/Supervisor embedding, real discovery, accessibility and
  Live acceptance remain **not verified**. No automatic installation or registry activation.
- [Workbench evidence](docs/workbench/codex-review-fix.md) and [Alpha 5 release notes](docs/releases/1.0.0a5.md).

## 1.0.0a4 — Declarative Platform (prepared, 2026-10-12)

- Declarative Core Platform: closed, versioned rule language on the existing engines
  (three-valued boolean logic, compare, map, first-match, bucket, formulas, fusion,
  stable_for/dwell/grace/edge/delta/age) and data-defined state machines with typed commands.
- Manual-first authoring: draft CRUD with OCC and metadata, unsaved aggregate validation
  with JSON-Pointer issues, semantic diff and static impact, explicit activation, rollback,
  export and re-import through the same public APIs.
- Workbench APIs: capabilities and schemas, reason catalog, contract diagnostics and explain,
  bounded history with cursors, exact evidence resolution, read-only HA entity discovery,
  server time, readiness reasons on WebSocket, administrative audit.
- Alpha-3 compatibility: config schema 1, API/Bridge protocol 1, DB migrations and
  installation ID unchanged; pure v1 documents keep the released storage shape; declarative
  definitions require explicit `fingerprint_version: 2`. Downgrade only via a matching backup pair.
- Opus review corrections OPUS-A4-01–A4-17 and OPUS-A4-D2-01/D2-02 included; follow-ups F1
  (retention/indexes/production scale) and F2 (large graphs) remain open.
- Release preparation only; publication and HA/Supervisor Live acceptance remain separate gates.
- [Alpha 4 scope, publication and upgrade notes](docs/releases/1.0.0a4.md).

## 1.0.0a3 — Phase 1A (prepared, 2026-10-11)

- Reusable producer lifecycle, temporal/state-machine orchestration and multi-field contracts.
- Multistage graph/restore coverage, dependency monitoring, declared refresh and typed commands.
- Commands reject producer evaluation failures atomically; replay remains idempotent.
- Lifecycle regressions for semantic changes, removal, resume and ordered shutdown.
- MQTT-only readiness and visible diagnostics for invalid Supervisor options.
- Release preparation only; publication and Live acceptance remain separate gates.
- [Alpha 3 scope, publication and upgrade notes](docs/releases/1.0.0a3.md).

## 1.0.0a2 — First-Run-Wizard (2026-10-10)

- Ingress-only first-run wizard before database/identity, CSRF-protected setup API.
- Private resumable connection authority, local LXC provisioning and secret import.
- Existing Alpha identity/configuration retained, explicit initialize/adopt gates.
- Bridge onboarding through HACS with honest missing/restart ambiguity and release blocker.
- Explicit confirmed Bridge config-entry setup; persistent wizard completion separate from readiness.
- Alpha release authorized after Opus GO with limitations; no automatic live installation.
- Upgrade, backup, rollback and deferred review tasks: [Alpha 2](docs/releases/1.0.0a2.md).

## 1.0.0a1

Platform Alpha 1 Foundation, tracked in issue #1. See the app changelog and
`docs/platform-alpha1/completion-report.md` for exact evidence and open gates.
