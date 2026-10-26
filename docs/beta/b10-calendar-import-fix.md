# b10: Kalenderabruf und Importdialog

Auftrag und Freigabe: [#58](https://github.com/Levtos/wuschel/issues/58), einschließlich [#57](https://github.com/Levtos/wuschel/issues/57). Kleine Fehlerkorrektur gemäß Arbeitsregeln, Release und Installation einschließlich HA-Neustart freigegeben.

Traber übersetzt die unveränderten Protokollargumente `start` und `end` ausschließlich an der HA-Dienstgrenze. Der neue HA-Kernel-Test registriert den echten Kalender-Entity-Dienst mit dessen Originalschema und einem synthetischen `CalendarEntity`. Er prüft die Antwort über den registrierten Traber-WebSocket-Befehl, einschließlich eines Fensters über die Zeitumstellung.

Wuschel meldet eine fehlgeschlagene Abrufserie einmal nach erfolgreichem Speichern. Der gespeicherte Warnmarker überlebt Neustart und Fensterwechsel; ein erfolgreicher Abruf setzt ihn zurück. Unbekannte Fehlertexte werden auf einen statischen Code abgebildet. Die administrative Abfrage `sources/calendar-status` liefert ausschließlich Quellenkennung, Verknüpfungskennung, Abrufzeit, Ablaufzeit und Fehlercode. Die Quellenansicht zeigt diese Angaben beim Ansehen der aktiven Konfiguration.

Der Importdialog prüft geänderte Auswahlen nach 300 ms automatisch erneut. Überholte Antworten dürfen weder Vorschau noch Fehler der aktuellen Auswahl ersetzen. Währenddessen bleibt Einlesen gesperrt; Fehler und erneute Prüfung stehen direkt am Knopf. Unveränderte Quellen erscheinen nur als Anzahl. Neue Quellen und Quellen mit geänderter Regel oder Vorlagenzuordnung können optional zugeordnet werden. Ohne Auswahl bleiben Dateiregeln erhalten. Die Backend-Prüfung für Digest und Konflikte bleibt unverändert.

## Lokale Nachweise vor dem Push

- HA-Kernel: 6 Tests grün mit Python 3.14.5, Home Assistant 2026.10.0, asyncpg 0.32.0, pydantic 2.12.5, pytest 9.0.2, pytest-asyncio 1.3.0; dieselben direkten Versionen wie der CI-Job `ha-bridge`.
- Gezielte Backendtests: 65 grün. Gezielte Oberflächentests: 50 grün.
- Ruff, Formatprüfung, mypy, TypeScript und Oberflächentext-Prüfung grün; Frontend gebaut.
- Firefox Desktop, 1920 × 1080, synthetische 28 bestehende + 1 neue Quelle: nur die neue Quelle zugeordnet, automatische Vorschau, echter Plan-Digest beim Einlesen akzeptiert, aktive Konfiguration unverändert. Reproduzierbar mit `npm run build` und `node scripts/b10-import-review.mjs AUSGABEORDNER` im Frontend-Verzeichnis.
- Strenges Versionsgate für Wuschel und Traber 1.0.0b10 grün. Veröffentlichung, Installation und lesender Nachweis werden im Issue dokumentiert; Live-Abnahme bleibt Benni.

Keine Änderungen an `calendar_active`, Vorlagen, Importpaketen oder Datenbankschema.
