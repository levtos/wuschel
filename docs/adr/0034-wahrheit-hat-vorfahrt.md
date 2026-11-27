# ADR 0034 – Wahrheit hat Vorfahrt

- Status **Freigegeben für A2** mit den [fünf Auflagen aus dem Claude-Review](https://github.com/Levtos/wuschel/pull/182#issuecomment-7226676193)
- Auftrag [Wuschel #180](https://github.com/Levtos/wuschel/issues/180)
- Ausgangspunkt Wuschel 1.0.0b32, Commit `baee49c`
- Details [Betriebsentwurf und Abnahme](../operations/wahrheit-hat-vorfahrt-a1.md) und [Routen und Operationen](../operations/wahrheit-hat-vorfahrt-routen.md)

## Ausgangslage

Die Arbeitsvorbereitung teilt heute Ereignisschleife, Python-Prozess und
teilweise die serielle Warteschlange mit der Wahrheit. Der Klassen-Umzug ruft
`portable_export` und `portable_preview` auf. `management.prepare` klont den
Stand in einem Thread und bezieht einzelne Daten über die Hauptschleife. Einige
Pflege- und Diagnoseabfragen filtern, sortieren oder serialisieren dort sogar
direkt. Bei Schutzbetrieb kann `operations/wait` mit einem Fehler abbrechen.
Der HA-Adapter verlängert in diesem Zustand das Sendeintervall der Projektion.
Die Auswirkungen sind in [#180](https://github.com/Levtos/wuschel/issues/180)
mit Bezug auf [#167](https://github.com/Levtos/wuschel/issues/167) belegt.

## Entscheidung

Wuschel hat genau **einen seriellen Wahrheitsschreiber**. Er bestimmt die
Reihenfolge der zugelassenen Eingänge, die aktive Registerrevision und die
Publikationsfolge. HA und MQTT, Kalenderbefunde, Fristen sowie Auswertung,
Commit und Projektion bleiben auf diesem Weg. Die vorhandene Semantik für
Ereignisblöcke, Wiederanlauf, Lücken, Befehle und Neuabgleich gilt weiter.
Geteilte Quellen, Vertragsabhängigkeiten, Katalogsichten und die HA-Projektion
brauchen denselben Stand. Schreiber pro Katalog oder Vertragsart würden dafür
eine verteilte Commit-Reihenfolge und neue Fehlerfälle an diesen Grenzen
einführen.

Der Arbeitsweg übernimmt die im Inventar festgelegten schweren Vorgänge.
Weitere Routen werden nur bei bestandsabhängigem Aufwand oder einem
gemessenen p95 über 50 ms im Hauptprozess bei doppeltem Bestand verlagert.
Für die verbleibenden Routen gilt das CI-Budget p95 ≤ 50 ms. Übersicht,
Verträge, Einstellungen und die Bedienung von Entwürfen bleiben bei
Ausfall des Koordinators verfügbar.

Ein eigener Koordinator betreibt hoch- und herunterfahrbare Unterprozesse.
Sie lesen einen unveränderlichen Stand und erstellen bei schweren
Schreibvorgängen versiegelte Pläne. Der serielle Schreiber prüft den
erwarteten Stand erneut und übernimmt den Plan atomar mit Beleg. Die
Vorbereitung und Validierung von Entwürfen wandert in den Arbeitsweg.
Die eigentliche Aktivierung und das Register-Rollback behalten die
Semantik aus b32 im Wahrheitsschreiber.

Die App verwendet ihre bestehende PostgreSQL-Rolle. Lesesitzungen der
Verarbeiter starten mit `default_transaction_read_only = on`.
Der Koordinator schreibt ausschließlich in die per normaler Migration
angelegten Tabellen für Auftragsjournal und Arbeitsartefakte.
Read-only-Sitzungen, eine feste Verbindungsobergrenze und Architekturtests
sichern diese Grenze.

Die drei Signale des Lastschutzes kommen ausschließlich aus dem Wahrheitsweg.
Der Schutz steuert die Freigabe von Arbeitsaufträgen. Die Grenzwerte ändern
weder Auswertungsreihenfolge noch HA-Sendeintervall. Interaktive Aufträge
erhalten eine dauerhafte, abbrechbare Warteschlange. Eine gemeinsame
Betriebssicht liefert den angezeigten Zustand, Grund, die verbleibende
Haltezeit und die Aufträge.

Verträge und Fachlogik gelten weiter. HA-Schnittstelle v1 bleibt eingefroren.
Betriebsangaben können additiv hinzukommen. Traber bleibt rückwärtskompatibel.
Die App heißt in dieser Beta Wuschel. [Prisma #181](https://github.com/Levtos/wuschel/issues/181)
ist ein eigener Auftrag.

## Folgen für die Umsetzung

Verarbeiter verursachen zusätzlichen Speicher- und Verbindungsbedarf. Die
Obergrenze aus den sichtbaren CPUs schafft Prozessplätze für 100 % Puffer.
Der Dauertest mit doppeltem Bestand und doppelter Ereignisrate prüft den Puffer
für den Wahrheitsweg und den Arbeitsweg gemeinsam. Eine größere VM kann die
Poolgröße automatisch erhöhen. CPU-, Speicher- und Datenbankgrenzen müssen
auch im restriktiven Supervisor-Container wirksam sein.

CI prüft jede Route und Operation gegen eine explizite Zuordnung. Ein
Belastungstest prüft, dass zugelassene Verwaltungsarbeit den Hauptprozess
innerhalb seines Schleifenbudgets lässt. Die kurze b32-Profilmessung liefert den Vergleich. Der 60-Minuten-Doppel-Dauertest
misst den vollständigen neuen Stand einmal vor dem Release. Maßgeblich sind die harten Stabilitäts- und
Korrektheitsbedingungen im [Betriebsentwurf](../operations/wahrheit-hat-vorfahrt-a1.md#abnahme).
