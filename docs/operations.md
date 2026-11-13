# Betrieb: Wuschel

Alpha 8 erlaubt eigene Vorlagen und Verträge ohne Test-Namensraum. Die Beispiele
bleiben synthetisch. Wuschel wird als neue App mit Slug `wuschel` installiert; Terra
und die alte Bridge bleiben erhalten. Für jede Installation: eigene logische PostgreSQL-DB,
eigene DB-Rolle und eigene Tokens. Keine Verbindung zur Legacy-DB herstellen.
Die folgenden Installationsschritte sind eine Betriebsvorlage, keine Freigabe:
Pre-Install-Remediation und unabhängiger Delta-Review müssen bewertet sein;
reale Installation und Supervisor-Abnahme benötigen Bennis gesonderten Auftrag.

## Vorbereitung und Installation

Für neue Installationen gilt der [First-Run-Wizard](first-run.md): App mit
Standardoptionen starten und Ingress öffnen. DB-Provisionierung, privater Import,
Identitätsprüfung und Migration werden geführt; Supervisor-DB-Optionen müssen
nicht manuell bearbeitet werden. Bestehende Alpha-Konfigurationen bleiben gültig.
Der noch manuelle HACS-Bridge-Schritt ist dort als Release-Blocker dokumentiert.
Die folgende Liste beschreibt den bisherigen Alpha-Optionspfad und ist keine
Aufforderung, die bestehenden produktiven Optionen zu ändern.

1. **PostgreSQL 14 oder neuer** bereitstellen: Der Writer setzt
   `idle_session_timeout`, das ab PostgreSQL 14 verfügbar ist. Eine leere eigene
   Datenbank und Rolle mit DDL-/DML-Rechten auf deren Schema/Tabellen anlegen
   (Migrationen benötigen auch CREATE/ALTER und Index-Erstellung). Die Rolle
   muss die unten genannten Session-Settings beim Verbindungsaufbau setzen dürfen.
   Zugangsdaten außerhalb des Repository verwahren.
2. Traber aus `custom_components/traber` über HACS
   oder manuell installieren und einmal in HA hinzufügen. Keine Entities oder
   Services entstehen. Keine Registry-Konfiguration gehört in die Bridge.
3. Für die Installation aus dem GitHub-App-Repository müssen beide in
   `wuschel/config.yaml` referenzierten GHCR-Images öffentlich
   verfügbar sein. Den aktuellen [Veröffentlichungs- und anonymen Pull-Nachweis](publishing.md)
   prüfen; eine grüne Build-CI allein genügt nicht. Für einen separaten lokalen
   Supervisor-Build `uv run python dev/stage_app.py <neues-ziel>` ausführen.
   Das erzeugte lokale App-Repository enthält den vollständigen Build-Kontext;
   beim Staging wird die Referenz auf ein vorgebautes Image entfernt.
4. DB-Optionen, TLS/CA unter `/ssl`, Anzeigename und MQTT-Modus konfigurieren.
   `SUPERVISOR_TOKEN` kommt ausschließlich aus der App-Umgebung.
   Externes MQTT unterstützt `mqtt_tls` und `mqtt_ca`; bei aktivem TLS werden
   Zertifikatskette und Hostname geprüft. Leere Credentials bedeuten anonyme
   Anmeldung. Supervisor-Credentials verbleiben ebenfalls ausschließlich serverseitig.
5. App starten. Root bereitet nur `/data` vor; Python läuft als UID/GID 10001.
   Liveness kann grün sein, während Readiness auf Registry/Snapshot wartet.
6. Ingress öffnen, `example-registry.json` als Draft importieren, Bindings auf
   eigene synthetische Testquellen ändern, validieren und ausdrücklich aktivieren.
   Die Fixtures sind keine produktiven Fach-Contracts.

App, API-Protokoll, Bridge-Protokoll, Client und Registry-/DB-Schema haben
getrennte Versionsnummern. Dieser Build erzeugt keinen Release oder Tag.

## Startdauer und Katalogpflege

`/health/ready` liefert unter `startup` die Phasen des letzten Startversuchs
in gerundeten Millisekunden. `startup_attempts` zählt die Versuche seit
Prozessbeginn, auch bei Fehlern beim Datenbankaufbau. Der Fehlergrund bleibt
in `setup.error`. Ein neuer Versuch ersetzt die bisherigen Phasen.
`startup_timing` protokolliert weiterhin die ungerundeten Messwerte.
`restore_migration` bleibt die Gesamtdauer; `restore_clone`,
`restore_catalog_baseline`, `restore_authoring` und `restore_contexts` zeigen
die einzelnen Anteile. Diese Teilwerte nicht noch einmal zur Gesamtdauer addieren.

