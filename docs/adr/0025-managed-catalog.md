# ADR 0025 — G4: verwalteter Hybrid-Katalog, gezielte Wirkung

20.10.2026 · Detaildesign **A1 freigegeben für A2** zur beschlossenen Variante 3 aus
[ADR 0007](0007-hybrid-catalog-decision.md). Grundlage: Matrix G4,
M30-27/M30-34, M07-09/10, A-06, M30-17/M30-30 und G14.
Keine Title-Classifier-Fachregeln oder Activity-State-Verträge in diesem Scope.

## Speicher und Definition

Drei getrennte Ebenen: unveränderliche Definition in der Konfiguration,
append-only gepflegter Inhalt, nichtsemantische Sichtungen als Laufzeitdaten.
Bestehende Inline-`Catalog(kind:map)` und Schema-/Fingerprintversionen 1/2
bleiben lesbar und bytegleich serialisierbar. Kein heimliches Umwandeln alter
Maps. Neue Hybriddefinitionen verlangen ausdrücklich config_schema_version=3;
für sie fingerprint_version=3. Neue geschlossene Fähigkeit
`managed_catalog_lookup` wird nur von einer ausdrücklich neuen Definition
(language_version=4) verwendet. Kein automatisches Hochsetzen beim Öffnen.

Definition:

```json
{"catalog_id":"example.titles","kind":"managed","version":1,
 "identity_rule":{"version":1,"fields":["scope","media_type","signal_type","normalized_key"],
                  "normalizer":"normalize_key"},
 "classification_schema":{"type":"enum","values":["other","example"]},
 "unassigned_value":"other","master_depth":1,"lifecycle":"active"}
```

Das Beispiel ist synthetisch. Klassen und Scopewerte stammen aus der von Benni
bestätigten Definition, nicht aus eingebauten TV-/Musikregeln. Unzugeordnet
liefert den deklarierten gültigen Wert plus G14-Hinweis
`catalog_assignment_missing`; ein verwendbarer Titel wird deshalb weder
unknown noch Signalalarm. Tatsächlich unbrauchbarer Eingang bleibt unknown.
Die neue Lookup-Expression referenziert catalog_id/version und typisierte
Eingänge für Scope, Medienart, Signalart und Schlüssel. Keine HA-Adressen in
Ausdrücken. Titel bleibt als rohe Source-Evidence erhalten; die Klassifikation
gehört der Resolverauswertung, nicht einem schreibenden Adapter.

