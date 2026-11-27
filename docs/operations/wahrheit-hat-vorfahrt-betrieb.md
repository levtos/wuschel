# Betrieb mit Auftragswarteschlange

Unter **Einstellungen → Betrieb** kannst du die Obergrenze der Verarbeiter festlegen. Im
automatischen Betrieb leitet Wuschel den Bedarf aus den verfügbaren CPUs ab. Eine CPU
bleibt für den Wahrheitsweg vorgesehen. Bei vier verfügbaren CPUs ergibt das einen Bedarf
von drei Verarbeitern und eine Obergrenze von sechs. Einer bleibt bereit. Wuschel startet
zusätzliche Verarbeiter für wartende Aufträge und fährt sie nach 90 Sekunden Leerlauf
herunter. Der Arbeitsweg verwendet bis zu sieben PostgreSQL-Verbindungen gleichzeitig,
eine für den Koordinator und bis zu sechs lesende Sitzungen mit der bestehenden App-Rolle.

Unter **Aufträge** siehst du, ob ein Auftrag wartet, läuft, übernommen wird, fertig ist,
fehlgeschlagen ist oder abgebrochen wurde. Bis zur Übernahme kannst du ihn abbrechen. Die
Übersicht und die übrigen Statusanzeigen nutzen dieselbe serverseitige Betriebssicht.
Wuschel sichert Aufträge und ihre Arbeitsdaten in PostgreSQL. Nach einem Neustart holt der
Koordinator unterbrochene Aufträge wieder ab. Die Übernahme verwendet dieselbe Quittung,
wenn Wuschel nach einer Unterbrechung erneut prüft, ob dieselbe Änderung bereits gespeichert ist.

Wuschel misst Schleifenverzug, Rückstau der Wahrheit und die Zeit bis zum bestätigten
Eintreffen der veröffentlichten Wahrheit bei Traber. Bei Rückstau drosselt Wuschel den
Arbeitsweg oder lässt dessen Aufträge warten. Unter **Betrieb** kannst du den Grund und die
verbleibende Haltezeit sehen. Eine Meldung erscheint bei einer tatsächlich verspäteten
Wahrheit ab einer Sekunde.

## Messprotokoll für Issue 180

Die folgenden Kurzläufe verwenden synthetische Bestände, Windows und den Speicher im
Prozess. Jeder Lauf verwendet dieselbe Datenform mit Kalender. Einfach bedeutet 100
Verträge, 90 Quellen und 3.500 Katalogeinträge. Doppelt bedeutet 200 Verträge, 180 Quellen
und 7.000 Katalogeinträge. Die Ereignisrate steigt von 8 auf 16 Ereignisse je Sekunde.
Die Tabelle nennt Millisekunden, außer für die ausdrücklich in Sekunden angegebenen Spalten.