Die Katalogpflege hat für Vorschau, Schreiben und Auflisten das Laufzeitziel
500 ms mit dem synthetischen Bestand aus `tests/test_b22_p2.py` (Speicher und
PostgreSQL). Alle Messwerte bleiben als `record_property` erhalten. Erst über
2.500 ms schlägt die Stabilitätsprüfung fehl; das Laufzeitziel allein sperrt
keinen Merge.

## Identität und Tokens

`/data/installation.json` und `cc_meta.installation_id` müssen übereinstimmen.
Beide leer erzeugt UUIDv4. Lokale ID bei leerer DB erfordert ausdrücklich
`initialize_empty_database: true`. Fehlende lokale ID bei bestehender DB
erfordert `adopt_installation_id` exakt passend. Unterschiedliche IDs werden
immer abgewiesen. Nach erfolgreicher Initialisierung die einmaligen Optionen
wieder deaktivieren/leeren.

`/data/secrets/consumer_token` und `admin_token` besitzen mindestens 256 Bit
Zufallsentropie und Modus 0600. Consumer benutzen Port 8787 intern und Bearer-
Authentifizierung. Keine Port-Freigabe nach außen einrichten. Ingress nutzt
Port 8099 und prüft die Peer-Adresse, nicht vom Browser gesetzte Header.
Tokens lassen sich in der Admin-UI rotieren; neue Werte erscheinen einmal im
Antwortfeld, nie im Browser-Speicher. Clients anschließend aktualisieren.
Rotation und Registry-Rollback verlangen eine Bestätigung. Auf dem Bearer-Listener
zählt der vorgeschaltete Limiter `auth:{ip}` **alle nicht-Health-Anfragen** vor der
Tokenprüfung: 120 pro Minute je IP, gemeinsam über Consumer- und Admin-Rolle,
einschließlich fehlgeschlagener Authentifizierung. Zusätzlich gilt für
authentifizierte Aufrufe ein Budget von 120 pro Minute je IP und Rolle.
Ein Rollen- oder Tokenwechsel umgeht das gemeinsame IP-Budget nicht. Beispiel:
100 Consumer-Aufrufe lassen derselben IP noch 20 Admin-Aufrufe im Zeitfenster.
Health-Abfragen sind ausgenommen; WS-Deltas zählen nicht als HTTP-Aufrufe,
der WS-Verbindungsaufbau zählt jedoch. Ingress verwendet seine Peer-Prüfung
und das Budget je IP/Admin-Rolle, keinen Bearer-Auth-Limiter.

## Fehler und Wiederanlauf

`/health/live` prüft Event-Loop-Heartbeat und Processor-Task; nur diese Route
steuert Watchdog/HEALTHCHECK. `/health/ready` meldet DB-/Registry-/Snapshot-Gates.
Späterer HA-Verlust erscheint als Connectivity-Komponente. Evidence behält ihre
normale Aktualitätsregel: zustandsbehaftete Werte ohne Ablauf bleiben gültig,
TTL/Heartbeat laufen regulär ab. Sicherheitsrelevante Öffnungen und Heizfreigaben
brauchen deshalb Heartbeat oder Liveness, siehe [ADR 0005](adr/0005-ha-outage-connectivity.md).
Ein DB- oder
Lock-Verlust friert die Fortschreibung ein; keine Publication oder Command-Acks.
Der letzte sichtbare Envelope bleibt unverändert, `publication_confirmed=false`.
Ingest hält pro Binding nur die letzte Observation. Recovery prüft Identität und
Lock neu, startet eine neue Epoche und protokolliert den Gap. Consumer resynchronisieren.
Readiness verlangt nach DB-Recovery einen neuen HA-Snapshot. Neue Connection-Gaps
werden nur beim Zustandswechsel geschrieben. Sie unterbrechen zeitliche
Kontinuitätsannahmen, entwerten HA-Messwerte aber nicht pauschal.
Ein verlorenes Zwischenereignis darf keinen alten stable_for-
Anker erhalten; Grace wird aus alter Evidence nicht neu gestartet.

DB-, HA- und MQTT-Verbindungsfehler verwenden einen Backoff bis 30 s. Der Client
ergänzt Jitter und resynchronisiert nach Transport-/Framefehlern. HTTP-/WS-401/403
sind terminale Auth-Fehler; Credentials müssen korrigiert werden.