Ergänzung Bereich 5 (#75): `classification_schema.legend` ist optional; wenn
vorhanden, enthält sie genau die Klassenschlüssel mit je `display_name`
(1–80 Zeichen) und `description` (1–300 Zeichen), beide nicht leer nach Trimmen.
Ohne Legende bleibt die historische Serialisierung unverändert. Die Legende
ist Teil der unveränderlichen Definition und ihres Digests. Änderungen verlangen
eine neue Definitionsversion und bewusste Inhaltsübernahme; alte Revisionen
werden nicht neu beschriftet. Bedienung und v3-Import: [Bereich 5 A2](../beta/bereich-5/A2.md).

Identität K = kanonisches JSON-Tupel aus (catalog_id, definition_version,
scope, media_type, signal_type, normalize_key(key)); kein mehrdeutiges
String-Zusammenkleben. Normalisierung verwendet den gepinnten bestehenden
Operator, inklusive dessen Unicode-Regel. Rohschreibweisen werden als
Anzeige/Herkunft aufbewahrt. Gleicher Titel in anderem Scope bleibt separat.
Änderung der Normalisierungsregel verlangt neue Definitionsversion und
explizite Migration/Review, keine spontane Zusammenlegung.

Tabellen (alle auf demselben serialisierten Writer):

| Tabelle | Inhalt / Invariante |
|---|---|
| `catalog_content_revision` | monotone Revision pro Katalogdefinition, Request-ID, Elternrevision, Autor, Zeit, Grund, Herkunftsdigest; append-only |
| `catalog_entry_revision` | K, content_revision, classification oder master_key, tombstone, Anzeigename/Notiz; immutable |
| `catalog_head` | Zeiger auf committed Inhalt, OCC-Generation; austauschbarer Index |
| `catalog_sighting` | K, first_seen/last_seen/seen_count und erlaubter Kontext; ändert keine Klassifikation |
| `catalog_request` / `catalog_import` | idempotente Anfrage/Importkennung, Inhaltsdigest und Ergebnis; append-only |

Unique-Constraints auf Request-ID und (Definition,Revision,K).
Alle FK-/Definitions-/Typ-/Lebenszyklusprüfungen serverseitig.
Ein Tombstone löscht keine Vergangenheit; neue Zuordnung ist neue Revision.
Ältere Revision ansehen/als neue Revision übernehmen ist ausdrücklich möglich,
niemals UPDATE einer historischen Zeile. Definition deprecated bleibt für
bestehende Nutzung zulässig; retired sperrt neue Verwendungen nach G5.

## Master und Varianten

Ein Master hat keinen Elternschlüssel, eine Variante genau einen Master
derselben Definition und desselben (scope,media_type,signal_type).
Varianten erben die Klassifikation des Masters vollständig. Kein eigener
Klassenoverride in Alpha 10, keine Enkel, Zyklen oder Auto-Merges.
Eigener Anzeigename/Notiz ist nichtsemantisch.
Master mit Kindern darf nicht selbst Variante werden; Auflösen eines Masters
braucht eine atomare Entscheidung über alle Kinder (neu zuordnen oder
selbständig mit ausdrücklich bestätigter Klassifikation). Keine hängenden FKs.
UI zeigt vorher die betroffenen Varianten.

## A-06: genaue Regel nur für betroffene Schlüssel

Statischer Fingerprint enthält Definition/Version/Identitätsregel,
Lookup-Ausdruck, Inputs und relevante Parameter, **keinen globalen Headzähler**.
Jede Auswertung erfasst ihren Lookup-Trace, auch einen negativen Treffer
`unassigned`: K, verwendete Inhaltsrevision, Masterkette (höchstens ein Link),
wirksame Klassifikation, Vollständigkeit und kanonischer Semantikdigest.
Gleicher Digest bei neuer Revision ändert Evidenzreferenz, aber nicht die Basis.

Änderungsmenge D: genau die Schlüssel, deren wirksame Klassifikation,
Masterzuordnung mit fachlicher Wirkung, Tombstone oder Vollständigkeitsbefund
sich zwischen altem und neuem Stand unterscheiden. Bei Masteränderung werden
seine erbenden Varianten nach altem **und** neuem Stand in D geprüft.
Anzeige/Notiz, Sichtungen, unveränderte Doppelimporte und reine Headbewegung
ergeben D=leer.

Betroffen sind Lookup-Verwendungen, deren letzter bestätigter oder noch
zustandsrelevanter Trace einen Schlüssel aus D enthält, einschließlich
unzugeordneter Treffer, und ihre transitiven abhängigen Ergebnisse/Zustände.
Traces werden je zustandsbehaftetem Slot gespeichert, nicht nur für den
aktuell sichtbaren Zweig. Anderer Schlüssel im selben Katalog bleibt unberührt.
Beim späteren erstmaligen Lesen eines geänderten Schlüssels wird dessen
aktueller Digest vor Verwendung des zugehörigen Altzustands verglichen.
Fehlender Trace beweist keine Kompatibilität: nur der betreffende
Lookup-Zustand und seine Abhängigkeiten werden als nicht validiert behandelt,
kein pauschaler Reset anderer Kataloge oder unabhängiger Verträge.

Für betroffene Vergleichs-/Temporal-Slots wird nach A-06 eine frische Basis
aus vorhandener verwendbarer Evidence gesetzt. Keine künstliche Flanke,
keine neue Quellmesszeit, Grace, Durchlauf-/Sitzungsbeginn oder Wiederholung
eines Befehls. Betroffene SM-Kontexte werden nach CP-15 auf Kompatibilität
geprüft; nicht belegbare Übernahme wird sichtbar restore_pending/unknown,
keine stillschweigende Neuinitialisierung einer Fachsitzung.
Unbetroffene Slots behalten Fingerprint, Baseline, Latch und Fristen.
Die generische Regression muss zwei verschiedene Schlüssel desselben Katalogs
in verschiedenen stateful Verbrauchern nachweisen.

## Sofortige Wirkung und Atomarität

„Zuordnen“ ist eine ausdrückliche administrative **Laufzeitpflege**, kein
Registry-Entwurf. Die UI erklärt vor Bestätigung: „Die Zuordnung wirkt sofort.“
Ändern einer Definition bleibt Entwurf → Prüfen → Übernehmen.

1. Auth/CSRF und Request-ID prüfen; erwartete aktive Registryrevision,
   Definitionsversion und Katalog-Head verlangen.
2. Im serialisierten Processor den alten Zustand klonen und alle Prüfungen
   gegen genau diesen Stand durchführen; Race mit Registryaktivierung = 409.
3. Eintrags-/Inhaltsrevision und D bilden, betroffene Kette mit alter, noch
   gültiger Evidence neu berechnen. Zeit und Frische bleiben unverändert.
4. In **einer** DB-Transaktion Revision, Head, Audit/Anfrageergebnis,
   Folgezustände, A-06-Kontexte, Publication und eventuell daraus entstehende
   Outbox-Aufträge committen. Keine Netzwerkdienste in der Transaktion.
5. Erst nach Erfolg In-Memory-Stand tauschen, Snapshot/WS publizieren und
   Scheduler/Outbox abgleichen. Response benennt committed Revision/Publication.

Bei Validierungs-/Commitfehler bleibt der alte Inhalt samt Ergebnissen gültig.
Keine Antwort „gespeichert“, wenn nur die Revision ohne Neubewertung vorliegt.
Crash nach Commit/vor Response: dieselbe Request-ID liefert das gespeicherte
Ergebnis. DB-Ausfall: keine optimistische grüne Anzeige/Publication.
Leser sehen konsistent alten oder neuen Stand, niemals neuen Head mit altem
Ergebnis. Snapshot- und Exportleser verwenden denselben Snapshot-/Commitzaun.

## Gleichzeitiges Bearbeiten und begrenzte Last

Pro Mutation expected_content_revision, expected_active_revision und UUID
request_id; gleiche ID/gleicher kanonischer Inhalt liefert dasselbe Ergebnis,
gleiche ID/anderer Inhalt = Konflikt. Auch Änderungen an verschiedenen Einträgen
werden bei altem Head zunächst 409; UI lädt Vergleich und verlangt bewusstes
erneutes Speichern. Kein Last-write-wins und kein stilles Retry über fremde Änderung.
Sichtungen konkurrieren nicht mit Inhalts-OCC. Snapshot-Neuladen verliert keine
ungesendeten Formulare; UI bietet eigenen/aktuellen Stand zum Vergleichen.

A2-Grenzen: Seiten höchstens 100 Einträge, Suche/Paging serverseitig;
ein Pflegevorgang höchstens 100 direkte Änderungen, inklusive Masterfolgen
höchstens 1000 betroffene Schlüssel, sonst sichtbar ablehnen. Kein Abschneiden.
Import maximal 50 MiB/100.000 Einträge (streamend vorprüfen); alle anzuwendenden
Änderungen als ein serialisierter Commit. A2 muss Speicher-/Transaktionslast
gegen den tatsächlich gelesenen Bestand messen; überschrittenes Budget
blockiert den Import, statt Atomarität in versteckte Teilcommits aufzulösen.

Sichtungen sind belegte neue Quellreports, dedupliziert über Evidence-ID:
Replay, Snapshot, Re-Evaluation und Retain zählen nicht. Kein Überschreiben
von Klassifikation, kein Verlängern der Freshness. Sichtungs-Retention ist
eigene Runtimepflege; Löschen ihrer Zähler löscht keine Katalogentscheidung.

## Pflegeoberfläche

Navigation: „Katalogpflege“, Auswahl des Katalogs; „Wertelisten“ bleibt die
bisherige Konfigurationsfunktion. Titel „Titelkatalog“, Satz
„Hier kannst du neue Titel zuordnen und vorhandene Zuordnungen pflegen.“
Hauptknopf „Titel zuordnen“ öffnet die Prüfliste; innerhalb dieser Seite
keine zweite gleichwertige globale Hauptaktion.

Ansichten „Noch nicht zugeordnet“, „Alle Titel“, „Master und Varianten“,
„Änderungsverlauf“. Filter für Scope/Medienart/Signalart, Suche, erste/letzte
Sichtung, gültige Klasse und klarer Vollständigkeitshinweis. Keine notwendige
JSON-Eingabe. Detail: „Selbständig zuordnen“ oder „Als Variante zuordnen“,
passender Master wählbar; Zahl der betroffenen Ergebnisse vor Bestätigung.
Kein automatisches Annehmen eines ähnlichen Namens. Fehler/Leere/Laden/409
werden nach der Seitenregel erklärt; Wuschel-Figur auf leerer Prüfliste.
Katalogrevision und Semantikdigest stehen unter „Technische Details“.

## Backup, Restore und Konfigurationsrücknahme

Vollständiges DB-Backup enthält alle fünf Haltungen, Runtime-Traces und
Publication. Restore validiert Definitionen, FKs, Head/Revision, Trace-Digests
und Importkennungen, bevor er publiziert. Fehlende Einträge ergeben den
deklarierten unzugeordneten Wert mit Hinweis, keine erfundene Klassifikation.
Keine Wiederholung vergangener Sichtungen/Outboxaktionen.

Rollback einer **Konfiguration** setzt Inhalte nicht auf einen historischen
Kataloghead zurück. Pro Definitionsversion wird der aktuelle kompatible Inhalt
verwendet. Inhaltsrücknahme geschieht als neue explizite Pflegerevision.
Desaster-Recovery restauriert dagegen einen zusammengehörigen Datenbankstand;
neuere Metadaten bleiben gemäß ADR 0020 erhalten, wenn es ein selektives
Wiederherstellen in einen bestehenden Bestand ist. Kein Zeilenmix ohne
ausdrückliche Konfliktprüfung. Konfigurationsexport sichert aktuelle
entscheidungsrelevante Kataloginhalte nach [0026](0026-import-export.md),
keine Messwert-/Sichtungshistorie.

## Einmaliger Import aus title_classifier

Quellformat v3 ist im gelesenen `io_v3.py` des bestehenden Projekts belegt:
`{version:3,entries:[…]}`, Identität scope/media_type/signal_type/normalized_key,
enum, parent_key, contexts mit source_app/enum_override, hidden_at/reviewed_at
und optionale Telemetrie. Kein Cover-/Artworkimport. Lokaler Code ist ein
Formatbeleg, kein Beweis des aktuellen produktiven Bestands.

In A2 entsteht ein reiner Parser/Planer, ausgeführt wird der Bestandimport
**erst in B nach zusätzlichem ausdrücklichem Go**. Quelle ist ein von Benni
bereitgestellter konsistenter v3-Export, alternativ separat freigegebener
read-only Snapshot. Kein Secret-/Optionsabruf, kein Schreibzugriff und keine
Schema-/Dateiänderung am title_classifier; kein dauerhafter Doppelschreiber.

Planphase schreibt noch keine fachlichen Inhalte. Sie bindet Quell-SHA256,
Quellprojekt/-Format, Zieldefinition/-Head und eine bestätigte
Enum-/Kontextabbildung. scope/context nicht still verwerfen:
abweichende Kontext-Overrides werden als eigene ausdrücklich bestätigte
Identitäts-/Definitionsabbildung geplant oder blockieren die betroffenen Zeilen.
Kein heimlicher Verlust von Klassifikation durch vereinfachtes Masterschema.
Unbekannte/anders normalisierte Schlüssel, nicht auflösbare Eltern,
mehrstufige Eltern, widersprüchliche Klassen, hidden/reviewed-Status und
Normalisierungskollisionen werden einzeln im privaten Bericht ausgewiesen.
Keine Fachklassen aus Zahlen raten; ohne bestätigte Abbildung kein Anwenden.

Wiederholkennung = Hash aus Quellprojekt, kanonischem Quellinhalt,
Importerformatversion und bestätigter Abbildung. Identisches erneutes Anwenden
ist No-op mit ursprünglichem Bericht, auch nach Restart/Restore.
Gleiches K mit gleichem Inhalt: Duplikat/skip; abweichender Inhalt: Konflikt,
Ziel bleibt erhalten bis ausdrücklicher Entscheidung. Alle Konflikte vor
Anwenden lösen; danach Einträge und Importmarker atomar zusammen speichern.
Importierte Zeiten/Zähler sind ausschließlich Herkunftsdaten, keine neuen
Wuschel-Sichtungen, aktuelle Messungen oder frischen Belege.

Präzisierung für Musik aus dem Review zu #75/PR #77: Aus dem flachen Schlüssel
werden der Titel allein und alle Zweiteilungen an ` - ` als kanonische
Laufzeittupel gebildet. Erste Zweiteilung (ohne Trenner: Titel allein) ist die
Hauptform; alle weiteren Formen erben direkt deren Klasse. Bei einer
Legacy-Variante erben sämtliche Formen direkt vom Legacy-Master. Zusatzformen
dürfen weder Hauptformen noch vorhandene Einträge verdrängen; Kollisionen
werden ausgelassen und gezählt. Konkurrierende Zusatzformen entfallen beide.
Hauptformkollisionen bleiben blockierend. Die 100.000er- und 50-MiB-Grenzen
gelten auch für die ausgeweitete Menge; die Eintragsgröße wird bereits während
des Aufbaus begrenzt. Siehe [Bereich 5 A2](../beta/bereich-5/A2.md).

Bericht: gelesen, gültig, neu, identisch, zusammenfallend, konfliktbehaftet,
abgelehnt; Master/Varianten, Abbildungen und Gründe je Datensatz. Summen
müssen alle Eingabezeilen erklären. Titel/Rohwerte bleiben im privaten
Benni-Bericht; öffentliche Nachweise nur Summen, Hashes und synthetische Fälle.

## LG-Source-Registry (M30-34)

Dasselbe Speicher-/OCC-/Audit-/A-06-Muster: Identität aus explizitem Geräte-Scope
und normalisierter Source, Eintrag zunächst `manual + active` nach M30-34,
Sichtung getrennt von fachlicher Zuordnung. „Neu gesehen“ ist keine aktive
Geräteumschaltung und kein Activity-State-Beweis. Schema, erlaubte Klassen
und Identitätsregeln sind eigene versionierte Definitionen; kein globaler
Titelkatalog für beides, keine LG-Fachregeln oder Live-Anlage in diesem Block.
Unknown-LG-Source und Zuordnung werden mit denselben gezielten Baseline-Tests
synthetisch geprüft, ohne die TV-Integration zu ändern.

Neue LG-Sichtungen materialisieren zunächst nur den in der Definition
festgelegten logischen Standard „manual + active“ in der Sichtungshaltung.
Das ist weder eine erlernte Zuordnung noch eine automatische Fachänderung.
Erst ausdrückliche Pflege legt eine abweichende append-only Inhaltsrevision an.
Die erstmalige Sichtung darf dadurch keinen zusätzlichen A-06-Reset erzeugen.

## A2-Pflichttests

Append-only/Tombstone/FKs, Definition immutable/Lebenszyklus, negative Treffer
gültig+G14, Sichtung ohne Inhalts-/Frischeänderung, Unicode-/Scopekollisionen,
Mastertiefe/Zyklen, direkte und geerbte D-Menge, unabhängiger Schlüssel ohne
Baseline-/Fristreset, kein künstlicher Edge/Command, atomare Publication,
Crash vor/nach Commit, 409 bei Pflege/Registry-Race, Idempotenzkonflikt,
Backup/Restore und neue DB-Migration. Import v3, Eltern/Kontext-Overrides,
Duplikat-/Herkunftsbericht, Wiederholung, Quellbestand unverändert.
Formularablauf ohne JSON, Tastatur/Leerzustand, budgetgerechtes Paging und
Messung vor Bestandimport. Produktprüfung erfolgt erst nach A2-Go.
