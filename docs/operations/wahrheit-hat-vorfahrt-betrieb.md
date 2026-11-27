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
60-Minuten-Lauf mit doppeltem Bestand und 40 Ereignissen pro Sekunde an.
