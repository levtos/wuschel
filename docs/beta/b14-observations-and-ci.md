# b14: Beobachtungen, Schlafanzeige und gezielte CI

Gemeinsamer Auftrag [#68](https://github.com/levtos/wuschel/issues/68) und
[#71](https://github.com/levtos/wuschel/issues/71), zuerst CI/Regeln, danach Produkt.
Ein Wuschel-PR und ein Release; zentrale Regeln separat in
[control#63](https://github.com/levtos/control/pull/63).

## Beobachtungen bei einer Übernahme

Der HA-Adapter vergleicht die tatsächlich abonnierten Entitäten und Berichtskanäle.
Bleiben diese gleich, bleibt die WebSocket-Verbindung bestehen. Änderungen an
unabhängigen Verträgen erzeugen dadurch weder einen neuen Snapshot noch ein
`input_restored`. MQTT behält bei identischen Verknüpfungen sein Abonnement und
seinen Duplikatspeicher. Neue Meldungen tragen die neue Registry-Revision.

Die vorhandene Fingerabdruck-Prüfung entscheidet weiterhin, ob die Beobachtung
der jeweiligen Quelle erhalten werden darf. Messzeitpunkt, Eingangsfolge,
Geräte-/HA-Zeit und Ablauf werden nicht erneuert. Eine geänderte Verknüpfung
startet ohne Wert und wartet auf eine neue passende Meldung. Bei einer bekannten
HA-Verbindungslücke während der Übernahme wird verbleibende HA-Beobachtung als
wiederhergestellt behandelt. Eine wieder verbundene Leitung allein liefert
keinen frischen Wert. Die gewöhnliche Alterung bereits belegter Werte während
einer Trennung bleibt erhalten; der Prozessneustart bleibt konservativ.

Synthetische Tests verwenden echte FakeHA-WebSockets und eine separate neue
Runtime beim Neustart. Sie prüfen insbesondere den alten 300-Sekunden-Ablauf,
einen Attributwechsel derselben Entität, HA-Trennung und MQTT-Duplikatgedächtnis.

## Schlafzustand und Weckplan

`sleep.state_view` zeigt in der Liste Zustand und gegebenenfalls Frist;
`wake.plan_view` nächstes Wecken und Planstatus. `sleep.flow` heißt dort
„Schlafablauf (intern)“ und wiederholt die Detailfelder nicht. Die Übersicht
kennzeichnet den internen Ablauf ebenfalls.

Die Detailansicht behält alle Felder einschließlich Durchlauf-ID. Bekannte
Zustände, Eintrittsgründe und Weckentscheidungen werden nur für diese Typen
übersetzt; beliebiger Nutzertext bleibt unverändert. Unbekannt bleibt unbekannt.
Verlauf, Feldüberschriften und Werttexte verwenden dieselbe Darstellung.
Öffentliche Beispiele ändern ausschließlich deutsche Anzeigenamen.

Die zusätzliche CI-E2E prüft Liste und Detail mit synthetischen Verträgen gegen
die echte isolierte Engine. Anschließend prüft sie die Vorschau einer reinen
Namenskorrektur; sie liest das Paket nicht ein und verändert die aktive
Konfiguration dabei nicht.

## Privates Korrekturpaket

`Import-Bereich-4-Korrektur.json` bleibt im privaten Codex-Ausgabeordner.
Es basiert auf dem lesend exportierten aktiven Stand und verändert ausschließlich
Anzeigenamen und Organisationsangaben der betreffenden Verträge. Die gültige
Paketform enthält unveränderte Referenzkonfiguration; sie darf keine bestehenden
Objekte durch ein unvollständiges Konfigurationspaket entfernen.

Die isolierte Kandidatenvorschau belegt: keine Konflikte, keine hinzugefügten
Fachobjekte, keine Katalogänderungen und identische semantische Fingerabdrücke.
Vorhandene Räume bleiben erhalten, wenn keine sichere Zuordnung vorliegt.
Namensänderungen sind in der Importvorschau ausdrücklich sichtbar. Echte
Fachänderungen erfordern weiterhin eine Konfliktentscheidung. Das Einlesen und
Übernehmen macht Benni nach der Installation von b14.

## CI-Nachweise

| Nachweis | b13 | #68 mit Cache und zusätzlichem Identitätsnachweis |
|---|---:|---:|
| Gesamtdauer | 7:32 | 7:01 |
| amd64-Image-Job | 2:05 | 2:16 |
| ARM-Image-Job | 5:09 | entfällt |
| erster amd64-Bauschritt | 0:26 | 0:28 |
| zweiter identischer Bau | entfällt | 0:06, identische Image-ID |

Belege: [b13](https://github.com/levtos/wuschel/actions/runs/40480505829),
[#68](https://github.com/levtos/wuschel/actions/runs/40488834447).
Einzelne Runner-Zeiten sind keine kontrollierte Leistungsstatistik. Der neue
Image-Job enthält zusätzlich den zweiten Bau und Identitätsvergleich; er ist
in diesem Vergleich nicht kürzer. Der Cache-Nachweis und der entfallene ARM-Bau
sind separat belegt. Die vollständige Pipeline bleibt durch E2E begrenzt.

Ein gesonderter reiner Dokumentationslauf nach dem Produkt-Merge belegt die
erfolgreich übersprungenen Anwendungsjobs. Er ist kein Release-Nachweis.
Vollständige PR-CI, Publikation mit anonymem amd64-Pull und vollständige CI des
Store-Commits bleiben erforderlich; Details in [publishing.md](../publishing.md).

Keine DB-Migration. Traber bleibt auf b11. Veröffentlichung, Installation und
Live-Abnahme sind getrennte Schritte; dieser Auftrag übernimmt nichts in HA.