| Version und Bestand | Verwaltung | Schleifenverzug max. | observation_block p95 | tick p95 | Umzug-Vorschau (s) | portable/stage (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| b32, einfach | aus | 14,73 | 11,85 | 0,82 | – | – |
| b32, einfach | an | 3.976,39 | 13,10 | 0,17 | 5,09 | 4,26 |
| b32, doppelt | aus | 37,69 | 24,15 | 0,09 | – | – |
| b32, doppelt | an | 7.739,38 | 42,66 | 0,16 | 11,67 | 7,91 |
| A2c, einfach | aus | 16,36 | 14,23 | 0,22 | – | – |
| A2c, einfach | an | 3.614,02 | 15,09 | 0,10 | 3,76 | 3,85 |
| A2c, doppelt | aus | 24,55 | 21,95 | 0,14 | – | – |
| A2c, doppelt | an | 7.471,24 | 38,59 | 0,13 | 10,08 | 7,63 |

Die A2c-Zeilen messen die Vorbereitung im selben Windows-Aufbau. Dieser Aufbau führt die
Verwaltungsrouten im selben Prozess aus. Mit doppeltem Bestand dauerte die Vorschau rund
1,6 s weniger. Für die 1-s-Grenze gilt die Messung des Linux-Abnahmelaufs mit getrennten
Verarbeitern.

Im profilierten Doppelversuch entfielen kumuliert 18,7 s auf den bisherigen Stage-Vorgang,
14,7 s auf die beiden Vorschauen und 6,0 s auf fünf Paketprüfungen. Die Zeiten im CPU-Profil
enthalten die Messinstrumentierung und überlappende Aufrufe. Die Timerproben des Kurzläufs
enthalten überwiegend Ticks ohne fällige Fachevaluation. Für die Laufzeit der
PostgreSQL-Transaktionen und die Zeit bis zur Traber-Bestätigung dient der separate
Prozessversuch.

`dev/truth_lane_profile.py` ist derselbe Kurztest für b32 und neue Stände. Wiederhole die
Läufe mit `--scale 1` und `--scale 2`, jeweils mit und ohne `--administration`.
`--profile` ergänzt ein kumuliertes CPU-Profil. Der manuelle GitHub-Arbeitsablauf
**Platform Alpha 1** bietet unter **work_acceptance** einen kurzen Probelauf und den
60-Minuten-Lauf mit doppeltem Bestand, 10 Ereignissen pro Sekunde und
Zehn-Sekunden-Spitzen mit 50 Ereignissen pro Sekunde alle fünf Minuten an.
Grundlage ist das A1-Planungsprofil mit 5 Ereignissen pro Sekunde und
Spitzen mit 25 pro Sekunde. Die tatsächliche mittlere Live-Rate wurde nicht
gemessen. Die bisherigen 40 Ereignisse pro Sekunde waren ein konstanter Stresstest.

## Linux-Abnahme mit PostgreSQL und Traber

Der kurze Probelauf mit doppeltem Bestand bestand bei 40 angebotenen Ereignissen pro
Sekunde. Der maximale Schleifenverzug betrug 498 ms. Die Ende-zu-Ende-p95 lag bei
2.377 ms, die p95 für `observation_block` bei 268 ms und die p95 für `tick` bei 5,3 ms.
Der Hauptprozess benötigte im Mittel 0,162 CPU-Kerne über den gesamten Probelauf.
Klassen-Umzug, TC-Abgleich, Verlaufsabfrage, Verarbeiter-Ausfall und Abbruch waren
Teil des Probelaufs. [Linux-Probelauf](https://github.com/levtos/wuschel/actions/runs/46071647804)

Der 60-Minuten-Versuch stoppte während der Aktivierung mit
`409 active_revision_conflict`. Die Beobachtungen hatten den Stand für die
Aktivierung fortgeschrieben. Die aktive Registerrevision war gleich geblieben.
[Dauertestversuch](https://github.com/levtos/wuschel/actions/runs/46071981207)

In einem längeren Probelauf mit doppeltem Bestand wurde die bestehende Aktivierung
nach diesem Konflikt direkt im seriellen Schreiber ausgeführt. Dabei lag der
maximale Schleifenverzug bei **3.058 ms**, gemessen während `activate`.
Das zugehörige Aktivierungsfenster dauerte rund 15,5 s. Traber hielt die Verbindung.
Der Test konnte **20 von 21** erwarteten geschützten Ereignissen in Reihenfolge
nachweisen und lief beim Warten auf das fehlende Ereignis in einen Zeitablauf.
Der Ausweichpfad ist aus A2c entfernt. Die Aktivierung unter laufender Wahrheit
ist in [#186](https://github.com/levtos/wuschel/issues/186) erfasst.
[Probelauf mit Aktivierung im Schreiber](https://github.com/levtos/wuschel/actions/runs/46072658958)

Der gezielte Regressionstest stellt das 21. geschützte Ereignis während der
Aktivierung in die Warteschlange. Die alte Revisionsprüfung verwarf es beim
Abarbeiten. Wuschel übernimmt es jetzt mit der aktiven Revision, wenn die
Beobachtungsgrundlage der Quelle gleich geblieben ist. Der Test prüft alle
21 Ereignisse in Reihenfolge und veröffentlicht den letzten Wert über dieselbe
Traber-Verbindung.

Die freigegebene Doppel-Abnahme misst Registeraktivierung und Rollback separat.
Die 1-s-Grenze gilt außerhalb dieser Fenster. Reihenfolge, Ereigniserhalt und
eine durchgehende Traber-Verbindung gelten über die gesamte Abnahme.
[Messbericht und Freigabe im Issue](https://github.com/levtos/wuschel/issues/180)
