# Core Contracts Platform Alpha 1

Entwicklungsstand: App mit Standardoptionen starten und Weboberfläche über
Home Assistant öffnen. Der First-Run-Wizard bleibt ohne Datenbank erreichbar.
Anleitung, sichere Konfigurationsautorität und offener HACS-Bridge-Release-Blocker:
[First Run](https://github.com/Levtos/wuschel/blob/main/docs/first-run.md).
Vorhandene Alpha-Installationen behalten ihre Supervisor-DB-Konfiguration,
bis eine neue Verbindung ausdrücklich im Wizard übernommen wird.

Nur generische Plattform und synthetische TEST-Fixtures. Keine produktiven
Domain-Contracts oder Consumer-Migration. Betrieb, Identität, Tokens und
Backup/Restore: [Operations](https://github.com/Levtos/wuschel/blob/main/docs/operations.md).

Eine eigene Datenbank auf **PostgreSQL 14 oder neuer** und die Thin HA I/O Bridge
sind erforderlich. Der Writer setzt `idle_session_timeout` (erst ab PostgreSQL 14).
Die dedizierte App-Rolle benötigt DDL-/DML-Rechte auf ihrem Schema und ihren Tabellen
für Migrationen und Runtime-Writes sowie das Recht, die Session-Settings
`tcp_keepalives_idle`, `tcp_keepalives_interval`, `tcp_keepalives_count`,
`tcp_user_timeout`, `idle_session_timeout`, `idle_in_transaction_session_timeout`
und `application_name` zu setzen. Standard-PostgreSQL verlangt hierfür keinen
Superuser; bei verwalteten Diensten die Freigabe der Settings prüfen. Abgewiesene
Settings oder PostgreSQL <14 verhindern den Verbindungsaufbau.

Der Bearer-Listener begrenzt alle nicht-Health-Anfragen mit `auth:{ip}` auf
120 pro Minute je IP über alle Rollen; zusätzlich gilt ein Budget je IP und Rolle
von 120 pro Minute. Auch erfolgreiche Anfragen zählen zum gemeinsamen IP-Budget.
Details zu Ingress, Health und WebSocket stehen in Operations.

Die Installation aus diesem Repository setzt öffentlich lesbare Images unter
`ghcr.io/levtos/{arch}-wuschel:1.0.0a7` voraus. Ein erfolgreicher Build
beweist noch keine Veröffentlichung. Ablauf, mögliche GHCR-Freigabe und
anonyme Pull-Prüfung: [Veröffentlichung](https://github.com/Levtos/wuschel/blob/main/docs/publishing.md).

Supervisor-/Live-Abnahme erfolgt separat durch Benni.

Der manuelle Titelabgleich verwendet die App-Option `title_classifier` mit
`database`, `user` und `password` für eine gesonderte TC-Leserolle. Trage diese
Werte selbst in der App ein; lasse sie bis zur Einrichtung der Rolle leer.
Host, Port und die geprüfte TLS-/CA-Konfiguration übernimmt der Abgleich aus
der bestehenden Wuschel-Datenbankverbindung. Die Rolle benötigt ausschließlich
Leserechte auf `public.tc_v3_catalog` und `public.tc_v3_entry_context`.
Der Abgleich startet in der Katalogpflege erst durch deinen Knopfdruck. Ein
Durchlauf liest den TC einmal und hält die Vorschau bis zum Abschluss, Abbruch
oder App-Neustart. Prüfe die breite Tabelle und bestätige jede Seite mit
„Diese Seite übernehmen“. Eine Seite enthält höchstens 350 Quelltitel; Master
und Varianten bleiben zusammen. Erst die erste erfolgreich übernommene Seite
speichert auch die Kontextabbildung. Du kannst jederzeit abbrechen: Bereits
übernommene Seiten bleiben erhalten. „Neu abgleichen“ liest einen neuen Stand
und zeigt nur die noch vorhandenen Abweichungen. Filter ändern die Ansicht;
übernommen wird stets die ganze angezeigte Seite. Ein zweiter Tab erhält
einen eigenen Durchlauf. Es erfolgt keine automatische Übernahme.
