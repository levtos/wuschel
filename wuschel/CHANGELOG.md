# Changelog

## 1.0.0b5 — Nachweise, HA-Sensoren und Verlauf (vorbereitet)

- Identische retained-Messungen verdrängen nach einem Neustart keinen gültigen Gerätenachweis; Alterung und Fristen bleiben unverändert.
- Verträge können einzeln als HA-Sensor über Traber 1.0.0b5 angezeigt werden. Unbekannte Werte bleiben unbekannt.
- Verlauf mit 24 Stunden / 7 Tagen, Schema-Reihenfolge für Aufzählungen und Zeitpunkt/Wert/Status am Messpunkt.
- Store-Angebot erst nach veröffentlichten und anonym geprüften Images; Veröffentlichung übernimmt die vollständig grüne CI für identischen Inhalt.
- Produktionsabhängigkeiten blockieren das Audit-Gate, Entwicklungsabhängigkeiten erscheinen als Warnung.

## 1.0.0b4 — Aktualitätsvorlagen

- Gemeinsame und eigene Aktualitätsregeln, bestätigte Sensorarten und nachvollziehbare Weiterführung bei Regeländerungen.
- Fehlercodes bei beschädigten Altentwürfen ohne Ausgabe privater Inhalte; belastbare Wartepunkte in den E2E-Tests.
- Testabhängigkeit undici auf 8.12.3 aktualisiert.

## 1.0.0b3 — Entwürfe und sichere Übernahme

- Entwürfe können veraltete Grundlagen nicht still ersetzen; Übernahme und Importbeleg werden atomar gespeichert.
- Altentwürfe werden ohne Löschen migriert und bei fehlenden Belegen zur Prüfung erhalten.

## 1.0.0b2 — HA-Verbindungsaufbau

- Streng steigende WebSocket-IDs, eindeutige Zuordnung von Antworten und sichere Fehlerdiagnosen.

## 1.0.0b1 — Öffnungen und HA-Verbindung

- Vier Öffnungen und drei Zonen können mit geprüften Pakettypen konfiguriert werden.
- Direkte MQTT-Geräte-Evidence mit Heartbeat, asymmetrischer Fusion und unverlängerlicher Grace.
- Sicher begrenzte Wiederbewertung beim Wuschel-Neustart; Retain und Replay schaffen keinen neuen Nachweis.
- Lebenszeichen-Grenzen im bestehenden Vertragsformular und neue Kategorie Öffnungen.
- Erste veröffentlichte Öffnungs-Beta mit Traber 1.0.0b1.

## 1.0.0a10 — Wuschel / Traber (A2, vor Release)

- Neue Produktnamen, Slug wuschel und Domain traber, Protokoll 2 und lokale Brandbilder.
- Einhorn-Herde mit unveränderten Originalen in Control und reproduzierbaren Ableitungen.
- Verwaltete Kataloge mit Master/Varianten, sofortiger atomarer Pflege und gezielter Neubewertung.
- Ein Menü Import & Export, vollständige Vertragsabschlüsse, Kategorien/Räume und Kataloginhalte.
- Alle App-Optionen in Deutsch/Englisch, sicherer MQTT-Modushinweis und Technische Details.
- Neue Datenbank nur über Bennis vorbereiteten, nicht ausgeführten Teil-B-Block.
- Titelkatalog-Planer nur lesend; ein Bestandsimport braucht ein eigenes späteres Go.


## 1.0.0a8 — Mimir (vorbereitet, 2026-10-17)

- Neuer App-Slug mimir und Bifröst-Domain bifrost; eigene Installation, Terra bleibt erhalten.
- Instanzparameter, lokale Uhr/Kalender, n-Klassen, Textregeln und benannte Formeleingänge.
- Lebenszyklus für Versionen, eigene Vollständigkeitsachse und normale Freshness bei HA-Ausfall.
- Referenzbewusste Aufbewahrung/Verdichtung und begrenzte Teilbewertung mit Kapazitätsnachweis.
- Verständliche Texte, fortgeführter Ja/Nein-Verlauf und Quellennamen bei Typsperren.
- [Alpha-8-Nachweise](https://github.com/levtos/mimir/blob/main/docs/alpha8/ABNAHME.md).
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
