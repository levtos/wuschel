# Betrieb und Systemzustand (b26, Schritt 2)

Entschieden in [#131](https://github.com/Levtos/wuschel/issues/131), aufbauend auf
[#130](https://github.com/Levtos/wuschel/issues/130). Dies ist der technische
Umsetzungsstand auf `b26`; Release, Sammeldurchsicht und Live-Abnahme folgen in #135.

## Ablage und Wirkung

`operations.json` liegt im Datenverzeichnis. Die Datei gehört weder zur
Datenbank noch zum Konfigurationsdokument oder dessen Import/Export.
Sie wird vor Einrichtung und Datenbankverbindung geladen. Ohne Datei und bei
ungültigem Inhalt gelten vollständig die bisherigen Standardwerte. Die Seite
Systemzustand meldet den Grund; Inhalte und Treiberfehlermeldungen werden nie
zurückgegeben. Unbekannte Felder, falsche Typen, nicht endliche Zahlen und Werte
außerhalb der Grenzen sind ungültig.

Alle Betriebswerte wirken beim nächsten Prozessstart. Die API unterscheidet
`value` (gespeichert), `active_value` (dieser Prozess) und `pending_restart`.
Das Speichern ändert weder laufende Warteschlangen noch bestehende Verbindungen.
Ein temporärer Dateistand wird vollständig geschrieben, synchronisiert und per
Umbenennen ersetzt. Bei einem Schreibfehler bleiben der gespeicherte und der
wirksame Stand erhalten. Die alte Datei wird nie vorab gelöscht.

Die Seite **Einstellungen → Betrieb** zeigt jeden Wert, Grenzen, Erklärung,
Standard und Neustart-Hinweis. Zurücksetzen verändert zunächst die Auswahl;
**Speichern** übernimmt sie. Hierfür ist kein Konfigurationsentwurf nötig.

## Verwaltungs-API

Die Routen benötigen dieselben Verwaltungsrechte wie die bestehenden
Verwaltungsrouten (Admin oder vertrauenswürdiger Ingress; Cross-Site-Schreiben
über Ingress bleibt verboten):

- `GET /api/v1/operations/settings`: Metadaten, gespeicherte/wirksame Werte,
  Hinweis und ausstehender Neustart je Feld.
- `PUT /api/v1/operations/settings`: Objekt mit einem oder mehreren Feldnamen
  und Zahlen. Prüft den gesamten neuen Stand, dann atomarer Dateiaustausch.
- `POST /api/v1/operations/settings/reset`: `{}` setzt alle Werte zurück;
  `{"key":"ingest_queue_size"}` setzt nur dieses Feld zurück.
- `GET /api/v1/system/state`: aktuelle Betriebsdiagnose ohne Datenbankabfrage,
  Protokollzugriff oder KI. Bleibt auch bei Ausfall der Speicherung lesbar,
  sobald die Installation identifiziert ist.

## Messwerte

**Diagnose → Systemzustand** fragt alle fünf Sekunden ab. Nach einem Fehler
wird ein zuvor gelesener Stand ausdrücklich als veraltet gekennzeichnet.
Eine fehlende Messung wird als „Noch nicht gemessen“ angezeigt, niemals als Null.
Nur die vom Backend festgestellte Bereitschaft erhält eine Statusfarbe;
Messzeiten und Zähler haben keine erfundenen Gesundheitsschwellen.

- Eingangs-, Auffrisch- und MQTT-Warteschlange: Belegung, eingestellte Größe,
  Überläufe seit Prozessstart. Bei nicht laufender MQTT-Verbindung bleibt ihre
  Belegung unbekannt. Geschützte FIFO-Ereignisse dürfen die weiche Grenze wie
  bisher überschreiten; das ist keine neue Verlust-/Drosselregel.
- Ereignisschleife: jede Sekunde gemessene zusätzliche Schlafverzögerung,
  aktuell und Maximum seit Prozessstart. Die Messaufgabe wird beim Beenden
  abgebrochen und abgewartet.
- Speicherzeiten: die letzten höchstens 256 erfolgreichen `Runtime._commit`-
  Vorgänge, gemessen mit `perf_counter`, einschließlich Vorbereitung und
  Übergang zum sichtbaren Stand. Median ist der statistische Median; p95 ist
  der aufgerundete Rang 0,95 × Anzahl. Fehlgeschlagene Speichervorgänge liefern
  keine erfolgreiche Stichprobe. Die alten Protokollmessungen bleiben erhalten.
- Startphasen/-versuche aus #130, Bereitschaftsgründe, Datenbank-, HA-, Traber-
  und MQTT-Status, Programm-/Traber-Version und Veröffentlichungszähler.
- Start, Datenbank-Erholung, HA, MQTT und HA-Ausgabe: Fehler insgesamt,
  aufeinanderfolgende Fehler, letzte Fehlerart und Zeitpunkt. Erfolgreiche
  Erholung setzt die Folge zurück, der letzte Fehler bleibt als Verlauf sichtbar.
  Es werden nur Fehlerklassen ausgegeben, keine Meldungstexte, Ziele oder Credentials.

## Inventar der festen Werte

Die folgende Startwerttabelle wird aus dem Modell erzeugt. Alle 42 Werte wurden
aufgenommen; die Standards entsprechen dem Stand vor #131. Die Poolgröße meint
die maximale Zahl zusätzlicher Leseverbindungen; die exklusive Schreibverbindung
bleibt separat. Einrichtungsprüfungen nutzen dieselben Datenbankfristen.

| Feld | Standard | Bereich | Einheit | Anschluss |
| --- | ---: | --- | --- | --- |
| `ha_heartbeat_s` | 20 | 5–120 | s | `adapters/ha.py` |
| `setup_bridge_timeout_s` | 15 | 1–120 | s | `setup.py` |
| `setup_reachability_timeout_s` | 5 | 1–60 | s | `setup.py` |
| `setup_status_timeout_s` | 10 | 1–60 | s | `setup.py` |
| `title_connect_timeout_s` | 5 | 1–120 | s | `title_reader.py` |
| `title_command_timeout_s` | 5 | 1–120 | s | `title_reader.py` |
| `title_read_timeout_s` | 30 | 1–300 | s | `title_reader.py` |
| `title_close_timeout_s` | 5 | 1–30 | s | `title_reader.py` |
| `title_prefetch_size` | 100 | 1–1000 | Zeilen | `title_reader.py` |
| `observation_block_window_s` | 0.05 | 0.001–0.5 | s | `observation_block.py` |
| `observation_block_size` | 100 | 1–1000 | Einträge | `observation_block.py` |
| `subscriber_queue_size` | 32 | 1–16384 | Nachrichten | `runtime.py` |
| `database_close_timeout_s` | 3 | 1–15 | s | `persistence.py` |
| `websocket_close_timeout_s` | 2 | 0.5–10 | s | `api.py` |
| `database_command_timeout_s` | 10 | 1–120 | s | `persistence.py / setup.py` |
| `database_connect_timeout_s` | 10 | 1–120 | s | `persistence.py / setup.py` |
| `database_idle_session_timeout_s` | 600 | 60–86400 | s | `persistence.py` |
| `database_pool_size` | 2 | 1–16 | Verbindungen | `persistence.py` |
| `database_idle_transaction_timeout_s` | 15 | 1–120 | s | `persistence.py` |
| `database_keepalive_idle_s` | 15 | 1–300 | s | `persistence.py` |
| `database_keepalive_interval_s` | 5 | 1–60 | s | `persistence.py` |
| `database_keepalive_count` | 3 | 1–10 | Versuche | `persistence.py` |
| `database_tcp_timeout_s` | 30 | 1–300 | s | `persistence.py` |
| `ingest_queue_size` | 256 | 1–16384 | Einträge | `runtime.py` |
| `refresh_queue_size` | 256 | 1–16384 | Einträge | `runtime.py` |
| `mqtt_queue_size` | 256 | 1–16384 | Nachrichten | `adapters/mqtt.py` |
| `management_concurrency` | 2 | 1–8 | Vorgänge | `runtime.py` |
| `ha_retry_max_s` | 5 | 1–300 | s | `adapters/ha.py` |
| `mqtt_retry_max_s` | 30 | 1–300 | s | `adapters/mqtt.py` |
| `projection_retry_max_s` | 5 | 0.25–300 | s | `adapters/ha.py` |
| `startup_retry_max_s` | 30 | 1–300 | s | `app.py` |
| `scheduler_interval_s` | 0.5 | 0.1–5 | s | `app.py` |
| `health_interval_s` | 2 | 0.5–30 | s | `app.py` |
| `adapter_poll_interval_s` | 1 | 0.1–10 | s | `adapters/ha.py / adapters/mqtt.py` |
| `http_timeout_s` | 30 | 1–120 | s | `app.py` |
| `ha_discovery_cache_s` | 15 | 1–300 | s | `adapters/ha.py` |
| `ha_discovery_timeout_s` | 10 | 1–60 | s | `adapters/ha.py` |
| `ha_service_timeout_s` | 12 | 1–60 | s | `adapters/ha.py` |
| `projection_timeout_s` | 20 | 1–120 | s | `adapters/ha.py` |
| `projection_heartbeat_s` | 5 | 1–60 | s | `adapters/ha.py` |
| `http_shutdown_timeout_s` | 3 | 1–15 | s | `app.py` |
| `shutdown_timeout_s` | 25 | 5–120 | s | `app.py` |

## Bewusst feste Grenzen und Algorithmen

Diese gefundenen Zahlen sind keine frei einstellbaren Betriebswerte:

| Fundstelle / bisheriger Wert | Entscheidung und Grund |
| --- | --- |
| `service_jobs.py` / HA-Dienstkanal: 120 Sekunden | Unverändert: ausdrücklich ausgenommenes Gültigkeits-/Sicherheitsprotokoll. |
| Traber `service_channel.py`: Ausführungsfrist 10 s; `command_ingress.py`: 30-s-Befehlsgültigkeit und Ergebnisaufbewahrung | Unverändert: fernseitiger HA-Befehlsvertrag und Schutz vor Wiederholung; keine App-Betriebsoption. Traber-Dateien werden nicht geändert. |
| `api.py`: WS-Begrüßungsfrist 10 s; HTTP-Ports 8787/8099; Protokollversionen, Größen-/Ratenlimits | Unverändert: Zugriffs-/Protokollgrenzen. |
| HA-Projektion: Warteschlange 1 | Unverändert: absichtlicher „neuester Stand“-Kanal, kein Ereignispuffer. |
| HA/MQTT/Start: erster Abstand 1 s, Projektion 0,25 s; Verdopplung, halber bis voller Zufallsabstand; HA-Stabilisierung 10 s | Unverändert: Wiederholalgorithmus; dessen Obergrenzen sind einstellbar. |
| `clock.py`: Prüfung alle 0,1 s; neue Messung jede 1 s; Commit-Fenster 256 | Unverändert: Uhr-/Messdefinitionen, keine fachliche Frist und kein Speicherumbau. |
| `operation_timing.py`: Protokoll nach 4096 Einträgen oder 60 s; Modell-/Auswertungs-Caches (z. B. vier Pläne) | Unverändert: begrenzte interne Mess-/Memoisierungspuffer; kein Wiederhol- oder Eingangsgrenzwert. |
| Konfigurations-/Importgrenzen: 50 MiB, 100000 Katalogeinträge, 64 Kontexte, 6400 Zuordnungen, 16 Übernahmepläne; Regelgrenzen 256 Knoten, Tiefe 16, 64 Eingänge, 32 Felder, 128 Vorlagen | Unverändert: Format-, Komplexitäts- und Sicherheitsgrenzen. |
| Aufbewahrung, Quellenaktualität, Kalender-/Weck-/Öffnungs-/Formelwerte, semantische Zeitregeln | Unverändert: bereits fachlich konfigurierte Werte oder fachliche Regeln; gehören in die Konfiguration. |
| SQL-Schema-/Lockkennungen, Protokoll-/Schema-/Versionsnummern, Stringlängen, CRC/IDs, Seiten-/Exportgrenzen | Unverändert: Identität, Verträge und Formatgrenzen. |

Der Code bleibt den bisherigen Paketen zugeordnet; `operations` gehört zu
`kernel`. Die acht Importregeln bleiben ohne Ausnahme erhalten. Die zehn
vorhandenen Replay-Aufzeichnungen bleiben bytegleich. Neue Betriebsdiagnose
wird separat geprüft; der bisherige Dienststatus-Vertrag bleibt unverändert.
