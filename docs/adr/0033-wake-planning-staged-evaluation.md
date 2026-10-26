# ADR 0033 – Weckplan-Port und lokale Auswertungsphasen ohne Vertragszyklus

- Datum: 26.10.2026
- Status: **angenommen für A2 am 26.10.2026; technisches Review vor Veröffentlichung offen**
- Auftrag: [wuschel#61](https://github.com/Levtos/wuschel/issues/61)
- Normativer Fachumfang/Testmatrix: [Bereich 4 A1](../beta/bereich-4/A1.md)
- Bezüge: [0013](0013-calendar-cache.md), [0015](0015-state-machine-deadlines.md),
  [0016](0016-manual-source.md), [0028](0028-grenze-wuschel-engines.md),
  [0032](0032-ordered-machine-commands.md).

## Problem und Grenzen

Ein öffentlicher Vertrag für Schlafbeginn und ein von ihm abhängiger Weckplan
dürfen keinen Rückverweis aufeinander bilden. Mindestschlaf verschiebt einen
Termin erst nach tatsächlichem Schlafbeginn; der Ablauf braucht denselben
fertigen Termin für den Weckbeginn. Ein Tick Verzögerung, ein Fake-Sensor oder
eine zweite Berechnung in einer Engine würde diese Kopplung verbergen.
Außerdem kann G19 bisher keine Listen von Planregeln als strukturierte
Vertragsparameter beschreiben; Kalenderprojektionen beziehen sich auf heute.

## Beschlossene allgemeine Erweiterungen

Nur Sprachversion 6 ergänzt:

- `captures`: begrenzte lokale Zeitplätze mit deklariertem Eintrittszustand
  und `mode:first_in_episode`; neue Episode löscht, erster echter Eintritt setzt.
- `machine_memory(slot)`: reiner Zugriff auf einen lokalen Speicherplatz ohne
  rekursiven Aufruf der Machine-Regel. Fehlender Anker ist ausdrücklich kein
  tatsächlicher Schlafbeginn; der Plan darf ihn als noch nicht gesetzt behandeln.
- `machine_field(field)`: typisierte Ausgabeprojektion der endgültigen Machine-
  Metadaten, nur in Ausgängen; keine Verwendung in Guards/Planvorbedingungen.
- `absolute_transition.at_rule` alternativ zu `at_input`: typisierte
  zeitpunktliefernde lokale Regel. Genau eine Alternative; fehlender Termin
  entfernt nur den absoluten Alarm, niemals die unabhängige Dauerfrist.
- `structured_schema` für Objektparameter: ausschließlich registrierte
  versionierte Backendmodelle, `extra:forbid`, keine freie Ausdruckssprache.
  Zunächst `wake_planning_v1`; konkrete Felder und Grenzen im A1 §6.
- `calendar_at` und `calendar_active.on_date`: Auswertung eines expliziten
  lokalen Kalenderdatums mit vollständigem Fensternachweis; gültig leer bleibt
  von nicht abgedeckt/veraltet/fehlerhaft unterschieden.
- `daily_window`: Intervall aus datumsabhängigen Anfangs-/Endgrenzen mit
  deklariertem Nachtbezug, Planung über vorhandene Clock. Konkretes
  Freigabemodell steht im A1 §5, ohne zweite Tagesphasenformel.

Capture-Namen sind eindeutig, maximal 16 pro Ablauf. Auf Referenzen bekannte
Zustände prüfen; keine Capture-Regel darf selbst eine Auswertung auslösen.
`at_rule` darf nur externe Inputs, Parameter, reine Regeln und lokale
Speicherplätze lesen, niemals `machine` oder `machine_field`. Dies wird auch
transitiv durch Referenzen statisch geprüft. Der äußere Vertragsgraph bleibt
azyklisch; ein lokaler Speicher ist eine Zustandsvariable, kein rückgekoppelter
Vertrag. Alle neuen Regeln sind im Capability-/Formularschema explizit sichtbar.

## Auswertungsreihenfolge

Ein Durchlauf lädt den committed Zustand, qualifiziert aktuelle Ereignisse,
nimmt höchstens einen aktuellen Command-/Eingangsübergang, aktualisiert
Capture/Frist und berechnet dann den Plan. Anschließend folgen fällige
Zeitbedingungen nach ADR 0015. Setzt ein tatsächlicher Zustandseintritt einen
neuen Speicherwert, wird der Plan einmal invalidiert und erneut gebildet.
Alle Ausgabeprojektionen lesen danach denselben endgültigen Stand.

Die erlaubten Phasen bilden einen gerichteten lokalen Graphen, keine
Fixpunktsuche: Eingang → Zustand/Capture → Plan → Zeitübergang → Ausgänge.
Ein durch Zeitablauf erstmals gesetzter Capture erlaubt einmal
Zustand/Capture → Plan → verbleibenden Zeitübergang. Die Schlafvorlage kann
dadurch höchstens `pre_sleep → sleep → waking` innerhalb einer Zeitprüfung
durchlaufen. Die Validierung begrenzt und verbietet andere rückläufige
Abhängigkeiten; eine dynamisch nicht terminierende Definition ist ungültig.
Die Reihenfolge der JSON-Ausgaben beeinflusst weder Zustand noch Plan.

Ein absoluter Übergang speichert seine verarbeitete Kandidatenkennung pro
Episode. Derselbe bereits ausgelöste Plan wird nicht nach Restore oder einem
Command zur Rückkehr in den vorherigen Zustand erneut ausgelöst. Neue
Plankennung oder neue Episode erlaubt wieder eine Auslösung; genauer Schlüssel
und Regeln stehen im A1 §7. Verarbeitete und blockierte Termine bleiben getrennt.

## Port und fachliche Vorlagen

Der einzige ausdrücklich erlaubte Fachcode ist der reine Port von
`benni-core-state` bei `c9408fa33f2914b1c5f7316738cb38b43add33b5`:
`wake_planning.py` und `wake_config.py`. Er wird als versionierter Regeloperator
`wake_plan` mit typisierter Feldauswahl angeboten. Sein Code liest keine HA-
Entitäten, keine Uhr außerhalb der Clock und keine Legacy-Konfiguration.
Profile/Regeln/Floor/Fenster/Feiertage/Kalendermarker/Routine/Konfliktverhalten
und Suche heute bis +30 bleiben in der Grundplanung unverändert.

Die lokale Fachvorlage `sleep.flow` verbindet diesen Operator mit dem
deklarativen Ablauf, erfasst den ersten Eintritt in sleep und projiziert
seinen Plan. Die öffentlichen Verträge `sleep.state` und `wake.plan` lesen
nur diesen Knoten. Es gibt keinen Planrückverweis und keine zweite fachliche
Planinstanz. Der genaue Ablauf, Herkunft, Fristen und Ereignislisten sind
Vorlagendaten, keine eingebauten Python-Schlafzweige.

Mindestschlaf und manuelle Tagesausnahme aus #61 sind separate Erweiterungen
über dem originalgleichen Grundplan. Mindestschlaf: nur freie Wecktage,
UTC-Dauer ab tatsächlichem ersten Schlafbeginn, Anpassung jedes Kandidaten
vor Auswahl, Fenster entsprechend verschieben. Eine manuelle Ausnahme nutzt
die bestehende adresslose Quelle nach ADR 0016 mit explizitem Datum und
begrenzter Gültigkeit. Sie ändert nicht rückwirkend den Grundplan.

Das öffentliche Ergebnis trennt heutigen Status vom nächsten Kandidaten und
dessen Datum/Fenster. Keine rohe Kalenderinformation in Evidence oder Trace,
kein `wake_needed`. Engines übernehmen keine Neuberechnung. Der bestehende
Kalenderadapter allein lädt einen belegten 31-Tage-Bestand; Datumsauswertung
und Planoperator stellen keine zusätzlichen Netzabfragen.

## Restore, Formate und Validierung

Capture, Eintrittsmetadaten, verarbeitete Plankennung, Frist und Zustand
werden als zusammengehöriger JSON-Knotenzustand mit lokaler Formatkennung
committed. Zeitspeicher nie aus `pre_sleep`, Empfangszeit oder Restore-Uhr
rekonstruieren. Ein fälliger Restore verwendet aktuelle Evidence, keine
neue volle Dauer und keine nachgespielte Interaktion. Fehlende/inkompatible
neue Laufzeitfelder liefern Restore-Diagnosen statt erfundener Historie.

Regelsprache 6 ist additiv; Registry 4, Fingerabdruck 4, Paket 2 und
Traber-Protokoll 2 bleiben. Alte Definitionen und ihre fehlenden Defaults
werden nicht verändert. Bestehende JSON-Spalten, `command_log`, manuelle
Requests und Kalendercache reichen aus; keine DB-Migration vorgesehen.
Falls die Umsetzung davon abweichen muss, zuerst Designnachtrag, Review und
Benni-Go; vor einem DB-migrierenden Release gilt Bennis LXC-108-Sicherung.

Die Parameterprüfung ist strikt und begrenzt. Strukturierte Modelle sind
typisiert, unbekannte Schemafassungen/Felder und ungültige Regeln werden
abgewiesen. Formular, Import und Runtime verwenden dieselbe Prüfung.
Fehlende Fähigkeit am Ziel wird vor Übernahme gemeldet. Ein abgelaufenes
Kalenderfenster wird nie zu „kein Feiertag“ geraten. Private Verknüpfungen
bleiben ausschließlich im Codex-Ausgabeordner, nicht im öffentlichen Paket.

## Nachweis und Konsequenzen

A1 liefert reproduzierbare Proben am unveränderten Ist-Code und synthetische
Originalergebnisse; es behauptet keine fertige neue Pipeline. A2 muss zusätzlich
den wirklichen Port gegen das gepinnte Original über mindestens drei Jahre
Kalendertage mit **0 Abweichungen** im Grundplan prüfen. Neue Ausnahmen und
Mindestschlaf werden separat nach ihren Sollregeln geprüft.

Weitere Pflichtprüfungen: statische Phasen-/Zyklusabwehr, Ausgabereihenfolge,
erstmaliger Capture im Zeitübergang, gleiche Planzeit an Alarm und Publication,
verpasste Basiszeit bei Mindestschlaf, W4/W5 ohne Wiederholung desselben Plans,
beide Zeitumstellungen, Tag +30, heutiger Skip mit künftigem Termin, verlorene
Evidence, Commitfehler/Restore und alte Sprache/Prüfsummen. Lokale Tests bleiben
gezielt, die volle Suite läuft ausschließlich in CI. Die spätere Oberfläche
bearbeitet dieselben Parameter; sie enthält keine eigene Planberechnung.


A2 übernimmt K1–K5 aus Claudes Review sowie die gemeinsame Kalenderquelle
und das unveränderte Fensterlimit aus dem [Issue-Nachtrag](https://github.com/Levtos/wuschel/issues/61#issuecomment-6415883723).
Regex wird begrenzt inline ausgewertet; kein eigener Prozess.
