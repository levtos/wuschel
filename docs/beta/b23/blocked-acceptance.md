# b23 – offener Abnahmestand vom 03.11.2026

Auftrag: [#100](https://github.com/Levtos/wuschel/issues/100), `agent:codex`.
Die Beta ist **nicht freigegeben und nicht veröffentlicht**. Kein Live-Zugriff.
Die Umsetzung wurde nach drei erfolglosen Korrekturversuchen am Lastkriterium
gemäß Bennis Arbeitsgrenze angehalten. Es gibt keinen weiteren automatischen
Testversuch, keinen Merge und kein Release.

## Gesicherter Bauzweig

`codex/100-b23-stability`, zunächst paketweise gepusht:

| Paket | Commit | Inhalt |
| --- | --- | --- |
| P1 | `58c5d8a` | Koaleszenz ohne Overflow-Reconnect, Projektions-Retry, Snapshot-Portionen, Verwaltungsworker/OCC, Traber-Karenz |
| P2 | `7f6acae` | Revisions-/Prüfcache und datensparsame Messinstrumente; Analyse-Draft #101 geschlossen |
| P3 | `e93b640` | Stabile Primärfelder, HA-Formate und Engine-Dokumentation |
| P4 | `9a6cf84` | Vollständiger Kalenderhorizont und konkrete Lücken über vier Monate |
| P5 | `65e2720` | Testtypen nur bei explizitem Testaufruf, stiller Dialogabbruch und Vorbereitungs-Timeout |

Der abschließende WIP-Commit enthält den Abnahmetest, die Wiederanlaufprüfung,
den auf fünf Sekunden begrenzten HA-Backoff und die letzten Änderungen zur
Wiederverwendung geparster Beobachtungen und unveränderter Zustandszeilen.
P1 ist trotz vorhandener Implementierung **nicht abgenommen**.

## Nachweise und offenes Gate

Synthetische Lastgröße: 100 öffentliche deklarative Verträge, 90 HA-Quellen,
elf aktivierte Revisionen, sieben verwaltete Kataloge mit 11.200 Einträgen.
Die Kataloge entstehen durch strukturell und fachlich geprüfte begrenzte
Schreibanfragen auf einer isolierten Fixture; keine privaten Daten.

| Pflichtkriterium | Lokaler Befund |
| --- | --- |
| 50 Beobachtungen/s während 30 s CPU-Verwaltungsarbeit, p95 <1 s | **Nicht erfüllt**. Letzter Lauf: 1.500 Ereignisse geplant; bis zum Messpunkt 248 Beobachtungen im Runtime-Prozess gezählt, Queue 0, Koaleszenzpuffer 0, p95 der verarbeiteten Beobachtungen 24,724358 s. Der anschließende begrenzte Abschluss-Warteweg läuft ins Timeout. Kein Nachweis eines tatsächlichen Durchsatzes von 50/s. |
| Keine Bridge-Trennung/Endlosschleife im letzten Lastlauf | Eine Verbindung; das Latenzkriterium bleibt trotzdem rot. Projektions-Retry wurde protokolliert. |
| Reload/Neustart, 100 Entitäten und unveränderte IDs <10 s | Synthetischer Wiederanlauf im echten HA-Kernel grün: Bridge-Reload bzw. WebSocket-Abbruch und leere Projektionscaches, alle 100 Sensoren wieder `on`/`off`, dieselben IDs. Kein Live-HA-Neustart. |
| Verwaltungszeiten jeweils <2 s | Lokaler MemoryStore-Lauf: Prüfung 0,4409265 s, Vorschau 0,5419011 s, Aktivierungsvorbereitung 1,1302884 s. Diese Zahlen stammen vor den letzten Live-Auswertungsänderungen. |
| Bestehende vollständige Suite | Nicht ausgeführt; vollständiges CI-/Release-Gate bleibt offen. |

Gezielte Nachweise: 13 HA-Kerneltests mit Python 3.14.5,
Home Assistant 2026.10.0, asyncpg 0.32.0, Pydantic 2.12.5,
pytest 9.0.2 und pytest-asyncio 1.3.0 grün. Nach den letzten Änderungen
84 betroffene Backendtests grün, außerdem Ruff und striktes mypy.
Die lokalen PostgreSQL-Tests können mangels isolierter PostgreSQL-Instanz
nicht als geprüft gelten; ihre vollständige CI-Ausführung steht aus.

Die drei fehlgeschlagenen Lastläufe betreffen denselben Blocker. Zuerst wurde
das doppelte Parsen historischer/aktueller Verwaltungsdaten entfernt; anschließend
wurden Beobachtungen innerhalb einer Auswertung gemeinsam geparst und schließlich
unveränderte Live-Zeilen mit Copy-on-write wiederverwendet sowie ungenutzte
Eingangsberechnungen bestehender Wiederverwendungspfade entfernt. Die betroffenen
Gleichheits-, Zeit- und Reihenfolgetests bleiben grün. Das Pflichtziel wird
weiterhin verfehlt. Daraus wird keine zusätzliche Ursache als bewiesen abgeleitet.

## Sicherheits- und Fortsetzungsgrenzen

- Aktivierungen aus einem veralteten Zustand werden per OCC abgelehnt. Eine
  zwischenzeitliche Live-Schreibtransaktion kann eine vorbereitete Aktivierung
  veralten lassen; die Vollständigkeit/Atomarität wird dafür nicht gelockert.
- Vor Fortsetzung müssen der offene Lastpfad und die vollständige Regression
  abgeschlossen werden. Kein Produktions-, Store- oder Live-Nachweis liegt vor.
- Versionsdateien/Marker nennen lediglich den **Kandidaten** 1.0.0b23. Der
  angebotene Store-Stand bleibt 1.0.0b22. Es gibt kein b23-Tag und kein Release.
- Eigene Fixture-/Typ-/Formatierungsfehler wurden berichtigt. Bestehende
  Erwartungen für sofortiges `unavailable`, Timeout-Reconnect, einen einzigen
  großen Snapshot und Abdeckung durch einen einzelnen entfernten Termin wurden
  ausschließlich wegen des ausdrücklich geänderten b23-Vertrags angepasst.
- Keine App-Optionen abgefragt, keine Geheimnisse ausgegeben, keine privaten
  Daten veröffentlicht und `benni_core_contracts` nicht verändert.

Weitere technische Umsetzung ist angehalten. Benni und Claude behalten die
Live-Abnahme und Übernahme; sie wurden durch diese Arbeit nicht vorweggenommen.