Die dedizierte PostgreSQL-Writer-Verbindung konfiguriert Keepalive 15 s / 5 s / 3 Versuche,
`tcp_user_timeout=30000`, `idle_session_timeout=60000` und
`idle_in_transaction_session_timeout=15000` (Millisekunden). Die dedizierte Rolle
muss `tcp_keepalives_idle`, `tcp_keepalives_interval`, `tcp_keepalives_count`,
`tcp_user_timeout`, `idle_session_timeout`, `idle_in_transaction_session_timeout`
und `application_name` als Session-Settings setzen dürfen. Diese Einstellungen
benötigen bei Standard-PostgreSQL keine Superuser-Rolle; bei verwalteten Diensten
muss deren Zulässigkeit für die App-Rolle geprüft werden. Version <14 oder
abgewiesene Settings verhindern den Connect und lassen den Start im DB-Retry.
Der reguläre 0,5-s-Probe hält den Writer unter dem 60-s-Idle-Limit aktiv;
Änderungen am Probe-Takt müssen dieses Verhältnis erhalten.
Siehe PostgreSQL 14:
[Session-Timeouts](https://www.postgresql.org/docs/14/runtime-config-client.html#GUC-IDLE-SESSION-TIMEOUT)
und [TCP-Settings](https://www.postgresql.org/docs/14/runtime-config-connection.html#GUC-TCP-KEEPALIVES-IDLE).
Connect/Command sind auf 10 s begrenzt.
Ein Lock-Konflikt wird mit der Backend-PID diagnostiziert, niemals zwangsweise
entsperrt. Netzwerkpartitionen können bis zur serverseitigen Erkennung eine
Wiederverbindung verzögern. Reale Firewall-/WAN-Bedingungen separat nachweisen.

Die Runtime hält nur aktuellen Zustand und ausstehende Changesets. Identische
Idle-Ticks erzeugen keine Historie. `test.temporal/age` publiziert ein bei Änderung
seiner Evidence abgetastetes Alter; bei unveränderter Evidence bleibt dieser Wert
auch bei fremden Timer-Auswertungen gleich. Ein laufendes Anzeigealter lässt sich
aus `measured_at` berechnen. Freshness-Ablauf, Grace-Ende und das Rücksetzen einer
aktiven Edge bleiben echte, einmalig publizierte Zustandswechsel.
Neue Heartbeat-Reports sind neue Evidence und erzeugen weiterhin Publications.
Audit-Zeilen echter Änderungen bleiben in
PostgreSQL; Retention/Partitionierung ist gemäß Spezifikation §29 vertagt und
DB-Speicher muss beobachtet werden. Die alte API-Historie bleibt auf 100 Einträge
begrenzt; Alpha 4 ergänzt UTC-sortierte Cursor-Seiten mit maximal 200 Einträgen.
Das begrenzt weder die dauerhafte Historie noch den SQL-Suchaufwand. Der
[Alpha-4-Messnachweis und Folgeauftrag F1](alpha4/opus-review-corrections.md)
beschreibt Speicherwachstum, Zugriffspläne und das offene Produktionsgrößen-Gate.
SQL-Writes pro geänderter Zeile bleiben seriell; hohe WAN-Latenz
kann den Durchsatz begrenzen.

HA-Gerätezeit ist nur über ausdrücklich konfiguriertes `adapter.ha_time_attribute`
aktiv. Ein zufälliges Attribut `device_timestamp` wird nicht implizit interpretiert.
Fehlerhafte konfigurierte Zeitstempel liefern `invalid_value` der betroffenen
Observation. HA-State-last_changed belegt nicht die Kontinuität eines Attributs.

Migrationen sind nummeriert und mit SHA-256 geschützt. Veränderte Checksummen
oder eine neuere unbekannte DB-Version blockieren den Start. Kein automatischer
Contract-/Registry-Semantik-Upgrade. App-Updates aktivieren keine Revision.

## Konsistentes Backup und Restore

Für einen Software-Downgrade gilt zusätzlich das
[Alpha-4-/Alpha-3-Runbook](alpha4/downgrade.md). Nach gespeicherten
Alpha-4-Erweiterungen genügt ein Registry-Rollback nicht: Er benötigt ein
nachweislich kompatibles Paar aus `/data` und PostgreSQL-Backup.

Ein Supervisor-Backup enthält `/data`, aber **nicht** die externe PostgreSQL-DB.

1. App kontrolliert stoppen, damit kein Writer weiterläuft. Letzte Publication-
   Sequenz und App-/Schema-Version notieren; `last_shutdown.json` enthält die Sequenz.
2. PostgreSQL mit `pg_dump --format=custom --file=<backup>` sichern. Credentials
   über geschützte PG-Service-/Passwortdatei bereitstellen, nie im Kommando/Log.
3. `/data`, Secrets, App-Version, DB-Schema-/Installations-ID und Dump gemeinsam
   gesichert ablegen. Wiederherstellung in separater Test-DB regelmäßig prüfen.
4. Restore: App stoppen; Dump in eine leere, eigene DB mit `pg_restore --exit-on-error`
   einspielen; passendes `/data` und App-Version wiederherstellen; ID prüfen.
5. App starten: neue Epoche, Gap, aktuelle Evidence. Keine Command-Wiederholung,
   keine Grace-Verlängerung, kein impliziter Kontinuitätsnachweis. Einen Warnhinweis
   `database_older_than_last_shutdown` ausdrücklich untersuchen.

Bei gleicher Installation-ID und neuerem Shutdown-Marker hebt die Runtime den
Sequenz-Floor an und schreibt einen Gap, bevor sie weiter publiziert. Während
eines laufenden Prozesses bleibt zusätzlich dessen zuletzt bekannte Sequenz
maßgeblich. Ein fremder Marker wird nicht als Sequenznachweis übernommen. Wenn
sowohl DB als auch `/data` älter sind, kann der Prozess extern bereits beobachtete,
nirgendwo mehr gespeicherte Sequenzen nicht rekonstruieren.

SIGTERM sendet `going_away`, schließt offene WebSockets und beendet Queue/DB
innerhalb eines gemeinsamen 25-s-Budgets. `last_shutdown.json` wird nach dem
Queue-Drain geschrieben. Der echte Supervisor-Signal-/Watchdog-Nachweis bleibt
separat offen; isolierte Docker-Tests sind kein Installationsnachweis.

Ein Supervisor-Restore überschreibt die DB niemals. Ein Rollback der Registry
ist eine neue Revision. Datenbank-Rollback und Registry-Rollback sind getrennt.

## Entwicklung

`uv sync --frozen`; `uv run pytest`; `uv run ruff check .`;
`uv run mypy --strict src client/core_contracts_client`.
Frontend: `npm ci`, `npm run check`, `npm test`, `npm run build` in `frontend/`.
Kein Preview-Server ist erforderlich. PostgreSQL-Tests verwenden ausschließlich
`TEST_DATABASE_DSN` zu einer dedizierten `*_test`-DB; sie erzeugen isolierte temporäre
DBs. Ohne DSN werden sie mit `skipped: environment` ausgewiesen.
`TEST_MQTT_DOCKER=1` aktiviert den isolierten Mosquitto-Test. Der HA-Kernel-Test
läuft im separaten CI-Environment mit gepinnter Home-Assistant-Version.
`uv run python dev/resource_probe.py` reproduziert die isolierte Idle-Messung.
Dev-Compose benötigt eine nicht eingecheckte Datei `dev/secrets/postgres_password`.

## Alte Apps und Bifröst nach Abnahme

Erst nach Abnahme und Bennis gesondertem Go; Benni entfernt die alten Komponenten
selbst. Vorher HA-Sicherung und externen DB-Rückweg prüfen. Legacy
benni_core_contracts 0.2.4 bleibt unberührt.

1. Bifröst-Eintrag unter Einstellungen → Geräte & Dienste entfernen.
2. HACS: Das bisherige Paket folgt dem umbenannten Repo levtos/mimir
   (HACS-ID 1409323489) und ist als Commit f039081 installiert, nicht als Release.
   Altes Paket entfernen; keine zweite Repositoryidentität erfinden.
3. Repo levtos/wuschel auswählen, „Beta-Versionen anzeigen“ einschalten und
   Traber gezielt als Release v1.0.0a10 laden. Nicht nur den neuesten Commit wählen.
   HACS-Name Traber; Dateipfad custom_components/traber.
4. Falls übrig: Benni entfernt genau custom_components/bifrost. Keine Änderung
   an .storage oder anderen Integrationen. Konfigurationsprüfung und geplanter
   HA-Neustart erst im autorisierten Teil B.
5. Mimir 1.0.0a9 zeigt danach „HA-Anbindung getrennt“. Das ist erwartet:
   Die App bleibt installiert und unverändert, ihr Transport wurde entfernt.
6. Alte mimir_<alte UUID>_signal/configuration-Hinweise nur falls vorhanden
   einzeln entfernen. Stand 20.10.2026, Benni/Claude: keine vorhanden.
   Kein dismiss_all, keine Wildcards oder fremden UUIDs.
7. Terra und Mimir erst nach ausdrücklicher Abnahme von Benni deinstallieren;
   Sicherungen behalten. Keine automatische Deinstallation/DB-Löschung.
   Alte DB mimir bis Beta-Abnahme behalten, Löschen nur mit gesondertem Go
   gemäß [Runbook](operations/wuschel-new-database.md).

### Verbindungsdaten und MQTT-Abnahme in Teil B

Benni überträgt Datenbank-Host und öffentlichen CA-Pfad selbst von Mimir;
Codex liest keine App-Optionen. TLS bleibt verify-full.
MQTT: Benni wählt external und trägt die im privaten Betriebsblatt festgelegten
Brokerdaten samt Benutzer/Passwort selbst ein. Testnachricht über HA-Aktion
mqtt.publish auf wuschel/test/abnahme; kein mosquitto_pub mit Passwort in der Shell.
Nachweis: Verbindungen meldet verbunden und die synthetische Testquelle liefert
den veröffentlichten Wert. Kein Teil-B-Schritt wird in A2 ausgeführt.
