# Importierbare Anwesenheitsvorlagen

`presence-templates-v1.json` enthält neun deklarative Vorlagen, jeweils Version 1.
Sie werden ausschließlich als Konfiguration importiert; Wuschel registriert
keine Anwesenheitsverträge automatisch im Programmcode.
Die Vorlagenliste ist die Datenquelle für die Paketerzeugung; eingelesen
wird ein vollständiges Konfigurationspaket, nicht die Liste allein.

Der vollständige synthetische Paketnachbau liegt unter
[`tests/fixtures/presence-package-v1.json`](../tests/fixtures/presence-package-v1.json).
Seine künstlichen Verknüpfungen und WLAN-Parameter sind nur Prüfdaten. Die
zugehörige Ausgangskonfiguration steht in
[`tests/fixtures/presence-baseline-v1.json`](../tests/fixtures/presence-baseline-v1.json).
Das echte private Paket wird getrennt auf Basis eines aktuellen API-Exports
erstellt und von Benni selbst eingelesen und übernommen.

Alle Vorlagen und Verträge gehören zur Kategorie `presence`. Ort, Entfernungsband,
Richtung und freier Ortsname bleiben getrennte Fakten. Zuhause und Eltern
gelten für Ankunft, Abfahrt, Durchfahrt und Haushalt gemeinsam als „nicht weg“.
Neue HA-Projektionen sind ausgeschaltet. Eine manuelle Besuchsquelle enthält
im Paket keine vorweggenommene Eingabe.

`presence.location` erwartet die Textparameter `home_wifi_1`, `home_wifi_2`
und `parents_wifi`; `presence.place` erwartet `home_label`. Beide benötigen
`grace_s`. Die drei Abläufe verwenden `hold_s`, das Entfernungsband sechs
Grenzparameter. Die synthetische Lebenszeichenfrist ist ein Prüfparameter;
für das private Paket wird sie aus der freigegebenen Fahrtmessung festgelegt.

Voraussetzung ist Wuschel 1.0.0b7 mit `first_usable` und der Gerätezeitkorrektur.
[Design und Freigaben](../docs/beta/bereich-2/A1.md) beschreiben die Semantik.
