# Lastschutz und Projektionspause (b26, Schritt 3)

Entschiedener Umfang: [#132](https://github.com/Levtos/wuschel/issues/132),
auf den Betriebseinstellungen aus [#131](https://github.com/Levtos/wuschel/issues/131).
Kein Release und keine Installation in diesem Schritt; Sammeldurchsicht in #135.

## Lastzustand

`load_guard.py` gehört zu `lane`. Der prozesslokale Wächter wird zusammen mit
der Ereignisschleifen-Messung einmal pro Sekunde abgetastet. Er besitzt weder
Speicherzugriffe noch Verbindungsbefehle. Der höchste Schwellenübertritt der
vier Eingänge bestimmt den Zielzustand. Ein Ziel muss während der ganzen
Haltezeit anliegen; ein anderes Ziel beginnt die Haltezeit neu. Auf- und
Abstieg haben getrennte Haltezeiten. Direkte Wechsel zwischen `normal` und
`geschützt` sind bei entsprechend anhaltender Last möglich.

| Eingang / Einstellung | Angespannt | Geschützt | Grenze |
| --- | --- | --- | --- |
| Ereignisschleife, `load_lag_ms_*` | 100 ms | 500 ms | 1–10.000 / 1–30.000 ms |
| Eingangstiefe, `load_queue_percent_*` | 50 % | 85 % | 1–100 %; geschützte Ereignisse dürfen die weiche Queuegrenze übersteigen |
| Eingangsüberläufe je Minute, `load_overflows_per_minute_*` | 1 | 10 | 1–100.000; begrenzte Sekunden-Buckets über 60 s |
| p95 der Ereignis-Commit-Zeiten im Zeitfenster, `load_commit_p95_ms_*` | 100 ms | 500 ms | 1–10.000 / 1–30.000 ms |
| `load_commit_window_s` | 60 s | | 10–600 s |
| `load_enter_hold_s` | 5 s | | 0,1–300 s |
| `load_recover_hold_s` | 30 s | | 0,1–600 s |
| `load_interactive_wait_s` | 30 s | | 1–60 s |
| `projection_protected_interval_s` | 10 s | | 1–60 s |
| `retry_warning_after` | 3 Fehlschläge | | 1–100 |

`load_commit_window_s` begrenzt diese Messungen auf die letzten 60 Sekunden
(erlaubt: 10–600 s; wirkt nach Neustart). Verwaltungsoperationen zählen nicht.
Unter fünf Messungen ist die Metrik nicht gemessen (`None`) und unauffällig.
Messungen altern auch ohne neue Commits aus; danach gilt die normale
Erholungshaltezeit `load_recover_hold_s`. Die Systemzustandsseite nennt die Zahl
der Messungen im Fenster oder „Noch nicht gemessen“.

`*` steht jeweils für `strained` oder `protected`. Die Schutzschwelle darf
nicht unter der angespannten Schwelle liegen. Fehlende Zeitmessungen bleiben
in der Diagnose ausdrücklich unbekannt. Wie alle Betriebswerte werden auch
  diese Werte atomar in `operations.json` gespeichert und erst beim
Neustart wirksam. Die bisherigen 42 Betriebswerte bleiben unverändert.

`angespannt` meldet nur Zustand und Protokolleintrag. `geschützt` begrenzt
ausschließlich die HA-Projektion: höchstens ein Versuch je eingestelltem
Abstand, auch bei Fehlern. Der neueste vollständige Snapshot wird erst vor
dem Senden gebildet. Zwischenstände werden nicht aufgestaut. Speicherung,
Berechnung, geschützte Eingänge, Fristen und Veröffentlichung bleiben in
ihrer vorhandenen Reihenfolge. Last oder MQTT-Queueüberlauf lösen keine neue
Verbindung aus. MQTT-Überläufe bleiben als Lücke sichtbar.

## Logdiagnose ab b29

`load_state_changed` meldet beide Zustände, die aktuellen Gründe und auf drei
Nachkommastellen gerundete Messwerte. Fehlende Messwerte erscheinen als
`null`. Die Warnung nennt den laufenden oder zuletzt beendeten Vorgang
und seine bisherige bzw. gesamte Laufzeit. `operation_active` zeigt an, ob
der genannte Vorgang noch läuft. Ohne verfügbaren Vorgang sind diese Felder
`null`.

`event_loop_stall` fasst aufeinanderfolgende Messungen ab
`load_lag_ms_strained` in einer Warnung zusammen. Beim ersten Messwert unter
der Schwelle oder beim Prozessstopp meldet sie Anzahl, höchsten Lag und bis
zu acht Vorgänge oder HTTP-Routenmuster mit der stärksten zeitlichen
Überlappung zur Spitzenmessung. Bei Sammelrouten erscheint das registrierte
Sammelmuster. Pfadparameter, Querys, Header und Anfragedaten werden nicht
erfasst. Die Laufzeit ist die verstrichene Zeit. Zeitliche Überlappung allein
belegt keine Ursache.

`operation_timing`, `commit_timing` und `observation_timing` erscheinen bei
DEBUG. Die Laufzeit eines Beobachtungsblocks steht in `operation_timing`.

## Verwaltung und Pause

`slots.Operation.heavy` kennzeichnet Vorschau/Validierung, Katalogpflege,
Katalogbereinigung und -umfang, Aufbewahrung, Titelabgleich und Import.
`Runtime.submit()` weist diese Vorgänge im Schutz mit `load_protected` ab,
bevor ein Einreichungshelfer oder eine Warteschlange erreicht wird. Der
Verwaltungsarbeiter prüft nach dem Warten auf seinen Platz erneut. Bereits
begonnene Arbeit wird zu Ende geführt. Die API antwortet mit 503. Bei bewusst
ausgelöstem Prüfen, Vorschau, Speichern oder Übernehmen sieht der Nutzer eine
Warteanzeige mit Abbruch. Der Leseaufruf `GET /api/v1/operations/wait` wartet
außerhalb der Laufzeitwarteschlange. Nach dem Ende des Schutzes kann der Browser
die abgewiesene Aktion mit denselben Daten erneut einreichen. Bei weiter aktivem
Schutz zeigt die Oberfläche Messgröße, aktuellen Wert, Schwelle und den Link zu
Einstellungen → Betrieb. Die Lastanzeige in der Kopfzeile führt ebenfalls dorthin.
Betriebsdiagnose, Pause/Fortsetzen und Wahrheitsverarbeitung bleiben zugänglich.
Große Importe werden schon vor dem Lesen/Entpacken abgewiesen.

Quittungen für Hinweise und Aktualisierungen speichern ihren Job- und
Wiederholstatus. Eine fällige Auswertung und Kalenderantworten mit möglicher
Auswirkung auf Wahrheiten durchlaufen den bisherigen Auswertungspfad. Wird
eine Frist während des Speicherns fällig, löst der Laufzeit-Timer die Auswertung
aus. Die Übernahme eines Dienstjobs bleibt dauerhaft gespeichert, bevor der
Dienst von seiner Ausführung erfährt. Die in #152 gemeldeten Laufzeiten für
Verwaltungs- und Dienstjoboperationen gehen über diesen Pfad nicht direkt in
die p95-Stichprobe ein. Hohe Laufzeiten können Eingänge dennoch länger warten
lassen.

`PUT /api/v1/operations/projection` akzeptiert genau `{"paused": true|false}`.
Die vorhandene Admin-/Ingress-Grenze gilt; Consumer und Cross-Site-Schreibzugriffe
bleiben gesperrt. Es wird kein Zustandswert in der Datenbank oder Datei abgelegt.
Nach einem Prozessneustart läuft die Projektion wieder. Während einer Pause
werden auch keine Projektions-Lebenszeichen gesendet. Ein bereits gesendeter
Versuch darf noch abgeschlossen werden. Fortsetzen weckt den bestehenden
Projektionsarbeiter; es öffnet keine neue Verbindung.

Der Schalter liegt im Systemzustand. Ein dauerhaftes Hinweisband in der
vorhandenen App-Shell zeigt die Pause auf jeder Seite und führt zum Schalter.
Der Lastzustand und Wiederholmeldungen werden dort ebenfalls angezeigt.

## Aktualität in Traber

Die im Issue angenommene 120-s-Regel war im Ausgangscode nur eine
Trennungsfrist. Ein offener, pausierter WebSocket konnte alte Werte zeitlich
unbegrenzt als frisch anzeigen. Traber prüft deshalb nun zusätzlich die Zeit
seit dem letzten akzeptierten Projektionspaket, unabhängig von der Verbindung.

Ab 120 s bleibt die Verbindung offen, der Sensorwert ist unbekannt und
`status=stale`, `quality=degraded` stehen am Sensor und an allen projizierten
Feldern. Die alten Rohwerte bleiben nur als entsprechend gekennzeichnete
Diagnose sichtbar. Der echte HA-Kern entfernt benutzerdefinierte Attribute bei
`unavailable`; für die offene, pausierte Verbindung wird deshalb der unbekannte
Sensorwert mit sichtbaren Stale-Attributen verwendet. Die vorhandene
Trennungsfrist bleibt unverändert. Auch ein identisches Paket nach Fortsetzen
aktualisiert die Sensoren wieder. Reguläre unveränderte Lebenszeichen lösen
weiterhin keine zusätzlichen Sensor-Schreibvorgänge aus.

## Wiederholinventar

Alle Fehlertexte enthalten ausschließlich eine Fehlerklasse, keine Eingaben,
Adressen oder Zugangsdaten. Die Diagnose führt Gesamtzahl, aufeinanderfolgende
Fehlschläge, letzten Fehler/Zeitpunkt, Beginn der laufenden Fehlerserie und
Warnkennzeichen. Erfolg beendet die Fehlerserie; der letzte Fehler bleibt als
historischer Nachweis sichtbar. Ab `retry_warning_after` erscheint eine Meldung
im Systemzustand. Zähler und Meldungen sind prozesslokal.

| Schleife | Obergrenze / Ende | Zähler | Meldung |
| --- | --- | --- | --- |
| `app.initialize` | exponentiell, `startup_retry_max_s`; Identitäts-/Schemafehler warten auf bewusste Korrektur | `startup` | ja |
| `app.health` / DB-Erholung | exponentiell, `startup_retry_max_s`; sonst `health_interval_s` | `database` | ja |
| `app.scheduler`, Ereignisschleifenmesser, Uhr | periodische Takte; keine Fehlerwiederholung, unerwarteter Taskfehler wird weitergegeben | entfällt | Last-/Taskzustand |
| `BridgeAdapter.run` | exponentiell mit Jitter, `ha_retry_max_s` | `ha` | ja |
| `projection_worker` | exponentiell mit Jitter, `projection_retry_max_s`; im Schutz zusätzlich Ausgabetakt; Pause sendet nichts | `projection` | ja |
| `MQTTAdapter.run` | exponentiell, `mqtt_retry_max_s`; Rücksetzen erst nach 10 s stabiler Verbindung | `mqtt` | ja |
| `refresh_worker` | ein Versuch je angefordertem Eingang, keine unmittelbare Wiederholung | `refresh` | ja |
| `service_worker` | ein Versuch je gespeichertem Job; Frist `ha_service_timeout_s`; keine Wiederholung ungewisser Aktionen | `service` | ja |
| Verwaltungsarbeiter | höchstens drei vorbereitende Versuche, ausschließlich vor dem Schreiben bei OCC-Livekonflikt; andere Fehler werden weitergegeben | `management` | ja |
| Kalender-/Benachrichtigungs-Jobs | vorhandene fachliche Planung/Fehlerzahlen und begrenzte Fälligkeit; Versand durch `service_worker` | fachlicher Job und `service` | fachliche Meldung und Systemzustand |
| Traber | ereignisgesteuerte WebSocket-Handler, keine automatische Wiederholschleife; Dienste einmal mit 10-s-Frist, Kommandos einmal mit 30-s-Frist | keine Wiederholung; ausbleibende Projektion hat sichtbaren Alterszustand | `stale` nach 120 s |

## Nachweise

`test_load_guard.py` prüft alle Eingänge, Haltezeiten, Grenzpaare, Ablehnung
vor Arbeit, endliche OCC-Wiederholung, API-Rollen und den 500-Ereignis-Sturm
mit vollständiger Speicherung und lückenloser Veröffentlichungsfolge.
`test_load_projection.py` verwendet einen echten synthetischen HA-WebSocket
für Pause, Lebenszeichen, Drossel, Zusammenfassen und Fortsetzen ohne Reconnect.
Der HA-Kerntest prüft 119/120 s und die Wiederherstellung am echten Sensor.
`test_load_storm.py` besitzt die neue Aufzeichnung `event_storm_protected.json`.
Alle zehn b25-Aufzeichnungen bleiben bytegleich. Ihr vorhandener
Normalisierer trennt nur die zusätzlich eingeführten b26-Diagnosefelder ab;
die neuen Diagnosezustände haben eigene Assertions und eine neue Aufzeichnung.
