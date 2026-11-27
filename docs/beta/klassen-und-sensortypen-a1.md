# A1 – Benannte Katalogklassen und pflegbare Sensortypen

Design zur Prüfung für [#169](https://github.com/levtos/wuschel/issues/169).
Grundlage ist Wuschel **1.0.0b30**. Erst Claude-Review und Bennis Go geben die
Umsetzung frei. Beide Pakete bilden einen Auftrag und eine gemeinsame Beta.

## Ist und Soll

[ADR 0025](../adr/0025-managed-catalog.md) trennt Definition, versionierten
Inhalt und Sichtungen. Heute ist `classification_schema` eine einzige
Auswahlliste. Ihre Werte stehen für unterschiedliche Fachbegriffe. Der
TC-Abgleich übersetzt Spielwerte in den Soundmodus. Die Zuordnung einer
Spielzahl zum Licht-Look fehlt. Die Katalogauflösung verwendet derzeit
einen Enum-Wert je Eintrag und vererbt ihn vom Master an die Varianten.

[ADR 0030](../adr/0030-freshness-templates-evidence.md) löst eigene und
gemeinsame Aktualitätsregeln aus der Registry auf. Vier feste Sensortypen
begrenzen die Auswahl. Unbestätigte Quellen landen im Zuordnungsdialog
gemeinsam in einem Block. Paket 2 ergänzt pflegbare Typen und Vorschläge.
Eine Bestätigung ändert ausschließlich die Typzuordnung. Der Wechsel auf
eine gemeinsame Vorlage setzt eine nachgewiesen identische wirksame Regel voraus.

## Paket 1 – Katalogklassen

### Definition, Wahlwerte und Zuordnung

Vorgeschlagene Speicherung in der Registry. Eine Klassendefinition hat eine
stabile Kennung, einen deutschen Namen und eine Art. Bei Auswahlwerten
hat jeder technische Wert einen stabilen Schlüssel, Namen und Bedeutungssatz.
Zahlen sind ganze Punkte mit Einheit sowie Grenzen. Ja/Nein sind echte
Booleanwerte. Der Server validiert Typen, Werte, Zuordnungen und Standards.
Beispielsweise wird `true` bei einer Zahlenklasse abgewiesen. Eine Kennung
hat über alle Kataloge dieselbe Bedeutung. Umbenennen betrifft Anzeigenamen.
Für einen fachlich anderen Wert wird ein neuer Schlüssel angelegt.

| Schlüssel | Name | Art und Startwerte | Gemeinsamer Standard |
| --- | --- | --- | --- |
| `sound_mode` | Soundmodus | Auswahl `home_cinema` Heimkino, `grind` Grind, `headset` Headset | Je Katalog |
| `light_look` | Licht-Look | Auswahl `standard` Standard. Weitere Werte legt Benni an. | Standard |
| `volume_offset` | Lautstärke-Korrektur | Ganzzahl −30 bis +30 Punkte. Ein Punkt entspricht 0,01 HA-Lautstärke. | 0 |
| `damped` | Dämpfen | Ja/Nein | Nein |
| `content` | Inhalt | Auswahl `normal` Normal, `private` Privat | Je Katalog |
| `tv_device` | Gerät | Auswahl PS5, Apple TV, Sonstiges mit den vorhandenen technischen Schlüsseln | Sonstiges |
| `tv_activity` | Aktivität | Auswahl Fernsehen, Streaming, Spielen, Sonstiges mit den vorhandenen technischen Schlüsseln | Sonstiges |

Unter **Einstellungen → Katalogpflege → Klassen** werden Definitionen und
Wertelisten über die vorhandene Entwurfs- und Übernahmeführung gepflegt.
Die Katalogzuordnung legt Reihenfolge und optional abweichenden Standard fest.
Klassen mit „Je Katalog“ brauchen bei jeder Zuordnung einen Standard.
Die Zuordnung referenziert eine konkrete Katalogdefinition. Das System
leitet sie weder aus dem Anzeigenamen noch aus einem Titel ab.

| Katalog | Klassen in Anzeigereihenfolge |
| --- | --- |
| HomePods | Lautstärke-Korrektur, Dämpfen |
| PS5 | Soundmodus (Heimkino), Licht-Look, Lautstärke-Korrektur |
| PC | Soundmodus (Headset), Licht-Look, Lautstärke-Korrektur |
| Apple TV Apps und Apple TV Titel | Inhalt (Normal), Lautstärke-Korrektur |
| Stash | Inhalt (Privat), Lautstärke-Korrektur |
| TV-Gerät | Gerät |
| TV-Aktivität | Aktivität |

Klassen und ihre Wertelisten erhalten wie andere Registryobjekte eine
Vorschau. Beim Entfernen einer Zuordnung zeigt sie gespeicherte Werte,
erbende Varianten und abhängige Vertragsfelder. Im neuen Stand wird die
entfernte Dimension ausgeblendet. Ihre Daten bleiben in den früheren
Definitionen und Inhaltsrevisionen lesbar. Belegte Schlüssel oder Werte
werden über die bestehenden Versions- und Nutzungsprüfungen geschützt.
Änderungen an Bereich, Art oder erlaubten Werten brauchen einen gültigen
Folgestand für alle betroffenen Zuordnungen.

### Inhalte, Auflösung und Pflege

Ein selbständiger Eintrag oder Master speichert eine typisierte Zuordnung
`class_values` nach Klassenschlüssel. Fehlend bedeutet unzugeordnet. Die
Auflösung unterscheidet explizite Werte, Vererbung und Standard. Die
Standardreihenfolge lautet Katalogstandard, danach gemeinsamer Standard.
`false` und `0` sind ausdrücklich gesetzte Werte. Varianten nehmen pro
Dimension den wirksamen Wert und die Vollständigkeit ihres Masters.
Fehlende oder ungültige Eingänge behalten ihre Qualität und werden über
die vorhandene Statuslogik behandelt. Der Standard greift bei einem
verwendbaren Eingang mit fehlender Katalogzuordnung und zeigt den
vorhandenen Vollständigkeitshinweis pro fehlendem Feld.

Liste und Detail zeigen nur die zugeordneten Klassen mit ihren Namen.
Katalogübergreifend bleiben nicht zugeordnete Spalten als solche erkennbar.
Filter, Sortierung und Stapeländerung wählen einen Klassenschlüssel. Ein
Stapelvorgang verändert nur die gewählte Dimension. Master- oder
Rollentausch zeigt die Auswirkungen auf alle erbenden Varianten. Die
vorhandenen Konflikt- und Seitengrenzen gelten. Cursor bleiben an Auswahl,
Sortierung und Inhaltsstand gebunden. Im b30-Lesecache zählen
Definitionsstand, Inhaltskopf und die sichtungsabhängige Grundlage weiter mit.

[A-06](../adr/0025-managed-catalog.md#a-06-genaue-regel-nur-für-betroffene-schlüssel)
vergleicht für neue Lookups Schlüssel und Klassendimension. Relevant sind
wirksamer Wert, Vollständigkeit und Tombstone. Ein geänderter Kopf, ein
Anzeigename oder eine Sichtung löst für sich keinen fachlichen Neustart aus.
Änderungen am Master prüfen alte und neue Erben. Die Traces führen die
Dimension und bleiben auf dieselben fachlichen Verbraucher begrenzt.

### Umzug und Rücknahme

Vorschau aus den tatsächlichen Katalogkennungen, Definitionsversionen und
Inhaltsköpfen. Sie gruppiert je Katalog und Altwert die Zahl der Einträge,
den neuen Wert, erbende Varianten und Abweichungen. Sie trennt explizite
Werte, bereits fehlende Zuordnungen, Sichtungen und Tombstones. Die neuen
Katalogdefinitionen behalten Identitätsregel und Masterbeziehungen.
Legacy-Schlüssel wie `heimkino`, `stumm` und `privat` werden über die
bereits belegten Alias-Paare normalisiert.

| Katalog | Überführung vorhandener Werte |
| --- | --- |
| HomePods | normal → 0 Punkte und Nein. boost → +15 und Nein. mute/stumm → 0 und Ja. quieter/leiser → −10 und Nein. |
| PS5 und PC | Bisheriger Wert → Soundmodus. Licht-Look Standard. Korrektur 0. |
| Stash und Apple TV Apps/Titel | Bisheriger Wert → Inhalt. Korrektur 0. |
| TV-Gerät und TV-Aktivität | Bestehende Werteschlüssel bleiben erhalten und erhalten den jeweiligen Klassenschlüssel. |

Ungepflegte Einträge bleiben ungepflegt und verwenden den jeweiligen Standard.
Ein gespeicherter Altwert ohne bestätigte Zuordnung wird in der Vorschau
angezeigt und blockiert seine Migration. Die Fallback-Regel für unbekannte
TC-Zahlen gilt nur im Abgleich. Der Umzug übernimmt Klassendefinitionen,
Katalogversionen und Vertragsreferenzen mit einem bestätigten Writer-Commit.
Jeder migrierte Katalog erhält eine neue Inhaltsrevision. Die Vorschau
bindet Registryrevision, Entwurfsversion, betroffene Inhaltsköpfe und Plan-Digest.
Eine parallele Inhaltsänderung verlangt eine neue Vorschau. Die Request-ID
liefert ein idempotentes Ergebnis. Neustart und Programminstallation führen
keinen Umzug aus.

Die Historie verbindet alten und neuen Katalogstand. Rücknahme innerhalb der
neuen Definition erfolgt als neue Inhaltsrevision. Für die Rücknahme des
Umzugs zeigt die Historie den zugehörigen alten Registry- und Katalogstand.
Die Vorschau benennt seit dem Umzug hinzugekommene Pflege und Sichtungen.
Die ältere Definition bleibt lesbar. Über **Umzug zurücknehmen** wird der
gemeinsam geprüfte Registry- und Inhaltsstand in einem Writer-Commit als
neue Revision übernommen. Eine Installation des Releases allein ändert den
aktiven Stand nicht.

### Title Classifier, Verträge und Traber

Für Spiele bilden 0, 1, 2, 3 den Soundmodus Heimkino, Grind, Headset, Grind.
Dieselbe Zahl wird getrennt über die pflegbare Zuordnung dieses Katalogs
zum Licht-Look aufgelöst. Fehlt die Licht-Look-Zuordnung, gilt dessen Standard.
Musik liefert für 0 Korrektur 0, für 1 Korrektur +15 und für 2 Dämpfen Ja
mit Korrektur 0. 0 und 1 setzen Dämpfen Nein. Für bekannte Profile bleiben
nicht vom TC gefüllte Dimensionen erhalten. Unbekannte Zahlen verwenden
die Standards der TC-gesteuerten Dimensionen und erhalten einen Hinweis in
der Vorschau, gruppiert nach Katalog und Zahl. Neue Titel erhalten die
übrigen Standards über die reguläre Auflösung. Der TC hat bis zu seiner
Abschaltung für die von ihm gefüllten Felder Vorrang. Die vorhandene
Prüfung auf Konflikte zwischen Master und abweichender Variante bleibt
erhalten und vergleicht künftig die TC-gesteuerten Dimensionen einzeln.
Der Abgleich erfindet keine Szenennamen und schreibt jede bestätigte
Änderung mit ihrem vorhandenen Plan- und Revisionsschutz.

Ein typisierter Katalog-Lookup benennt die Katalogdefinition und
Klassendimension. Validierung prüft den Rückgabetyp gegen die Klassendefinition.
Die jeweils neue Vertragsvorlage publiziert ein separates Feld pro Dimension.
Apple TV summiert die tatsächlichen App- und Titel-Korrekturen in Wuschel.
Aus je −30 bis +30 folgt ein Summenbereich von −60 bis +60 Punkten. Die
Summe behält Qualität und Evidence beider Komponenten. Bei belegtem
`not_applicable` des Titels gilt die Korrektur der App. Bei unbekanntem
oder unbrauchbarem Titel ist die Summe unbekannt. Ein bekannter Titel ohne
Pflege erhält seinen deklarierten Standard.

Die [eingefrorene HA-Schnittstelle](../ha-interface.md) lässt in v1 neue Felder
und Verträge zu. Für die bestehenden Sensoren bleiben IDs, Primärzustand
und Attributnamen erhalten. Benannte Felder ergänzen die vorhandene
Projektion. Nur HomePods erhält einen parallelen Vertrag mit dem Primärfeld
`volume_offset`. Der bestehende HomePods-Vertrag leitet seinen Skalarwert
aus Korrektur und Dämpfen ab und bleibt während der Engine-Umstellung in
Betrieb. Bestehende IDs oder Felder abzuschalten
braucht eine eigene Freigabe und Übergangszeit. Die Umsetzung liefert ein
Inventar je betroffenem Vertrag mit alten und neuen Feldern, vorgeschlagenen
Entitäts-IDs und tatsächlicher `unique_id`. Keine Änderungen an den Engines
oder an der Apple-TV-Erfassung sind Teil dieses Auftrags.

## Paket 2 – Sensortypen und gemeinsame Aktualität

**Quellen → Aktualität → Sensortypen** pflegt eine Registryliste mit stabilem
Schlüssel und deutschem Namen. Die vorhandenen Schlüssel für Kontakt,
Bewegung, Temperatur und Zähler bleiben erhalten. Hinzu kommen Luftfeuchte,
Leistung, Helligkeit, Wind, Strahlung, Zustand/Text und Sonstiges. Neue Typen
und Anzeigenamen sind im Entwurf pflegbar. Ein genutzter Typ braucht vor
seiner Entfernung eine Vorschau und eine Ersatzzuordnung für Quellen und
Vorlagen. Die bisherige Sonderregel für manuelle Quellen bleibt erhalten.

Der Dialog **Vorhandene Quellen zuordnen** ordnet Quellen Vorschlagsgruppen
zu und nennt pro Zeile den Grund. Bestätigte Zuordnungen behalten Vorrang.
Die Geräteklasse aus Home Assistant hat Vorrang. Eine klare Einheit oder
Wertart kann einen Vorschlag begründen, etwa °C für Temperatur, W für
Leistung, lx für Helligkeit, m/s für Wind und W/m² für Strahlung. Prozent allein belegt keine Luftfeuchte. Ja/Nein allein
unterscheidet Kontakt und Bewegung nicht. Wenn eindeutige Metadaten fehlen,
erscheint die Quelle in der Gruppe „Unklar“. Einheiten, die nur einen
Vorschlag begründen, werden als solcher sichtbar gekennzeichnet. Titel,
Namen oder aktuelle Werte sind kein Beleg für eine Zuordnung. Der Hinweis
auf der Seite benutzt denselben Namen wie der Knopf.

Benni kann eine Gruppe bestätigen und jede Quelle einzeln umsetzen oder
ausnehmen. Die Vorschau zeigt aktuelle und vorgeschlagene Typen sowie die
wirksame Regel vor und nach der Änderung. Nur bestätigte Typen werden als
`sensor_kind` mit `sensor_kind_origin=confirmed` gespeichert. Eine
Typbestätigung lässt `freshness_selection` unverändert. Eine Quelle mit
bereits geteilter Regel kann nur zu einem anderen Typ wechseln, wenn eine
gleich wirksame gültige Zuordnung oder eine eigene Kopie dieser Regel
explizit mit bestätigt wird.

Mehrere bestätigte Quellen desselben Typs mit identischer eigener Regel
erhalten die Aktion **Gemeinsame Vorlage aus gleichen Regeln**. Der Server
vergleicht die kanonisch aufgelösten Regeln einschließlich Zeitgrenzen,
Toleranz und Liveness-Referenz. Er prüft danach erneut, dass jede
umgestellte Quelle exakt dieselbe wirksame Regel hat. Die Vorlage enthält
die regelbezogenen Eigenschaften. Eine quelleneigene Liveness-Referenz
bleibt in der Auswahl erhalten. Unterschiedliche Regeln verbleiben als
eigene Regeln. Die Vorschau nennt Vorlage, Quellen, Ausnahmen und
unveränderte Wirksamkeit. Abbruch, konkurrierende Entwurfsbearbeitung
und Übernahme nutzen die vorhandenen Schutzmechanismen.

[ADR 0030](../adr/0030-freshness-templates-evidence.md) bleibt maßgeblich für
die getrennten Vergleiche. Der Wechsel einer Referenz bei gleicher Regel
behält Bewertung, gespeicherte Evidence, Latches, Quellzeit und absolute
Fristen. Die Prüfung muss auch Legacy-Quellen abdecken, deren wirksame
Regel nach dem Entwurfsupgrade weiterhin identisch ist. Die bisher
eingefrorene Liveness- und Safety-Semantik bleibt wirksam.

## Formate und Umsetzungsschritte nach Go

Registry-Schema 5 führt die beiden
Stammlisten und die mehrdimensionalen Katalogdefinitionen ein. Es
behält Fingerprint-Version 4 und die bestehende Semantik für Quellen ohne
fachliche Änderung. Die Versionsprüfungen, das Capability-Schema und die
Entwurfs-Upgrades werden dafür gemeinsam angepasst. Paketformat 3
schließt die neuen Referenzen und das neue Eintragsformat ein. Vorhandene
Registry- und Paketformate bleiben für historische Revisionen lesbar und
behalten ihr bisheriges Serialisat. Der Referenzabschluss umfasst die
zugeordneten Klassendefinitionen, deren Werte, verwendete Sensortypen,
Aktualitätsvorlagen und Liveness-Referenzen. Grundlage für Export und
Import ist [ADR 0026](../adr/0026-import-export.md).

1. **Katalogmodell und Writer**. Typen, Standards, Vererbung, OCC,
   Änderungsmenge, Historie und Rücknahme mit bestehenden Store-Pfaden.
2. **Katalogpflege und Umzug**. Definitionen, Zuordnung, Stapelaktionen,
   überprüfbare Migration und portables Paket.
3. **TC-Abgleich und Projektionsvertrag**. Vorschau je Dimension,
   konfliktfreie Abbildung, neue Vertragsfelder und synthetischer
   Traber-Nachweis im HA-Kern.
4. **Sensortypen und Aktualität**. Typenkatalog, Vorschlagsgruppen,
   Zuordnung, Vorlage aus gleichen Regeln, Referenzabschluss und
   Nachweis der unveränderten wirksamen Regeln.

### Abnahmen für die gemeinsame Beta

| Bereich | Nachweis |
| --- | --- |
| Definition und Pflege | Neue Klasse, neuer Wert, Katalogzuordnung und abweichender Standard über die UI. Falscher Typ und ungültiger Standard scheitern. Entfernen mit Vorschau und erhaltener Historie. Liste, Detail, Filter, Sortierung und Stapelaktion je Dimension. |
| Auflösung | Master, Variante, teilweise Zuordnung, 0 und Nein, fehlende Zuordnung und unbekannter Eingang. A-06 erfasst nur betroffene Schlüssel und Dimensionen. |
| Migration | Synthetische Beispiele für jeden Katalog und Wert, inklusive HomePods-Boost, Mute, Quieter und PS5-Headset. Variante, Alias, Tombstone, Sichtungen, Konflikt, Wiederholung, Commitfehler und Rücknahme mit MemoryStore und PostgreSQL. Kein Update historischer Inhalte. |
| TC und Projektion | Spielzahl 2 mit voneinander abweichenden Licht-Looks pro Katalog, unbekannte Zahl 4, ungemappter Licht-Look, Musik-Mute, nicht vom TC verwaltete Dimension und Variantenkonflikt. Apple-TV-Summe mit beiden Eingangswerten, unbelegtem und nicht anwendbarem Titel. HA-Verträge mit vollständigen `fields` und bestehenden v1-Aufzeichnungen. |
| Quellen | Synthetischer gemischter Bestand mit 89 Quellen, inkl. Prozent ohne eindeutige Metadaten, Ja/Nein, Zähler und manueller Quelle. Neuer Typ, Gruppe bestätigen, Einzelausnahme und Template für identische Regeln. Effektive Regel, Evidence, Fristen und Zustände vor und nach übernehmen vergleichen. |
| Kompatibilität und Betrieb | Bestehende Pakete lesen, neue vollständig exportieren und einlesen, ungültige Querverweise im Teilimport melden. b30-Lesecache, bisherige Historien- und Schnittstellentests sowie vollständiges Release-Gate prüfen. |

Claude-Review und Benni-Go sind vor Umsetzung erforderlich. Danach folgen
A2-Nachweise, Claude-Review und Benni-Go vor der gemeinsamen Beta.
Installation, Auswahl des tatsächlichen Katalogbestands und Live-Abnahme
bleiben eigene Schritte. Veröffentlichung und Live-Freigabe werden im Issue
getrennt dokumentiert.
