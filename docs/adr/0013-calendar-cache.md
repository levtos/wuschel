# ADR 0013 — G2: Kalender als Quelle über Bifröst

18.10.2026 · **A2 freigegeben: Claude (Technik), Benni (Texte), 18.10.2026.** Bezug: Matrix G2,
M30-41 §9, M19-19 (Zielbild durch M30-41 fortgeschrieben),
[ADR 0012](0012-bifrost-service-channel.md), [0005](0005-ha-outage-connectivity.md),
[0010](0010-clock-calendar-inputs.md). Keine Wake-Fachlogik oder Kalenderbearbeitung.

## Ziel, Modell und Datenformat

Ein neuer Quelladapter liest `calendar.get_events` mit Antwort. Ein Kalender ist eine
explizite Quelle; die Zuordnung zu einem HA-Kalender liegt nur in der Verknüpfung.
Schema für die Ereignisliste ist typisiert und in Formularen bearbeitbar. Cache und
Abrufzustand sind Laufzeitdaten, keine heimlich geänderte Registry.

```json
{"kind":"ha_calendar","entity_id":"calendar.example",
 "window":{"timezone":"Europe/Berlin","past_days":1,"future_days":14},
 "refresh_s":900,"max_age_s":3600}
```

Vorgeschlagene Grenzen: maximal 31 Kalendertage je Fenster, 1.000 Termine und 1 MiB
je Antwort; Überschreitung = sichtbarer Fehler, niemals eine unbemerkt gekürzte Liste.
`refresh_s` und `max_age_s` sind erforderliche, im Formular sichtbare Einstellungen;
die gezeigten Zahlen sind Vorschläge. Gültigkeit des Cache und Gültigkeit eines Termins
bleiben getrennt. Schlüssel: Installation, Binding-/Konfigurationsprüfsumme,
Kalender und konkretes UTC-Zeitfenster.

Antwort: `{window_start,window_end,fetched_at,expires_at,events:[{key,summary,
start,end,all_day,location?}]}`. Zeitfenster und Termine sind halboffen `[start,end)`.
Ganztägige Datumsangaben werden an lokalen Tagesgrenzen in Europe/Berlin aufgelöst;
23-/25-Stunden-Tage korrekt behandeln, kein UTC+24h. Originaldatum erhalten.
Zeitangaben mit fehlender Zone werden nur nach belegtem HA-Kalender-Zeitzonenvertrag
aufgelöst, sonst Antwort ablehnen. Gleiche Termine dürfen mehrfach existieren:
ohne gelieferte stabile ID identische Datensätze als Multimenge erhalten statt
verschiedene Kalendertermine aufgrund gleicher Titel zusammenzuführen.

## Aktualisierung, Ausfall und Beispiel

Erstabruf bei Aktivierung; weitere Abrufe über Clock: nächster Abruftermin,
lokaler Tageswechsel/Fensterwechsel sowie begrenzter Neuabruf nach Reconnect.
Kalenderzustandswechsel darf einen Abruf zusammenfassen, er enthält selbst keine
vollständige Terminsammlung. Parallele Anforderungen desselben Schlüssels bündeln.
Antwort vollständig validieren, atomar speichern und erst dann publizieren.
Unveränderte erfolgreiche Abfrage belegt den aktuellen Kalenderdatenbestand; sie
erfindet keine Geräte-Messzeit. Fehlender Antwortschlüssel ist nicht die leere Liste.
Eine explizit erfolgreich gelesene leere Liste ist dagegen gültig.

HA-Ausfall löscht nichts: letzter erfolgreicher Bestand bleibt bis `expires_at`
verwendbar und nur innerhalb seines belegten Fensters. Danach `unknown/input_stale`,
ohne künstliche leere Liste. Abfragefehler verlängert keine Frist; Bereitschaft und
Connectivity folgen ADR 0005. Wiederholung nur lesend, Vorschlag 5/30/120 s,
danach normaler deklarierter Abruftermin; kein sekündlicher Kalender-Poll.
Restart lädt Cache samt unveränderten absoluten Fristen; abgelaufener Cache wird
nicht frisch. Alte Binding-/Fensterantworten werden an ihrer Generation erkannt.

Beispiel: erfolgreicher Abruf 10:00, Gültigkeit eine Stunde, HA getrennt 10:10:
Bestand bis 11:00 verwendbar, danach unbekannt. Ein Termin am Folgetag bleibt
ein geplanter Termin; sein Start ist nicht der Zeitpunkt der Kalenderabfrage.

## Oberfläche, Tests und Akte

„Kalender hinzufügen“: Kalender auswählen, Zeitraum sowie „Neu lesen nach“ und
„Ohne neue Antwort gültig für“; Datum-/Zeitauswahl ohne JSON. Vorschau zeigt
„Zuletzt gelesen“, „Gültig bis“, Termine und „Kalender erneut lesen“. Kein privater
Termintext in allgemeinen Logs oder HA-Problemmeldungen.

A2-Tests: Antwortformat je Kalender, leer/fehlend/fehlerhaft, Mehrfachtermine,
All-day/DST/Jahreswechsel, Bereichsgrenzen, Cachetreffer, überlagerte Fenster,
Zeitlimit, HA-Verlust, abgelaufener Restore, Terminersatz/Löschung, begrenzte
Wiederholung, veraltete Revisionsantwort, Commit-Fehler, geführter Formularablauf.
Betroffen: M30-41 §9, G1/G2, Build-Spezifikation §8/§9/§13/§18/§21,
Adapter, Scheduler, Source-Schema, Retention/Restore und Vertrags-Evidence.
Neue Sprach-/Schemafähigkeiten werden explizit ausgewiesen; alte Bindings bleiben unverändert.

P8/b22 (#97): Optional prüft `calendar_coverage_months` an einer Feiertagsquelle
die künftige Abdeckung (1–24 Monate, Vorschlag 12). Die zusätzliche, lesende
Prüfung läuft in einzelnen Fenstern von höchstens 31 Tagen über denselben Kanal;
Termin- und Antwortgrenzen bleiben bestehen. Sie speichert nur belegte Daten und
Fristen zur Abdeckung, keine fernen Termine im fachlichen Cache. Ohne belegten
künftigen Termin bleibt der Eingang `unknown/calendar_coverage_missing` und die
Gesundheit eingeschränkt. Quelle und Vertrag zeigen das letzte belegte Ende oder
ausdrücklich „Ende unbekannt“. Neue Verknüpfung, veraltete Antwort und abgelaufener
Nachweis können keine Abdeckung bestätigen. Die normale 31-Tage-Auswertung und
deren absolute Gültigkeitsfrist werden durch die Prüfung nicht verlängert.

Quelle: [calendar.get_events — Parameter, Antwort und exklusives Ende](https://www.home-assistant.io/actions/calendar.get_events/).
