# b12 – Importvorschau

Auftrag und aktueller Nachweis: [Issue #64](https://github.com/Levtos/wuschel/issues/64).

Die Importvorschau zeigt Konflikte zuerst. Bei vollständigen Konfigurationspaketen
ersetzt „Aus Datei übernehmen“ das vorhandene Objekt unter derselben Kennung im
geplanten Entwurf. Für einzelne Vertragspakete bleibt diese Auswahl unzulässig.
Katalogeinträge können ebenfalls ausdrücklich aus der Datei übernommen werden.

Der Server liefert `editorial_changed` je Vertrags-/Vorlagenkennung. Nur neue
oder bei Kategorie/Raum abweichende Angaben erscheinen zur Auswahl. Unveränderte
Angaben behalten ihre bisherigen Werte; Version und Prüfbindung bleiben Teil des
Importplans. Vertrag und Vorlage gleichen Namens sind getrennt beschriftet.

Unten bleiben „Vorschau neu prüfen“, „Als Entwurf einlesen“ und der Sperrgrund
zusammen erreichbar. Offene Konflikte sind über einen Sprung erreichbar.
Während einer Neuprüfung, bei Fehlern oder überholtem Prüfstand bleibt Einlesen
gesperrt. Alte Antworten dürfen eine neuere Auswahl nicht freigeben.

Plan-Prüfsumme, Revisionsbindung, Entwurfsprüfung und bewusste Übernahme bleiben
streng. Registry-Prüfsummen unterscheiden weiterhin ganze Zahlen und
Gleitkommazahlen. Bestehende Vorlagen und Aktualitätsregeln werden nicht geändert.
Keine Datenbankmigration. Wuschel trägt 1.0.0b12, Traber bleibt auf 1.0.0b11.

## Nachweise und Grenzen

- Gezielte Frontendtests prüfen Neuprüfung, Ladeanzeige, Sperrgrund, Konfliktsprung,
  Dateiauswahl und die gefilterte Organisation mit 40 unveränderten Angaben.
- Backendtests prüfen gleiche Kennungen, stabilen Plan-Digest, veränderte Auswahl,
  Revisionswechsel und das Verbot der Dateiersetzung bei einzelnen Vertragspaketen.
- `workbench-e2e` ergänzt einen vollständigen synthetischen Ablauf gegen die reale
  isolierte API: 42 vorhandene Kategorie-/Raum-Angaben, davon 40 unverändert,
  zwei Konflikte sowie ein neuer Vertrag. Der Ablauf endet vor dem Einlesen und
  prüft unveränderte aktive Prüfsumme und unveränderten Entwurfsbestand.
- Diese CI-Prüfung verwendet den vorhandenen DOM-/API-Prüfaufbau. Der lokale
  Firefox-Nachweis entfällt gemäß Claudes Entscheidung im Issue. Den anschließenden
  Live-Nachweis über die HTTPS-Oberfläche übernimmt Claude, nur bis zur Vorschau.
- Private Pakete bleiben im lokalen Ausgabeordner und heißen
  `Import-Bereich-<n>.json`, mit `Import-Bereich-<n>-Hinweise.md` als Begleittext.

Installation, tatsächliches Einlesen und Übernahme gehören nicht zu diesem Auftrag.
