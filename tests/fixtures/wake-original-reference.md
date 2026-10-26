# Synthetische Originalreferenz für Bereich 4

`wake-original-2032-2034.json.gz` enthält ausschließlich synthetische Daten:
1.096 Kalendertage (01.01.2032 bis 31.12.2034), 17 Varianten, 18.632 vollständige
`plan_wake(...).as_attributes()`-Ergebnisse sowie die Original-Standardkonfiguration.
Die Eingaben stehen in `tests/wake_reference_cases.py`; der Test lädt ausschließlich
die erwarteten Originalergebnisse und vergleicht jedes Feld mit dem echten Port.

Quelle: `Levtos/benni-core-state`, Commit
`c9408fa33f2914b1c5f7316738cb38b43add33b5`.

- `wake_planning.py`: SHA256 `33dea1fb5c4868b68a36defea7d41ab889e540e7b7dd9350a72ec4d0dc069e0a`
- `wake_config.py`: SHA256 `5c5f3790cf3d9fcf140aac31f6ae446435728a92718499910168f155f7fd1315`
- Unkomprimierte kanonische JSON-Datei: SHA256 `5f36503ab80f932ea21dec8448c68ce950f1cfbcdee6adfbb3b325abfe369098`

Reproduktion: `python dev/generate_wake_reference.py /pfad/zum/original-checkout`.
Der Generator prüft die gepinnte Quelle über die A1-Ladefunktion und führt deren
reine Module aus. Er importiert dabei keinen Wuschel-Port. Keine Netzabfrage,
keine Produktionskonfiguration. Gzip-Zeitstempel ist null.

Varianten: Profile, Mittagsprofile, Feiertage, Urlaub, Skip- und Weckmarker,
alle drei Kalenderkonfliktmodi, Untergrenze, Datum, Zeitraum, Zyklus,
Wochenintervall, Priorität, inaktive Planung und Feiertag ohne passende Regel.
Mindestschlaf und manuelle Tagesausnahmen sind neue Issue-61-Erweiterungen
und werden separat in `test_beta_wake.py` geprüft.
