# Wuschel – Wahrheit hat Vorfahrt

A1-Entwurf zu [#180](https://github.com/Levtos/wuschel/issues/180), Stand b32.
Die Entscheidung zur Reihenfolge steht in [ADR 0034](../adr/0034-wahrheit-hat-vorfahrt.md).
Das [Inventar](wahrheit-hat-vorfahrt-routen.md) benennt die heutigen Einstiegspunkte.
Die unten genannten Startwerte und Budgets sind Vorschläge für A2.

## Wege und Zuständigkeiten

1. **Hauptprozess.** Er hält die HA-/Traber- und MQTT-Verbindungen, nimmt
   Zeit- und Quellenereignisse an und besitzt den seriellen Wahrheits-Worker.
   Nur dieser Worker schreibt Wahrheitsdaten und veröffentlicht Revisionen.
   Gesundheitsabfragen, Berechtigungsprüfung und Transport bleiben schlank.
2. **Arbeitskoordinator.** Ein eigener Prozess nimmt Aufträge an, führt das
   dauerhafte Journal, startet Verarbeiter und verwaltet Fristen und Ergebnisse.
   Er betreibt getrennte, begrenzte Datenbankverbindungen für Lesesichten und
   Arbeitsdaten. Der Hauptprozess tauscht mit ihm kleine typisierte Nachrichten
   aus. Nutzlasten, Seitenausgaben und Artefakte werden gestreamt. Eine langsame
   Arbeitsverbindung darf weder die Wahrheitswarteschlange füllen noch die
   Hauptschleife mit blockierenden Pipe-, Datei- oder JSON-Operationen belegen.
3. **Verarbeiter.** Jeder Unterprozess bearbeitet genau einen Auftrag zugleich.
   Alle Aufträge benutzen die bestehende Fachlogik. Vorschau und Vergleich
   dürfen die aktive Wahrheit weder schreiben noch veröffentlichen.

## Verarbeiter-Pool im Supervisor

Der Hauptprozess startet einen eigenen Python-Arbeitskoordinator per
`exec`-Unterprozess mit neuem Interpreter. Dieser startet die Verarbeiter
auf dieselbe Weise und überwacht sie einzeln. Das hält auch Interpreterstart,
Imports und die Überwachung der Kinder von der Hauptschleife fern. Die
Prozesse verwenden eine begrenzte, typisierte IPC-Nachricht mit separatem
Datenstrom für große Inhalte. Die Schnittstelle lädt keine serialisierten
Python-Objekte. [Python-Dokumentation zu subprocess](https://docs.python.org/3.14/library/subprocess.html).

Der heutige Supervisor-Entrypoint behält UID 10001 und die Start- und
Gesundheitsgrenzen. Der Koordinator erhält nur die für Arbeitsdaten nötigen
Zugänge. Worker starten mit freigegebenen Umgebungsvariablen, ohne offene
Handles aus Hauptprozess oder Koordinator und ohne Schreibzugang zur
Wahrheitsdatenbank. Ein Supervisor-Stopp schließt die Annahme, lässt kurze
Commits auslaufen und beendet die Kindprozesse mit begrenzter Frist. Der
Koordinator überwacht die Elternverbindung. Beim Abbruch eines Workers
beendet und erntet er die gesamte zugehörige Prozessgruppe. Beim Neustart
versöhnt er Auftragsjournal und Commit-Belege, bevor Aufträge erneut anlaufen.
Fällt der Koordinator aus, startet ihn der Hauptprozess mit begrenzter
Rücksetzfrist neu, ohne seine Wahrheitswarteschlange zu unterbrechen.
Nur ein Koordinator je Installation darf Aufträge beanspruchen. Noch
unbestätigte Anfragen benutzen bei Wiederverbindung dieselbe Kennung.

| Ausgangslage | Bedarf | Automatische Obergrenze |
| --- | ---: | ---: |
| 4 verfügbare CPUs | 3 | 6 |
| 8 verfügbare CPUs | 7 | 14 |
| 1 verfügbare CPU | 0 | 1 für den Betrieb auf Einzel-CPU |
| Allgemein ab 2 CPUs | verfügbare CPUs minus 1 | doppelter Bedarf |

Beim Start wird die effektive CPU-Zahl aus Prozess-Affinität und, soweit
vorhanden, der engsten cgroup-Quote der Eltern ermittelt. Bruchteile werden
abgerundet, mindestens eine CPU wird angesetzt. Fehlen cgroup-Informationen,
gilt die sichtbare Affinität beziehungsweise CPU-Zahl. Bei 4 CPUs stehen
standardmäßig bis zu 6 Prozessplätze bereit, davon höchstens 3 gleichzeitig
für rechenintensive Abschnitte. Der Puffer hilft bei blockierendem I/O und
unterschiedlich langen Aufträgen. Er setzt keine 6 gleichzeitig verfügbaren
CPU-Kerne voraus. [Linux-Kernel-Dokumentation zu CPU-Quoten und cpusets](https://docs.kernel.org/admin-guide/cgroup-v2.html).

- Ein Verarbeiter läuft warm. Sobald er belegt ist, startet bei Bedarf ein
  weiterer und meldet sich erst nach einem Bereitschaftstest als verfügbar.
  Bei mindestens zwei erlaubten Prozessplätzen bleibt ein Platz für kurze
  interaktive Arbeit frei, solange Langläufer anstehen. Separate
  Auftragskanäle und begrenzte Datenbankverbindungen verhindern, dass ein
  blockierter Worker den Pool oder seine gemeinsame Ergebnisausgabe anhält.
  Innerhalb einer Klasse zählt die Annahmereihenfolge. Alterung verhindert,
  dass Hintergrundaufträge beim Wiederanlauf dauerhaft nachrücken müssen.
- Zusätzliche Leerlaufprozesse fahren nach zunächst 90 Sekunden herunter.
  Ein ausgefallener warmer Prozess wird ersetzt. Die Überwachung prüft
  Lebenszeichen, Fortschritt und eine harte Laufzeit je Auftragsart.
  Wartezeit zählt nicht zur Laufzeit. Auch ein Worker mit Lebenszeichen
  kann eine harte Frist überschreiten. Dann die betroffene DB-Abfrage
  abbrechen, den Prozess beenden, nötigenfalls hart abbrechen und den Platz
  zeitnah neu besetzen. Andere Aufträge laufen weiter. Das Journal zeigt
  beim betroffenen Auftrag einen verständlichen Fehler. Sicheres erneutes
  Einreihen mit demselben Auftragsschlüssel ist für eine vom Koordinator
  veranlasste Schutzpause oder beim Wiederanlauf nach Belegprüfung möglich.
- Bei mindestens zwei voll verfügbaren CPUs schränkt der Koordinator die
  Worker-Affinität auf die Arbeits-CPUs ein. Ohne nutzbare Affinität oder bei
  Quotierung unterhalb der CPU-Menge sorgen niedrigere Prozesspriorität und
  begrenzte Arbeitsfreigaben für die Reserve. Auf Einzel-CPU werden lange
  Arbeitsschritte in zeitbegrenzten Portionen zugelassen. In allen Fällen
  haben Koordinator und Abbruchüberwachung eigene kurze Ausführungsabschnitte.
  Der App-Container benötigt dazu keine privilegierte cgroup-Montage.
- Erstes **Planungsbudget**, im Dauertest zu vermessen. Je Verarbeiter etwa
  64–96 MiB im Leerlauf und 192–256 MiB bei Arbeit. Sechs aktive Verarbeiter
  ergeben bis zu 1,5 GiB bei 256 MiB je Prozess. Dazu kommen Koordinator,
  Hauptprozess, temporäre Seiten und Reserve. Bei 4 vCPU und 8 GiB für die
  HA-VM reserviert der Entwurf als Startpunkt 256 MiB für Koordination und
  Datenpuffer sowie mindestens 1 GiB Reserve über dem gemessenen
  Hauptprozessbedarf. Das App-Budget richtet sich am kleineren wirksamen
  Container-/VM-Limit aus und hält Platz für HA und PostgreSQL, soweit diese
  Ressourcen teilen. Die Schätzung wird mit PSS, Prozess-RSS und
  cgroup-Spitzenwerten bei großen/gleichzeitigen Dateien ersetzt.
- Der Koordinator reserviert Speicher pro Auftrag und pro zusätzlichem Kind.
  Ist die Reserve belegt, verbleiben bereits angenommene Aufträge im Journal.
  Große Pakete verarbeiten die Worker abschnittsweise mit begrenztem
  Arbeitsspeicher. Grenzwerte für temporäre Daten, Lesen und Übernehmen
  geben stets zuerst der Wahrheit Ressourcen frei. Ein unkontrolliert
  wachsender Worker wird vor Erreichen der Containergrenze beendet. Bei
  dauerhaft zu knappem Host muss die Abnahme die benötigte RAM-/CPU-Größe
  gegenüber der vorgesehenen Doppelbelastung beziffern.

**Einstellungen → Betrieb** verwendet die bestehende Oberfläche für
Betriebswerte. Der neue Wert heißt „Höchstens gleichzeitig verfügbare
Verarbeiter“. Erklärung „Wuschel richtet die Zahl der Verarbeiter nach den
verfügbaren Rechenkernen. Einer steht bereit. Weitere kommen hinzu, wenn
Aufträge warten. Du kannst die Obergrenze selbst festlegen.“
Die Auswahl „Automatisch“ zeigt Bedarf und errechnete Obergrenze,
beispielsweise „3 benötigt · bis zu 6 Verarbeiter“. Manuell sind mindestens
zwei Verarbeiter bei mindestens zwei CPUs einstellbar, damit ein einzelner
Langläufer Platz für andere lässt. Der Ein-CPU-Modus verwendet einen
Platz, sofern keine höhere Obergrenze mit den verfügbaren Ressourcen sicher
zulässig ist. Dort schützen Zeitportionen und der erzwungene Abbruch den
Fortschritt. Die Eingabeprüfung zeigt den sicheren einstellbaren Bereich,
den gespeicherten und den wirksamen Wert. Änderungen und Rücksetzen auf
„Automatisch“ folgen der bestehenden Neustartregel.

## Unveränderliche Sicht und atomare Übernahme

1. Beim Start bindet der Koordinator den Auftrag an die erwartete
   Registerrevision und je nach Route an Entwurf, Katalogköpfe, Zuordnung,
   redaktionellen Stand, Retentionsgrenze und Publikationsnummer. Eine eigene
   lesende PostgreSQL-Verbindung erzeugt in `REPEATABLE READ READ ONLY` einen
   zusammenhängenden Stand. Live-Daten, die nur im Hauptprozess vorliegen,
   kommen als kurze Metadaten oder Referenz auf einen versiegelten
   Publikationsstand hinzu. Große Referenzen werden bei normalem Commit
   mitgeführt. Die Auftragsannahme kopiert keine Runtime. Längere Aufträge
   übernehmen ihre unveränderliche Sicht in ein befristetes Arbeitsartefakt,
   damit sie keine offene MVCC-Sicht halten. Seiten bleiben an dieselbe
   Revisions-/Abfragekennung gebunden, soweit die betreffende Route das
   bereits verlangt. Der In-Memory-Testspeicher stellt dieselbe Sicht bereit.
   [PostgreSQL-Dokumentation zur Transaktionsisolation](https://www.postgresql.org/docs/18/transaction-iso.html).
2. Je Prozess gibt es höchstens einen laufenden Leseauftrag. Eigene
   Verbindungsgrenzen, `statement_timeout`, `lock_timeout` und
   `idle_in_transaction_session_timeout` halten Arbeitsabfragen von der
   reservierten Schreibverbindung und den Verbindungsplätzen für HA fern.
   Der Koordinator stellt nur parametrisierte, zugelassene Leseoperationen
   bereit. TC-Daten liest der bestehende Adapter mit Zeit- und Größengrenzen
   für den jeweiligen Plan. HA-Entitätensuche erhält ausschließlich die
   nötigen Metadaten. Anmeldedaten der Adapter verbleiben dort. Arbeitsartefakte und Journal
   liegen in der eigenen Installation, sind nur für berechtigte Auftraggeber
   abrufbar und werden nach befristeter Aufbewahrung bereinigt. Protokolle
   enthalten Kennungen, Typen und Zeiten, keine Nutzlasten.
3. Ein Auftrag erzeugt Antwortdaten oder einen vollständig vorbereiteten
   Plan mit betroffenen Schlüsseln, erwarteten Versionen, Prüfsumme und
   `request_id`. Import, Vergleich, Hashing und Validierung großer
   Datenmengen laufen im Verarbeiter. Der Koordinator prüft die Planform
   und versiegelt Datenabschnitte in einem begrenzten, getrennt berechtigten
   Arbeitsbereich. Das Arbeitsjournal speichert Besitzer, Phase, Schlüssel
   und Ergebnisverweis. Die Arbeitsdatenbankrolle kann in diesem Bereich
   schreiben. Sie besitzt keine Schreibrechte an den Wahrheitstabellen
   und kann keine aktive Revision wählen. A2 legt Rollenrechte, Schema-Upgrade
   und Wiederanlauf für bestehende Installationen im PostgreSQL-Test offen.
   Der Hauptprozess prüft die Rechte bei der Betriebsbereitschaft des
   Arbeitswegs und meldet eine fehlgeschlagene Einrichtung unter Betrieb. Die IPC-Nachricht zur Übernahme
   enthält nur Vorgang, Schlüssel, Versionsbezug, Prüfsumme und Referenz.
4. Der Wahrheitsschreiber überprüft `expected_active_revision` und die
   betroffenen Köpfe, Entwurfs-, Zuordnungs- und Redaktionsversionen,
   Sichtungsgrenzen, Planbindung, Auftragszustand und den vorhandenen
   Idempotenzbeleg. Ein unveränderlicher Plan ist an genau die geprüften
   Daten gebunden. Ein Konflikt hat dasselbe Ergebnis wie heute. Eine
   erneute Planung ist eine neue Vorschau, die die Oberfläche bei
   geänderter Grundlage ausdrücklich anzeigt.
5. Der Schreiber schaltet vorbereitete, bis dahin unsichtbare
   Planabschnitte über transaktionale Referenzen frei. Kleine Deltas
   schreibt er direkt in derselben Transaktion. Revision, wirksame
   Versionszeiger, Beleg, Audit und Publikationsstand werden gemeinsam
   festgeschrieben. Große Beiwerte, Indexarbeit und Kodierung müssen vorab
   vorbereitet sein. Der Übernahmeschritt darf weder alle Zeilen kopieren
   noch den Inhalt eines kompletten Pakets erneut validieren. Die
   vorhandenen Schreibhandler werden an dieser Grenze aufgeteilt.
   Übernahmen erhalten ein kurzes Lock-/Commit-Budget und werden einzeln
   zwischen Wahrheitsblöcken eingeordnet. Fällige Fristen und angenommene
   HA-Eingänge behalten ihre Reihenfolge und Vorfahrt.
6. Bei Aktivierung kompiliert der Arbeitsweg den neuen Auswertungsplan.
   Der Schreiber ermittelt für den neuen Plan den Ausgangsstand mit
   derselben Fachauswertung und sichtungsbezogenen Regeln. Er kann
   vorbereiten und Ereignisse in begrenzten Abschnitten nachführen, solange
   er weiter den alten Stand veröffentlicht. Ein vorbereiteter Stand ist
   an die letzte eingearbeitete Eingangsfolge gebunden. Der Umschaltpunkt
   prüft diese Folge, Zeitgrenzen und Revision erneut. Reicht das Budget
   bis zum nächsten sicheren Schnitt nicht, führt der Schreiber die
   Vorbereitung weiter oder plant neu. Er veröffentlicht erst den
   vollständigen Stand. So können weder stale Eingänge noch eine neue
   Revision mit alten abgeleiteten Werten wirksam werden.
7. Abbruch, Prozessabsturz oder Datenbankfehler vor dem Commit lassen den
   bisherigen Stand bestehen. Wurde der Commit bestätigt oder kann die
   Bestätigung verloren gegangen sein, entscheidet der atomar geschriebene
   Beleg. Eine Wiederholung mit gleicher Identität und Prüfsumme liest den
   vorhandenen Beleg. Arbeitsdaten werden erst nach Abgleich mit Journal
   und wirksamen Referenzen aufgeräumt. Ein kürzeres Commit-Budget, größere
   Mengen in der Vorbereitungsphase oder ein anderer physischer Zuschnitt
   dürfen die atomare Grenze nicht verschieben.

## Lastschutz und Oberfläche

**Nur diese Signale regeln die Aufnahme weiterer Arbeitsaufträge in den Pool.**

| Signal | Erfassung |
| --- | --- |
| Schleifenverzögerung des Hauptprozesses | Monotone Probe alle 20 ms gegen einen geplanten Termin. Aktueller Wert und Maximum bleiben getrennt. |
| Wahrheitswarteschlange | Anzahl, Anteil an ihrer Kapazität und Alter des ältesten Eintrags einschließlich eines aktuell laufenden Blocks. Auftrags- und HTTP-Wartezeit bleiben separate Diagnosewerte. |
| Ende-zu-Ende p95 | Vom angenommenen HA-Ereignis bis zum vom Traber bestätigten HA-Projektionsstand, der die fällige Änderung enthält. Gleiche monotone Uhr im Hauptprozess. Ohne fachliche Änderung wird nur die Verarbeitung gezählt. Ausstehende fällige Projektionen melden ihr Alter zusätzlich, damit ein stockender Kanal keine scheinbar guten p95-Werte liefert. |

Ausgangspunkt für A2 ist ein rollendes Fenster von 60 Sekunden, p95 erst ab
20 abgeschlossenen verknüpften Messungen. Beispielwerte für „Angespannt“
sind 100 ms Schleifenverzug, 25 % Belegung oder 250 ms Ende-zu-Ende p95,
für „Geschützt“ 250 ms, 50 % bzw. 500 ms. Das Alter offener Wahrheit wird
bereits vor Überschreiten von 1 s berücksichtigt. Wiederholt gemessener
Druck reduziert parallele Freigaben. Bei unmittelbar drohender Verspätung
stoppt der Koordinator neue Freigaben sofort, bricht notfalls laufende
Arbeitsabfragen ab und lässt Aufträge sicher fortsetzen oder neu starten.
Die Startwerte für Anlauf, Haltezeit (zunächst 5 s) und stufenweise Freigabe
werden mit dem reproduzierbaren Versuch kalibriert. Die Wahrheit läuft
weiter, auch während des Wiederanlaufs der Arbeitsaufträge. Fehlende
Messungen erscheinen als „Noch nicht gemessen“. Ein gültiger Leerlauf ohne
fällige Publikation erzeugt keine Verspätungsmeldung.

**Aufträge** werden nach Annahme dauerhaft angezeigt. Die Oberfläche
verwendet dafür die vorhandene App-Navigation und einen gemeinsamen
Auftragsbereich. Titel „Aufträge“. Einleitung „Hier kannst du deine
Aufträge verfolgen und abbrechen.“ Ein Einstieg aus der Statuszeile
öffnet denselben Bereich. **Einstellungen → Betrieb** verwendet weiter
seine vorhandene Hauptaktion und zeigt die Betriebsangaben in einem
eigenen Abschnitt.

| Anzeige | Verhalten |
| --- | --- |
| Wartet | Auftrag und Schlüssel sind dauerhaft angenommen. „Abbrechen“ beendet ihn. Hohe Last erzeugt weder Dialog noch erneuten Absendeversuch. |
| Läuft | Arbeit oder Übernahme läuft. „Abbrechen“ gewinnt bis zur bestätigten Übernahmegrenze. Hat die Übernahme begonnen, zeigt der Auftrag „Wird übernommen“ und erhält den tatsächlichen Commit-Ausgang. |
| Fertig | Ergebnis ist abrufbar, eine Änderung hat ihren dauerhaften Beleg. „Ergebnis öffnen“ führt zurück zur Vorschau, zum Vergleich oder zum Beleg. |
| Fehlgeschlagen | Verständlicher fachlicher oder technischer Grund am Auftrag, zum Beispiel „Seit der Vorschau wurde der Katalog geändert. Öffne eine neue Vorschau.“ Ein erneuter Versand nach verlorenem HTTP-Resultat prüft zuerst den vorhandenen Schlüssel. |
| Abgebrochen | Der Auftrag wurde vor der Übernahme beendet. Ein Abbruch während des Lesens stoppt die Abfrage und gibt den Arbeitsplatz frei. |

Die Oberfläche sendet große Aufträge direkt mit `Prefer: respond-async`.
Der vorhandene Endpunkt bestätigt erst nach dauerhafter Annahme mit
`202`, Auftragskennung und Ergebnisadresse. Der additive Auftragszugang
bietet Status, Ergebnis und Abbruch je Besitzer und Berechtigung. Bestehende
Aufrufer erhalten über dieselben Routen weiterhin Formate und fachliche
Fehlercodes. Eine synchrone Antwort wartet asynchron auf das Ergebnis aus
dem Arbeitsweg. Ein erneuter Aufruf mit derselben Anfragekennung findet
den angenommenen Auftrag. `operations/wait` bleibt als kurze
Verträglichkeitsroute erhalten. Die neue Oberfläche verwendet sie nicht.
Auftragszustand läuft über Seitenwechsel, HTTP-Abbruch und App-Neustart
weiter. Begrenzte Annahme- und Ergebnisstreams üben Rückstau aus. Echte
Speicher-/Datenbankausfälle werden als technische Fehler diagnostiziert.
Der Schutzstatus allein führt zu keiner Ablehnung.

Der Server veröffentlicht **eine** versionierte Betriebssicht für
Statuszeile, Übersicht und Auftragsdialoge. Sie enthält Instanz- und
Standkennung, den Zeitpunkt der Messung, Wahrheit (Zustand und Grund),
Arbeitsfreigabe (Zustand, Grund und verbleibende Haltezeit), Poolgröße und
die für den Betrachter sichtbaren laufenden/wartenden Aufträge.
`GET system/state` enthält sie additiv. Die Oberfläche verwendet den
gleichen Stand für alle Ansichten und übernimmt nur neuere Stände.
„Normal“ darf nur für einen tatsächlich wirksamen Normalzustand stehen.
Ein Kandidat für Normalbetrieb während der Haltezeit bleibt Diagnose.
Nach Verbindungsverlust lädt sie die vollständige Sicht und zeigt frühere
Daten als veraltet. Eine abgelaufene Haltezeit löst eine Aktualisierung
aus. Der Browser leitet daraus keinen eigenen Schutzstatus ab.

Drosselung, ihre Gründe und Haltezeit stehen unter **Betrieb**. Im normalen
Gebrauch ist nur der Auftragsfortschritt sichtbar. Die Statuszeile meldet
„Wahrheit wird verspätet aktualisiert“, wenn ein bereits fälliger
Wahrheitsstand den definierten Verspätungsgrenzwert tatsächlich
überschreitet. Als Startwert dient 1 s, der Test prüft die Abgrenzung zu
einer ruhigen Quelle und kalibriert den Wert. Transportausfälle behalten
die bestehende Verbindungsdiagnose. Rollen, Ingress-Schutz und die
deutsche Du-Form folgen [der Übergabe](../uebergabe.md#3-oberfläche-und-wörterbuch)
und [Control #17](https://github.com/Levtos/control/issues/17).

## Reproduzierbarer Messplan

| Ausgangsbeobachtung b32 aus #180 | Messung in A2 |
| --- | --- |
| `observation_block` 150–400 ms, unter Konkurrenz bis 1 s | Anzahl Eingänge pro Block und Wartezeit getrennt messen. Profile für Sicht-/Kopieraufwand, betroffene Vertragsknoten, Katalog-Hooks, DB-Schreiben, Benachrichtigung und Projektion. |
| `tick` bis 650 ms | Fristensuche, fällige Auswertung, DB-Probe und Aufbewahrung einzeln erfassen. |
| Klassen-Umzug-Vorschau 15–26 s, Speicher-p95 unter Konkurrenz bis 980 ms | Export, Abbildung/Blocker, Paketvalidierung und Prüfsummen, Zusammenführung/Diff, Ausgaben und Serialisierung getrennt erfassen. |
| `portable/stage` 17 s, beobachteter Schleifenverzug bis 13,3 s | Eingabe, erneute Planbildung, Kodierung, Staging, Übernahme und Beleg messen. |
| TC-Vorschau 1,4 s und Verlauf 1,4–2,8 s | TC-Abruf, Einlesen und Unterschiede bzw. SQL, Seitenkodierung und Lieferung messen. |

Die Zahlen stammen aus dem [Issue](https://github.com/Levtos/wuschel/issues/180).
Vorher und Nachher für den synthetischen Bestand werden erst in A2 ermittelt.

- Einen versionierten Generator und einen außerhalb der App laufenden
  Treiber mit fester Saat `180` anlegen. Der einfache Satz hat rund
  100 Verträge, 90 Quellen, 3.500 Katalogeinträge. Der doppelte Satz
  hat 200, 180 und 7.000. Anteile für abhängige Verträge, zeitgebundene
  Abläufe, Wertewechsel, Varianten, redaktionelle Zuordnungen,
  Katalogrevisionen und Historienzeilen bleiben gleich. Kalender enthalten
  je Quelle ein vollständiges synthetisches 31-Tage-Fenster mit
  Wiederholungen, Überschneidungen und Wechsel über Tagesgrenzen.
  Synthetische TC-Zeilen und Verlauf werden ebenfalls verdoppelt.
- Feste Zeitfolge mit HA-Ereignissen, MQTT, Fristen und Snapshot. Als
  Startprofil 5 bzw. 10 HA-Ereignisse/s, ergänzt durch 10-Sekunden-Bursts
  mit 25 bzw. 50/s alle fünf Minuten. Die Rate des doppelten Profils ist
  für jede Quellenart und jeden Burst genau doppelt. Die vorläufige
  einfache Rate wird vor dem Vergleich anhand gezählter, aggregierter
  b32-Befunde geprüft und als Parameter im Messprotokoll festgehalten.
  Akzeptierte Ereignisse und erwartete wirksame Änderungen werden separat
  gezählt. Eine feste künstliche TC-Antwortzeit und identische Netz- und
  DB-Bedingungen machen die Messung wiederholbar.
- Dieselbe Linux-Umgebung mit 4 vCPU, 8 GiB und gepinnter PostgreSQL-,
  Python- und HA-/Traber-Version verwenden. b32 und A2 aus den genauen
  Commits jeweils mit neuer Datenbank und identischen Fixtures starten.
  Je Variante nach 10 Minuten Aufwärmen drei identische kurze Profil-Läufe
  sowie mindestens 60 Minuten Doppel-Dauertest. Messungen für einfach
  und doppelt je ohne und mit Verwaltung ausweisen. Dokumentieren, wenn
  b32 Abfragen abweist oder nur teilweise ausführt. Diese Fälle zählen
  zur Rate und als Fehlschläge, nicht als kurze Erfolgszeiten.
- Während des Doppel-Dauertests gleichzeitig wiederholt Klassen-Umzug
  (Vorschau, Stage, Validierung, Übernahme), lesender TC-Abgleich samt
  apply, Verlaufsabfrage und portable Stage/Export ausführen. Auslösen,
  Wartezeit und Ausführung zeitlich überlappen lassen. Ein Konfliktlauf
  ändert den Stand gezielt zwischen Vorschau und Übernahme. Die
  Durchsatzmessung verwendet konfliktfreie Arbeitsdaten. Weitere Läufe
  erzwingen Verarbeiterabsturz, einen hängenden Auftrag, Abbruch und
  Koordinator-Neustart. Den normalen Betrieb mit höherem Bestand und die
  Fehlerphasen getrennt ausweisen.
- Den Monotonie-Zeitstrahl vom Treiber versandter Ereignisse bis zur
  beobachteten HA-Entität zusätzlich außerhalb der App messen. Intern
  Ingress, Queue, Auswertung, Commit und Traber-Bestätigung korrelieren.
  Die Zuordnung bleibt intern oder im Testtreiber. Der HA-v1-Payload
  braucht keine neue Kennung. Übereinstimmung mit der Ausgangslogik
  anhand Eingangsfolge, veröffentlichter Werte, Zeitgrenzen, Revisionen
  und Belege prüfen. Gültige Bündelung und Eingänge ohne Ausgabe werden
  bei der Erwartungsbildung berücksichtigt.
- Ausgeben je Variante Stichprobenzahl, p50, p95, p99, Maximum,
  Durchsatz, wartende/abgebrochene Aufträge, CPU-Sekunden je Prozess und
  für den Wahrheitsweg pro Wandsekunde, PSS/RSS, cgroup-Speicherspitze,
  Zahl/Alter DB-Abfragen, Commit-Zeiten und Wiederanläufe. Die
  vorhandenen Phasenmessungen ergänzen. Profiling-Läufe mit gesonderten
  Profildaten außerhalb der Abnahmeläufe halten den Vergleich fair.

Im Code sichtbare Kostentreiber sind bis zum Profil **Hypothesen**.
`State._fork`/Latches, wiederholte Vertrags- und Fristensuche,
Publikationsaufbereitung und weite Vergleiche beim Commit sind Kandidaten
für die Wahrheitszeit. Nach Profil nur betroffene Zeilen kopieren,
Abhängigkeits- und Fristenindizes je Revision wiederverwenden und
Publikations-/Archivänderungen aus den tatsächlich geänderten Zeilen
bilden. Dieselbe Frist und dasselbe Ergebnis bleiben prüfbar.
Beim Klassen-Umzug und `portable/stage` sind wiederholte Paketbildung,
Validierung, Sortierung und Hashing sowie vollständige Kopien die
Kandidaten. Die Umzugsvorschau baut aus derselben Sicht gezielt einen
Plan der betroffenen Definitionen, Zuordnungen und Ausgaben, ohne einen
vollständigen Export zu erzeugen. Einen benötigten Export für die bewusste
Übernahme erzeugt der Arbeitsweg erst dann. `portable/stage` nutzt die
bereits validierten unveränderlichen Eingaben und Planabschnitte erneut,
sofern Prüfsumme und erwarteter Stand passen. Bei SQL-Dominanz mit
synthetischem `EXPLAIN (ANALYZE, BUFFERS)` prüfen, welche Schlüssel und
Seiten den Aufwand verursachen. Die A2-Auswertung nennt die größten
beobachteten Kostentreiber mit Teilzeiten und Effekt je Korrektur.

## CI-Architekturprüfung

- Eine geschlossene Zuordnung aus Methode und normalisiertem Pfad sowie
  explizitem Operationsnamen für `truth`, `work` und begrenzte
  Kontrollpfade einführen. Sie muss aus dem tatsächlichen HTTP- und
  Laufzeit-Dispatch prüfbar sein. Test: alle Core-Routen und Erweiterungen
  (`slots.ROUTES`, `OPERATIONS`, `OPERATION_PREFIXES`, `SUBMIT`,
  `SUBMIT_PREFIXES`), Timer und Hintergrundpfade sind klassifiziert.
  Neue oder unbekannte Zweige und pauschale Wildcard-Freigaben schlagen
  fehl. Ersetzt ein Eintrag einen bisherigen Handler, prüft der Test den
  tatsächlichen Ein- und Aufrufpfad.
- Prozessgrenze als Test durchsetzen. Arbeitsrouten dürfen im Hauptprozess
  nur Transport, Berechtigung und Einreihung aufrufen. Änderungen an der
  Wahrheit müssen durch den seriellen Handler und dieselbe
  Konfliktprüfung. Architekturtests prüfen Abhängigkeiten und Zugriffsrechte.
  Ein absichtlich eingeschleuster Vollscan und ein direkter Schreibversuch
  aus einem Worker müssen den jeweiligen Test scheitern lassen.
- Linux-CI startet den echten Prozessbaum und PostgreSQL mit begrenzten
  Ressourcen. Ein kleiner deterministischer Belastungsfall nutzt große
  Nutzlasten, einen hängenden Worker und parallel alle Klassen von
  zugelassenen Wahrheits- und Kontrollrouten. Die monotone Schleifenprobe
  im Hauptprozess darf nie über 1 s Verzug melden. Ein absichtlich
  blockierender Testpfad muss vom Detektor erkannt werden. Separate
  Assertions prüfen, dass kein Arbeitsauftrag HA-Publikation pausiert
  oder eine gültige Aktion mit `load_protected` beantwortet.
  Der volle Doppel-Dauertest ist technischer Abnahmenachweis vor
  Veröffentlichung. Die Latenzziele werden als Vergleichswerte berichtet.

## Abnahme

**Harte Bedingungen im Doppel-Dauertest.** Schleifenverzug des
Hauptprozesses zu keinem Zeitpunkt > 1 s. HA-/Traber-Verbindung bleibt
bei Verwaltungsarbeit und Worker-Ausfällen bestehen. Keine verlorenen
Wahrheitsdaten, keine unerwartete Lücke und keine falsche Reihenfolge.
Kein halber Entwurf und kein halber fachlicher Stand nach Abbruch oder
Worker-Absturz. Ein hängender Auftrag hält andere Schritte und
Wahrheitsausgaben nicht auf. Keine gültige Aktion wird wegen Last
abgewiesen. Status, Grund, Haltezeit und Auftragsphasen stimmen mit
dem tatsächlichen serverseitigen Stand überein.

| Zusatztest | Erwartung |
| --- | --- |
| Start, Hoch-/Herunterfahren bei 4 CPUs, Leerlauf, gesetzte Obergrenze, kleine CPU-/RAM-Grenzen | Ein Prozess warm, Start auf Bedarf bis zur Obergrenze, Leerlauf räumt zusätzliche Kinder auf. Ressourcenreserve und Fortschritt bleiben erhalten. |
| Gleichzeitige Routen und fällige Frist | Katalog-Umzug, TC-Abgleich, Verlauf und Messung laufen parallel. Snapshot, Befehle und HA-Projektion liefern dieselben fachlichen Ergebnisse wie auf b32. |
| Worker hängt im CPU-Teil bzw. in SQL, Worker stirbt vor/nach Staging | Kurze Arbeit läuft parallel. Genau der betroffene Vorgang wird beendet oder sicher neu aufgenommen. Keine halben Geschäftsdaten, verwaisten Kinder oder offenen DB-Sichten. |
| Veraltete Registerrevision, Katalogkopf, Zuordnung, Entwurf oder Redaktionsversion | Bestehende Konfliktart, unveränderter Stand, neue Vorschau möglich. |
| Abbruch beim Warten, während Arbeit und beim Commit; Antwortverlust nach Commit | Eindeutiger Gewinner an der Übernahmegrenze. Wiederholungen liefern genau einen Beleg. |
| Koordinator-/App-Neustart mit wartenden und laufenden Aufträgen | Journal, sichtbarer Zustand und Belege werden versöhnt. Bestehendes HA-Wiederanlaufverhalten gilt. |
| Druck steigt und fällt; HA ist ruhig, später tatsächlich verspätet | Aufträge bleiben angenommen. Haltezeit und Freigabe überall konsistent. Nur tatsächlich verspätete Wahrheit erzeugt eine Meldung. |
| Vertrags-, Traber- und API-Kompatibilität, Rollen, Datenschutz | Vertrag und HA-v1-Basis sind unverändert. Nur additive Betriebsangaben. Fremde Aufträge und Artefakte sind geschützt. Unzulässige Eingaben liefern die bestehenden fachlichen Fehler. |

**Messziele** sind Ende-zu-Ende p95 < 250 ms im Dauertest,
`observation_block` p95 < 50 ms im Normalbetrieb, Wahrheitsweg bei
doppelter Last ≤ 0,5 CPU-Kern und Klassen-Umzug-Vorschau < 2 s.
Der Bericht nennt Vorher/Nachher für beide Größen. Verfehlte Ziele werden
mit Ursache und Verbesserung beziffert. Die harten Bedingungen gelten
unabhängig davon.

## Umsetzung nach Freigabe

1. Reproduzierbare b32-Messung und reproduzierbare Prüfung der semantischen
   Ausgaben erstellen. Rechte, CPU-Ermittlung und Speicherbudgets im
   Testcontainer prüfen.
2. Koordinator, Journal und Architekturprüfung einführen. Zuerst lesende
   Arbeit und große Transportdaten verlagern, anschließend Plan-/Commit-Grenze
   für Pflege, portable, TC und Registeraktivierung herstellen.
3. Wahrheitsschleife anhand der Messungen optimieren, das alte
   Last-Warten ablösen und die gemeinsame Betriebssicht samt
   Auftragsanzeige anbinden. Dauertest und Betriebshandbuch mit
   Konfiguration, Auftragsabbruch, Wiederanlauf und Diagnose vervollständigen.
4. CI, Messnachweise, Claude-Review im PR und Bennis Go für die nächste
   Wuschel-Beta einholen. Versionsänderung und kurze deutsche Einträge für
   Benni in `CHANGELOG.md` und `wuschel/CHANGELOG.md` liegen gemeinsam
   in der Versions-PR. Technische Veröffentlichung und Live-Abnahme
   sind getrennte Schritte.

Die Tests verwenden synthetische Daten. Für Versuche werden keine
Wuschel-App-Optionen abgefragt. Kennungen, Größen, Zustände und Zeiten
genügen für Diagnose und Nachweis. Die Legacy-Integration
`benni_core_contracts` bleibt außerhalb des Auftrags.
