# ADR 0030 – Gemeinsame Aktualität und getrennte Nachweiskompatibilität

- Status: **Angenommen** – Go von Benni für A2 b4 am 23.10.2026.
- Datum: 23.10.2026; Design [#35](https://github.com/Levtos/wuschel/issues/35), Umsetzung [#37](https://github.com/Levtos/wuschel/issues/37).
- Implementierungsbasis: b3-Merge `23d200f101ca0f8b356361aeed02c688e209ab0c`.
- Ausarbeitung: [UX-A1 §§7–8 und Testliste](../beta/ux/A1.md).
- Grenzen: [ADR 0019](0019-sensor-profile-value-origin.md),
  [ADR 0026](0026-import-export.md), [ADR 0028](0028-grenze-wuschel-engines.md).

## Problem

Heute ist die Sensorvorgabe eine Kopie fester Werte, keine gemeinsam gepflegte
Registryreferenz. contact fällt auf event_stateful zurück (F4). Gleichzeitig
enthält der Quellenfingerprint die gesamte freshness-Regel (F3). Deshalb löscht
eine Friständerung bei activate die aktuelle Observation, verwirft Latches und
ändert transitiv Node-Fingerprints. Eine vorhandene passende Meldung geht
verloren; auch eine wirkungsgleiche Vorlagenzuordnung könnte Abläufe neu starten.

## Entscheidung: Registry statt globale Nebeneinstellung

Gemeinsame Aktualität ist Teil jeder übernommenen Konfiguration. Beschlossen:
config_schema_version=4 und fingerprint_version=4, weil die Versionen 1/2/3
bereits belegt sind. Registry-Feld freshness_templates enthält gemeinsam
genutzte vollständige Regeln mit stabiler template_id und sensor_kind.
Keine neue Vertragsvorlagenversion für eine geänderte Frist.

Jede Quelle besitzt in Schema 4 genau eine freshness_selection:

- shared: template_id, keine versteckten eigenen Regelüberschreibungen;
  bei liveness_source eine ausdrücklich quelleneigene Liveness-Referenz.
- own: vollständige eigene Regel, optional Vergleichsbezug auf eine Vorlage
  ohne Vererbung. Wertgleichheit ändert own nicht automatisch in shared.

Legacy-freshness und neue Auswahl dürfen nicht parallel gespeichert werden.
Das Backend löst die konkrete Freshness-Regel und ihre Herkunft konsistent auf;
Prüfung, Wirkungsvorschau, Bewertung und Scheduler benutzen dieselbe Auflösung.
Nicht referenzierte Vorlagen sind erlaubt, referenzierte nicht ohne vorheriges
Umstellen löschbar. Liveness darf nicht alle Sensoren implizit an ein beliebiges
Gerät binden. Zyklen und unpassende Referenzen bleiben Validierungsfehler.

Kontakte, Temperatur, Bewegung und Zähler sind Fachgruppen, keine Ableitungen
aus bool/number. Sie sind auch nicht die numeric sensor_profile-Prüfung aus
ADR 0019. Source.sensor_kind und seine bestätigte Herkunft erlauben eine
belegbare Gruppierung; unbekannt bleibt unbekannt. Namen allein sind kein Beleg.

## Bestand, Grundwerte und Export

Ein Programmupdate setzt keine gemeinsame Frist aktiv. Bestehende Regeln werden
verlustfrei als own lesbar; Benni bestätigt Gruppierung, gemeinsame Regel und
Ausnahmen im Entwurf. Grundwerte werden nicht aus Namen oder Ja/Nein-Werten
abgeleitet. Benni hat für das Kontaktformular ausdrücklich report_heartbeat
und 10800 Sekunden als Vorschlag beschlossen. Temperatur, Bewegung und Zähler
bleiben ohne vorgeschlagene Regel. Der Vorschlag ist keine zugesicherte
Geräteeigenschaft und keine aktive Zuordnung durch ein Update.

Portables format_version=2 gilt für Schema 4, weiterhin Leser für Format 1
und ältere Registry-Schemas. Referenzabschluss umfasst verwendete Vorlagen,
Liveness und unverändert benötigte Quellen/Verknüpfungen. Digests umfassen die
konkreten Konfigurationsobjekte. Eine Laufzeitbeobachtung wird nicht exportiert.
Ein Teilimport ändert keine bestehende globale Zielvorlage still; bewusste own-
Abweichung oder Zielbindung wählen. Ersatzimport darf gemeinsame Zielregel nach
ausdrücklicher Bestätigung und vollständiger Wirkungsanzeige ändern.

## Entscheidung: drei getrennte Vergleiche

1. **observation_fingerprint**: fachliche/physische Quellenidentität,
   Verknüpfung einschließlich Wert-/Zeitpfad und Adresse, Transform,
   Quellen-/Wertschema, Wertinterpretation und benutzte Katalogsemantik.
   Diese Identitätsänderungen behalten die strenge Neuaufnahmegrenze.
2. **assessment_fingerprint**: aufgelöste wirksame Aktualitätsregel,
   Liveness-Referenz und relevante Bewertungsabhängigkeiten. Eine andere
   Template-ID mit gleicher wirksamer Regel verändert ihn nicht.
3. **context_compatibility**: typbezogene Vereinbarkeit von Vertrag,
   Parametern, Eingängen, Baselines, Latches, Temporal-/Ablaufzuständen und
   nachgewiesener Kontinuität. Kein einzelner globaler Reset-/Keep-Schalter.

Nur Friständerung: Observation erhalten, neu bewerten, Timer aus ursprünglichem
Zeitbezug neu planen. Evidence-ID, ingest_seq, Quellzeit, received_at und
observation_kind werden nicht als neuer Bericht geschrieben. Ungeprüfte Felder
behalten konservative bisherige Invalidierung; keine beiläufige Ausweitung
auf neue Herkunfts-/Schema-/Transformkompatibilität.

### Kontexte und Safety

| Konstellation | Vorgeschlagene Folge |
|---|---|
| Wirksame Regel gleich | Observation, Baseline/Latch, since_at, Grace und absolute Deadlines unverändert. Kein unnötiger Ablaufneubeginn, UX-3-10. |
| Regel anders, Eignung und Kontinuität unverändert | Kompatible fachliche Kontexte behalten, Timer neu bewerten. Keine neue Episode, Quota oder Flanke allein durch Registryrevision. |
| Verkürzung macht Meldung unbrauchbar | Gültigkeit sofort neu bewerten; Kontinuität endet. Übernahme allein schärft keine neue Grace. |
| Verlängerung macht ursprünglichen echten Nachweis wieder verwendbar | Ursprünglichen Nachweis unter neuer Regel verwenden; vergangene Lücke bleibt, stable_for beginnt frühestens bei neu belegter Kontinuität. Kein neuer Report. |
| Grace läuft bereits | Deadline nicht verlängern; abgelaufene Grace nicht neu beginnen. Neu verwendbare Evidence darf gehaltenen Wert nach bestehendem Vertrag beenden. |
| Andere physische Quelle, Wertinterpretation oder Ablaufparameter | Bisherige Baseline-/A-06-Grenzen beibehalten, keine ungedeckte Kompatibilitätsbehauptung. |
| Liveness-Verfahren/-Zuordnung geändert | Neue Eignung prüfen, keine bloße Intervalloptimierung und keine rückwirkende Erreichbarkeit. |

Retain/Replays, fehlende/ungültige/future Messzeit, HA-restored, begrenzte
MQTT-Geräte-Wiederbewertung, Hysterese, Auffälligkeit und Wertart bleiben
getrennte Regeln. Harte Nachweisgates werden nicht für freundlichere Texte
gelockert. Die bestehende Intervall-Gleichheitsgrenze bleibt unverändert.

## Migration und Alternativen

Alte getrackte Registry und alte Fingerprints bleiben nachvollziehbar. Die neue
Kompatibilität wird aus gespeicherter alter Konfiguration und neuer effektiver
Regel nachgewiesen, nicht durch globales Umschreiben historischer Evidence-IDs.
Bei unklarer Kontinuität gilt die vorhandene konservative Lücke. Prüfung und
neue Bewertung werden atomar mit der Aktivierung publiziert. Falscher oder
fehlender Migrationsnachweis darf nicht zu erfundenem Eingang führen.

Alternativen: volle bisherige Invalidierung verliert Meldungen; nur Observation
behalten lässt unnötige transitive Resets bestehen; alle Kontexte behalten kann
Safety und Kontinuität verfälschen. Empfohlen ist die eng geprüfte Trennung mit
typbezogener Kompatibilität. Der Preis sind mehr explizite Vergleichsdaten und
eine gezielte F3-Regressionsmatrix, statt einer scheinbar kleinen Keep-Ausnahme.

## Tests, Schnitt und Review

Prüfumfang: UX-3-01…10, UX-4-01…06 sowie die F3-Matrix aus UX-A1 §12, jeweils mit
FakeClock für Alterung, Grace und Deadlines. Insbesondere identische effektive
Regel, kürzere/längere Frist, laufender/abgelaufener Hold, stabile/unterbrochene
Kontinuität, echte Identitätswechsel, Transformation und Katalogabhängigkeit.
Schema-/Export-Rundreise und Bestandszuordnung ausschließlich synthetisch testen.

Die Umsetzung und Prüfbelege stehen in [A2-b4](../beta/ux/A2-b4.md).
Beim Start werden unveränderliche aktive Revisionen und abgeschlossene
Entwurfsstände nicht nachträglich umgeschrieben. Die verlustfreie Schema-4-Kopie
entsteht beim bewussten Bearbeiten der neuen Aktualitätsfunktionen. Beim Übernehmen
wird die Kontextübernahme gegen die gespeicherte alte Registry und den exakten
alten Node-Fingerprint nachgewiesen und atomar publiziert. Ohne passenden
Fingerprint bleibt die bisherige Neuinitialisierung bestehen. F3 schützt auch
eine spätere Übernahme noch im alten Schema gespeicherter b3-Entwürfe.

Kompatible Frist-/Vorlagenänderungen lösen keinen neuen HA-Snapshot und keine
MQTT-Neuanmeldung mit Wiederholungen aus. Die bestehenden Verbindungen lesen
weitere echte Meldungen mit dem aktuellen Konfigurationsstand. Echte Bindungs-,
Herkunfts- oder Interpretationswechsel behalten die Neuaufnahmegrenze. Änderungen
am Lifecycle-Handshake (F6) sind kein Teil dieser Entscheidung.

Nach dem A2-PR gilt STOPP: kein Merge, Tag, Release oder Installation ohne neues Go.

## Präzisierung durch Abschluss-Beta #80

Der [Abschluss-Entwurf](../beta/abschluss/A1.md) und seine Umsetzung in
[A2](../beta/abschluss/A2.md) präzisieren den dritten Vergleich: Der eigene
Vertragsfingerabdruck enthält die eigene Definition/Version, Parameter und
direkten Feldschnittstellen. Eine gleich typisierte vorgelagerte Ableitung
macht einen Ablauf allein nicht inkompatibel. Verlaufsspeicher bekommen eine
zusätzliche Abhängigkeitsprüfung je Platz: `event_match`, zeitliche Basis von
`stable_for`/`dwell`/`grace`/`delta`/`age` und flüchtige Buckets. Ein unabhängiger
Platz bleibt unverändert. Machine-Memory und Captures gehören zur kompatiblen
Episode; ihre regulären Übergangsregeln gelten auch bei der Neuauswertung.
Die Übernahme bestätigt keinen manuell behaupteten Zustand durch neue Evidence.

Gespeicherte b15-Kontexte mit Fingerabdruckversion4 werden nur nach passender
Altprüfung gegen dieselbe gespeicherte aktive Konfiguration überführt. Beschädigte
oder unbelegte Kontexte erhalten keinen neuen Nachweis. Das öffentliche
Registry-/Exportformat bleibt unverändert, ebenso CP-15 und die strenge
Beobachtungsidentität. Die Identität laufender Refresh-Quoten verwendet weiter
den bisherigen Vergleich; die neue Hashdarstellung beginnt keine neue Quote.
Paket- und Entwurfsvorschau erfassen die gleichen konkreten Rücksetzungen wie
die Aktivierung, einschließlich der vorhandenen Kataloginvalidierung.
