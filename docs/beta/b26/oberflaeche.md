# b26 – Oberfläche, Schritt 5

Auftrag [#134](https://github.com/Levtos/wuschel/issues/134), Reihenfolge
#105 → #104 → #103 → #102. Ein Umsetzungsagent, vier Commit-Blöcke,
PR nach `b26`. Kein Release und keine Installation.

## Seitenrundgang und Quellen

Die bereits mit b23 umgesetzten Kalenderlücken, Primärfelder, HA-Formate,
Testtypen-Trennung und der stille Entwurfsabbruch bleiben erhalten.
Schnittstellenversion 1 und ihre Aufzeichnungen werden nicht geändert.

Die Oberfläche ergänzt deutsche Feldbezeichnungen, vorhandene Vertragsnamen
in der Fehlersuche, Kennzeichnung ungenutzter Vorlagenversionen, einheitliche
Datumsanzeigen in Europe/Berlin und verständliche Ereignis-/Abgleichszähler.
Leere Vertragskategorien erscheinen nicht im Menü. Veraltete offene Entwürfe
können nach Namensvorschau und Bestätigung aufgeräumt werden. Der geöffnete
Entwurf ist ausgeschlossen; jede Entfernung verwendet die angezeigte Version,
und ein Konflikt hält die Verarbeitung an.

Quellen erklären die Kette von HA-Entität über Verknüpfung, Quelle und Vertrag
zu Traber und Engine. Fehlende gemeinsame Aktualitätsvorlagen bedeuten nicht,
dass eigene Regeln fehlen. Bestätigte Typen und noch unbestätigte Typen werden
getrennt gezählt. Keine bestehenden Regeln oder Typzuordnungen werden geraten.
GET /catalogs liefert aktive Bestandszahlen gesammelt, entsprechend der
Katalogpflege einschließlich offener Sichtungen und ohne entfernte Aliasse.
Unbekannte Zahlen erscheinen nicht als Null.

## Objektaktionen

Objektzeilen verwenden ein gemeinsames Bearbeiten-Symbol mit Lucide Pencil,
Tooltip und deutschem aria-label. Umbenennen und Ein-/Ausschalten sind erst im
Bearbeitungsmodus sichtbar und verlangen eine App-Bestätigung.
Anzeigenamen ändern keine Kennung. Vertragsbestätigungen nennen transitive
Verbraucher; außerhalb der Konfiguration angeschlossene Engines bleiben
ausdrücklich unbekannt. Entfernen verwendet die bestehenden, versionierten
Operationen. Katalogänderungen brauchen eine passende Vorschau und anschließende
Bestätigung. Verlauf und Wiedergaben werden nicht durch direktes Schreiben
umgangen. Quellen/Verknüpfungen haben keinen erfundenen enabled-Schalter.

## Katalogpflege

Eine gemeinsame Tabelle zeigt Auswahl, Titel/Version, Rolle, Status, Ausgabe,
Klasse, Wiedergaben und letztes Datum; über alle Kataloge zusätzlich Katalog.
Varianten haben dieselben Spalten, Einrückung und Verbindungslinie.
Die vorhandenen Tokens bleiben maßgeblich. Varianten werden begrenzt
nachgeladen; die Familienansicht öffnet sie unmittelbar. Ein Master zeigt
die Summe und die jüngste Wiedergabe seiner Familie. Die Erklärung trennt
Zählwerte seit einem Reset von weiterhin sichtbaren historischen Datumswerten.

Alle Kataloge ist die Vorauswahl ohne Katalogparameter. Zusammenführung,
Filter, Sortierung und Seitenbildung laufen serverseitig. Zeilen führen ihre
Katalogkennung, Definitionsversion und Inhaltsrevision mit. Bearbeitung und
Rollentausch bleiben so am jeweiligen Katalog. Klassenfilter beziehen sich
auf die wirksame Klasse; Nicht zugeordnet bedeutet fehlende eigene/geerbte
Einstufung. Die Familienansicht enthält keine Einzelstücke.

Zum Master machen verschiebt die gesamte Familie in einem atomaren CareWrite:
die gewählte Variante wird Master, der alte Master und alle Geschwister werden
Varianten. Die bisher wirksame Klasse wird vorgeschlagen. Eine andere Klasse
muss in Vorschau und Bestätigung ausdrücklich geprüft werden.
Lösen bleibt über die vorhandene eigenständige Zuordnung möglich.

Neue, eindeutig erkennbare Musikversionen verwenden den Grundwerk-Schlüssel
ohne Versionszusatz und feat. Ein bisher ungespieltes Grundwerk erscheint als
Master ohne erfundene Wiedergabe. Ein später gespieltes Original verwendet
dieselbe Kennung. Bestehende ausdrücklich gepflegte Einträge haben Vorrang;
es gibt keine nachträgliche Sammelumsortierung. Unsichere Künstler-/Titelpaare
bleiben offen. Studio/Szene und Apple-TV-Hierarchien werden wiederverwendet;
Spiele ohne Versionszusatz bleiben eigenständige Grundwerke.

Eine erneut ausführbare, lesende Strukturprüfung meldet Versions-Master,
geteilte Familien, abweichende Grundwerke, fehlende Zuordnung, Duplikate und
abweichende Künstlerreihenfolge. Ergebnisse sind Hinweise, keine automatischen
Korrekturen. Der CSV-Export enthält die ganze Master-/Variantenstruktur mit
UTF-8-BOM, Semikolon, CRLF und Schutz vor Tabellenformeln in Titeltexten.
Ein CSV-Rückimport ist nicht entschieden und deshalb nicht implementiert.

## Pakete und Nachweise

catalog_families und catalog_views gehören zur Kartusche catalog.
Die Paketgrenzen in pyproject.toml und der Verzeichnispfad unter catalog in
tests/areas.json erfassen beide Module. Backendnachweise liegen in den bereits
zugeordneten test_catalog_care.py und test_workbench_api.py; Frontendnachweise
in src/b26 und den direkt betroffenen bestehenden Katalogtests.
Es gibt keine Importausnahme und keine Änderung bestehender Replay- oder
HA-Schnittstellenaufzeichnungen. Lokale Auswahl mit --changed meldet wegen
pyproject.toml ALL; lokal werden konservativ die betroffenen Katalog-, API-,
Hierarchie-, Wiedergabe- und Replaytests ausgeführt, die volle Suite in CI.

## Bewusst offene Konfiguration und Entscheidungen

Die im privaten Rundgang genannten Namen, fachlichen Kategorien, Räume/Zonen
und die Feiertags-Verknüpfung sind keine automatischen Codeänderungen.
Die automatische Befüllung und Zuordnung gemeinsamer Aktualitätsstandards
bleibt die in #104 ausdrücklich offene Produktentscheidung.

Der private Neustart der Titelkataloge ist in diesem Schritt nicht ausgeführt:
keine Installation, keine Übernahme und kein Löschen privater Daten.
Er erfolgt über eine neue Katalogversion/überprüfte Konfiguration im
Release-/Abnahmeschritt; der alte Stand muss archiviert und lesbar bleiben.
Die bestehende Versions- und Übernahmeprüfung bleibt dafür maßgeblich.
TV-Gerät und TV-Nutzungsart sind vom Neustart ausgenommen.
Die private Sicherung der Nicht-Standard-Zuordnungen wird weder neu gelesen
noch veröffentlicht. Die offene Farbauswahl wird nicht vorweggenommen.
