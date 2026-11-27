# ADR 0034 – Wahrheit hat Vorfahrt

- Status **Entwurf für A1**, zur Claude-Nachprüfung und Freigabe durch Benni
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

## Entscheidung im Entwurf

Wuschel hat genau **einen seriellen Wahrheitsschreiber**. Er bestimmt die
Reihenfolge der zugelassenen Eingänge, die aktive Registerrevision und die
Publikationsfolge. HA und MQTT, Kalenderbefunde, Fristen sowie Auswertung,
Commit und Projektion bleiben auf diesem Weg. Die vorhandene Semantik für
Ereignisblöcke, Wiederanlauf, Lücken, Befehle und Neuabgleich gilt weiter.
Geteilte Quellen, Vertragsabhängigkeiten, Katalogsichten und die HA-Projektion
brauchen denselben Stand. Schreiber pro Katalog oder Vertragsart würden dafür
eine verteilte Commit-Reihenfolge und neue Fehlerfälle an diesen Grenzen
einführen.

Der Arbeitsweg übernimmt große Eingaben, Aufbereitung, Vorschauen, Abfragen,
Vergleiche und Vorbereitung von Änderungen. Ein eigener Koordinator betreibt
hoch- und herunterfahrbare Unterprozesse. Die Verarbeiter erhalten je Auftrag
eine unveränderliche Sicht und geben bei Änderungen einen geprüften, versiegelten
Plan zurück. In der seriellen Reihenfolge prüft der Schreiber den erwarteten
Stand erneut und übernimmt den Plan atomar. Geschäftsdaten, Beleg und
Publikationsstand werden gemeinsam wirksam. Schreibrechte auf Wahrheitstabellen
liegen ausschließlich beim Wahrheitsschreiber. Arbeitsdaten und Auftragsjournal
haben eigene Rechte und Lebenszyklen.

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
innerhalb seines Schleifenbudgets lässt. Vollständige Abnahme und Vorher/Nachher
gehören zu A2. Maßgeblich sind die harten Stabilitäts- und
Korrektheitsbedingungen im [Betriebsentwurf](../operations/wahrheit-hat-vorfahrt-a1.md#abnahme).
