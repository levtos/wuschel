# Wuschel – abschließende Plattform-Architekturprüfung

Stand: 11.11.2026 · Geprüft: `Levtos/wuschel`, `main@0b1fc9d` (05.11.2026), Version 1.0.0b24
Auftrag: einmalige, entscheidungsorientierte Prüfung vor dem Go-Live. Es wurde nichts geändert, kein Issue, kein PR, kein Release.

> **Nachtrag 12.11.2026:** Beim Ausarbeiten der Aufträge haben sich fünf Punkte verfeinert. Maßgeblich dafür sind `docs/architecture/b25/grenzen.md` und `testbereiche.md`.
> 1. Statt eines Pakets `kernel` gibt es `kernel` und `rules`, weil `producer` und `registry` den Speicher importieren.
> 2. Kartuschen dürfen `management` importieren (Import/Export-Dienst).
> 3. Testdateien werden nicht verschoben, sondern in `tests/areas.json` Bereichen zugeordnet. 30 Tests bestimmen Pfade relativ zum eigenen Ort, und Tests importieren sich gegenseitig.
> 4. In b25 ziehen nur Kartuschen und neue Module in Ordner um. Die übrigen Kerndateien bleiben liegen; ihre Paketzugehörigkeit steht in der Grenzprüfung. Der Paketname `core_contracts` bleibt, weil im Repo schon ein Ordner `wuschel/` liegt, der mit `.` im Suchpfad kollidieren würde.
> 5. Kalender und Umweltformeln sind keine Kartuschen, sondern Kern (`rules`): Der Kalender ist eine Eingangsart wie die Uhr, Formeln sind mitgelieferte Bausteine, und beide wurden in der Beta kaum geändert. Kartuschen in b25 sind `catalog`, `tc_migration`, `wake` und `openings`.

Lesehilfe: Jede Aussage trägt eine Marke.
**[F]** = nachgewiesene Tatsache (selbst gemessen oder im Repo belegt) · **[H]** = Hypothese · **[S]** = Schätzung · **[E]** = Entscheidungsvorschlag an dich · **[R]** = offenes Risiko

---

## 0. Ergebnis in zehn Sätzen

1. **Wuschel bleibt eine App, ein Repo, ein Image, Python.** Der Umbau passiert innen. **[E]**
2. **Kartuschen werden interne Pakete mit festen Steckplätzen**, die im selben Prozess laufen und nie selbst veröffentlichen. Der Kern bleibt die einzige Stelle, die schreibt. **[E]**
3. **Eigene Container oder eigene Repos pro Kartusche lehne ich ab.** Sie würden die atomare Veröffentlichung aufbrechen und die Toolbox als verteiltes System nachbauen. **[E]**
4. **Zwei Dinge dürfen eigene Prozesse sein:** die Verwaltungsarbeit (Prüfen, Vorschau, Import) als Arbeitsprozess im selben Container, und später Zulieferer für Fremddienste, die nur Messwerte einliefern. **[E]**
5. **Der Opus-Befund „60 von 94 im Importkreis“ stimmt in der Zahl, aber nicht in der Schwere.** Auf Modulebene gibt es keinen einzigen Zyklus. Der Knoten hängt an 104 Importen innerhalb von Funktionen, und höchstens 21 davon müssen weg. **[F]**
6. **Die Folge des Knotens hat Opus richtig beschrieben:** Heute hängen 114 von 127 Testdateien an fast jeder Änderung. **[F]**
7. **Der teuerste Einzelposten ist `runtime.py`:** Sie war in 44 % aller Backend-Änderungen der Beta betroffen und enthält eine einzige Methode mit 1.031 Zeilen. **[F]**
8. **Opus' „Schritt 2“ (Wecken und Kataloge später in eigene Apps auslagern) widerspreche ich.** Beide berechnen Wahrheiten und gehören dauerhaft in die Schreibreihenfolge des Kerns. **[E]**
9. **Vor der ersten Engine muss nur die Außenseite feststehen** (HA-Schnittstelle, Vertrags-IDs, Ausfallverhalten, Neustart). Alles Innere lässt sich später billig ändern. **[E]**
10. **Ob der Umbau Codex-Tokens im erhofften Maß spart, ist nicht bewiesen**, weil niemand gemessen hat, wofür Codex sie verbraucht. Dafür nenne ich einen kleinen Test. **[R]**

---

## 1. Messwerte aus dem Repository

Alle Zahlen in diesem Abschnitt sind **[F]**, selbst gemessen am oben genannten Stand.

### 1.1 Größe

| Teil | Dateien | Zeilen |
|---|---|---|
| Backend `src/core_contracts` | 94 | 29.656 |
| Backend-Tests `tests/` | 127 Testdateien, 921 Testfunktionen (laut Release-CI b23: 1.746 Fälle) | 29.881 |
| Oberfläche `frontend/src` ohne Tests | – | 15.882 |
| Oberflächen-Tests | 89 Testdateien | 8.838 |
| Traber + Python-Client | 8 | 1.263 |

Aufteilung des Backends nach Aufgabe (meine Zuordnung, Dateilisten im Anhang):

| Aufgabe | Dateien | Zeilen |
|---|---|---|
| Wahrheitskern (Modell, Qualität, Regelsprache, Resolver, Abläufe, Speicher) | 34 | 8.188 |
| Verwaltung (Entwürfe, Prüfung, Import/Export, Einrichtung, Aufbewahrung) | 18 | 5.858 |
| Zentrale (`runtime.py` und drei Hilfsdateien) | 4 | 3.558 |
| Kataloge | 10 | 3.295 |
| Wecken | 5 | 2.108 |
| Titelabgleich mit dem alten Title Classifier | 5 | 1.696 |
| Anbindung HA/MQTT | 6 | 1.652 |
| API und Start | 2 | 1.586 |
| Kalender | 5 | 715 |
| Bauwerkzeuge für Vorlagen | 3 | 676 |
| Öffnungen, Umweltformeln | 2 | 324 |

Die drei Bauwerkzeug-Dateien (`apple_tv_package`, `stash_package`, `fact_package`) werden von keinem Modul der App importiert und gehören nach `dev/`.

### 1.2 Importgraph und der Befund „60 von 94“

| Zählweise | Kanten | Zyklen |
|---|---|---|
| Nur Importe am Dateianfang | 314 | **keine** – sauberer gerichteter Graph mit 18 Ebenen |
| Zusätzlich Importe innerhalb von Funktionen | 418 (104 neue) | ein Knoten aus **60** Modulen, dazu einer aus 2 Testhilfen |
| Zusätzlich `TYPE_CHECKING` | 423 | unverändert 60 |

- Die Zahl 60 von 94 ist also korrekt, **wenn man funktionsinterne Importe mitzählt**. Diese 104 Importe sind genau die Stellen, an denen ein Zyklus schon einmal aufgetreten und per „Import erst in der Funktion“ umgangen wurde.
- **Eine einzige Kante hält den großen Knoten zusammen:** `value_origin.py:107` holt sich `transform` aus `adapters/ha.py`. Ein Modul der untersten Ebene greift damit in die Anbindung. Ohne diese Kante zerfällt der Knoten von 60 auf 19, 9, 3 und 2 Module.
- **Höchstens 21 Kanten** müssen entfernt werden, damit der gesamte Graph zyklenfrei ist (Obergrenze aus 400 Suchläufen; die Liste steht im Anhang).
- `import-linter` 2.16 läuft unter Python 3.14 gegen diesen Code und meldet funktionsinterne Importe mit Zeilennummer. Das habe ich ausprobiert, nicht nur nachgelesen. Die Grenzen lassen sich also automatisch erzwingen.

### 1.3 Die Zentrale `runtime.py`

- 2.955 Zeilen, eine Klasse `Runtime` mit 51 Methoden.
- **`process` allein hat 1.031 Zeilen** und verzweigt über 49 verschiedene Vorgangsnamen: Messwert, Snapshot, Takt, Entwurf, Übernahme, Rollback, Katalogpflege, Titelabgleich, Import/Export, Aufbewahrung, Dienstaufträge und mehr.
- Sie importiert 47 andere Module (20 am Dateianfang, 27 in Funktionen). 6 Module importieren sie.
- `management.py` leitet mit `class Planner(Runtime)` von der Zentrale ab und läuft als Thread im selben Prozess (`asyncio.to_thread`).
- Der Kopfkommentar beschreibt das Prinzip richtig: eine serielle Instanz, die vorbereitet, auswertet, speichert und erst danach sichtbar macht. `_commit` schreibt zuerst in den Speicher und setzt erst dann den neuen Stand und die Veröffentlichung mit `publication_seq`, `prev_seq`, `epoch_id` und `registry_revision`.

**Bewertung:** Das Prinzip ist gut und muss bleiben. Schlecht ist nur, dass alle Vorgänge in einer Methode stehen und der Speicherpfad Katalog und Benachrichtigungen namentlich kennt (`_commit`, `_restore`).

### 1.4 Wie stark hängt Fachcode am Kern?

Importkanten vom Kern in den jeweiligen Fachteil. Genau diese Kanten müssen Steckplätze werden:

| Fachteil | Kanten Kern → Fachteil | aus wie vielen Kernmodulen | Querbezug zu anderen Fachteilen |
|---|---|---|---|
| Kataloge | 22 | 10 (`runtime` 6, `api` 4, `portable` 3, `authoring` und `adapters.ha` je 2, …) | 3 zum Titelabgleich (müssen weg) |
| Titelabgleich mit dem alten Title Classifier | 6 | 3 | 8 zu Katalogen (erlaubt) |
| Kalender | 8 | 4 | keiner |
| Wecken | 6 | 4 | keiner |
| Öffnungen | 1 | 1 | keiner |
| Umweltformeln | 1 | 1 | keiner |
| **Summe** | **44** | | |

Wecken, Kalender und Kataloge kennen sich untereinander nicht. Der Rechenkern `wake/wake_planning.py` (1.286 Zeilen) hat keinen einzigen internen Import.

### 1.5 Tests

- 70 von 127 Testdateien sind nach Release benannt (`test_alpha9_…`, `test_b22_p7`, …). 40 Dateien verwenden `Runtime`, 19 beziehen sich auf PostgreSQL.
- **Betroffene Testdateien bei Änderung eines Moduls** (Testdatei gilt als betroffen, wenn sie das Modul über Importe erreicht):

| Geändertes Modul | heute | wenn nur Importe am Dateianfang zählen |
|---|---|---|
| `catalog_care` | 114 | 11 |
| `title_sync` | 114 | 5 |
| `calendar_rules` | 114 | 1 |
| `wake/operator` | 114 | 2 |
| `portable` | 114 | 14 |
| `runtime` | 114 | 67 |
| `quality`, `persistence`, `registry` | 114 | 114 / 106 / 100 |

Die rechte Spalte ist eine **optimistische Untergrenze [H]**: Tests, die die vollständig zusammengesteckte App starten, laden weiterhin alles. Der wahre Wert nach dem Umbau liegt dazwischen.

### 1.6 Änderungen während der Beta (Git, ab 20.10.2026)

- 135 Commits, davon 61 mit Backend-Code.
- Pro Backend-Commit: im Mittel 4,2 Backend-Dateien (Median 2, 90 % ≤ 9, Maximum 19).
- **`runtime.py` in 27 von 61 Backend-Commits (44 %)**, `adapters/ha.py` 28 %, `api.py` 25 %, `persistence.py` 20 %.
- **42 von 61 Backend-Commits (69 %) ändern auch die Oberfläche.**
- 20 Commits betreffen Katalog-/Titelcode, **14 davon fassen zusätzlich Kerndateien an**.
- Geänderte Zeilen nach Bereich: Oberfläche 30.189, Tests 23.301, Backend 16.267, Doku 10.839, Traber 589.

### 1.7 Betrieb und Last (aus `docs/beta/b23`, Issues #109 und #111)

- Durchsatz ohne Verwaltungslast: 461 bis 704 Messwerte pro Sekunde bei 100 Verträgen und 90 Quellen. Der geprüfte Bedarf lag bei 20 bis 50 pro Sekunde.
- Mit gleichzeitiger Verwaltungsarbeit im selben Prozess: p95 erst 1,51 s, nach einer Korrektur 0,317 s; mit PostgreSQL in der CI zunächst 1,16 s.
- **#109:** Der Neustart dauert live länger als 60 Sekunden (lokal etwa 5). b24 hat nur den Timeout von 60 s auf 10 min gesetzt. Die Dauer ist nicht gemessen, die Ursache nicht behoben, es gibt keinen Test für den Startpfad.
- **#111** ist offen: Zentrale aufteilen, Startdauer, HA-Last durch die Projektion (101 Sensoren mit großen Attributen), Notbremse, sichtbare Grenzen für Schleifen, Technik-Einstellungen in der Oberfläche.
- Versionsnummer steht an 5 Stellen, eine davon fest in einem Test (`tests/test_image_distribution.py:45`).
- Wuschel verbindet sich mit HA über `ws://supervisor/core/websocket` und den Supervisor-Token. Supervisor-Bezüge stecken im Wesentlichen in `app.py`, `setup.py` und `api.py`.

---

## 2. Prüfung der bisherigen Opus-Aussagen

| Nr. | Aussage Opus | Urteil | Begründung |
|---|---|---|---|
| 1 | „60 von 94 Dateien hängen in einem Importkreis“ | **Zahl richtig, Schwere überzeichnet** | Siehe 1.2. Kein Zyklus auf Modulebene, eine Kante hält den Knoten, ≤ 21 Kanten lösen ihn ganz. |
| 2 | „Deshalb kann niemand sagen: nur X geändert, also nur X testen“ | **bestätigt** | 114 von 127 Testdateien, siehe 1.5. |
| 3 | „`runtime.py`: 2.955 Zeilen, eine Klasse, 51 Methoden, 47 Importe“ | **bestätigt, unvollständig** | Es fehlte der eigentliche Kern des Problems: eine 1.031-Zeilen-Methode und die Vererbung `Planner(Runtime)`. |
| 4 | „Rund 8.000 von 29.700 Zeilen sind Fachcode“ | **bestätigt** | 8.138 Zeilen. Opus' „Kern 21.700“ verdeckt aber 5.858 Zeilen Verwaltung und 676 Zeilen Bauwerkzeug. |
| 5 | „Wecken ist fast frei“ | **bestätigt** | 6 Kanten, Rechenkern ohne internen Import. |
| 6 | „Erst Steckplätze, dann Kartuschen herausziehen“ | **Schritt 1 richtig, Schritt 2 falsch** | Wecken und Kataloge berechnen Wahrheiten. Als eigene App wären sie externe Zulieferer mit eigenem Ausfallverhalten, eigener Version und eigenem Datenbestand. Dasselbe Ziel (nur das Geänderte testen) erreicht die Auswahl nach Ordnern im selben Repo. |
| 7 | „Unabhängig veröffentlichen geht nur als eigener Prozess“ | **richtig, aber kein Bedarf** | Du bist einziger Nutzer mit einem Release-Zug. Unabhängige Versionen erzeugen nur eine Verträglichkeitsmatrix. |
| 8 | „Verwaltung in einen eigenen Prozess (P8)“ | **bestätigt mit Auflage** | Die Messung aus b23 stützt es. Die neue Prozessgrenze ist aber eine neue Warteschlange; dort gilt eure Regel „Überlast nie mit Neuverbinden beantworten“. |
| 9 | „Aufteilen erst nach 1.0“ (später von Opus selbst korrigiert) | **Korrektur ist richtig** | Jetzt ist das Risiko am kleinsten, weil keine Engine angeschlossen ist. |
| 10 | „Sprache ist nicht das Problem“ | **bestätigt** | Siehe 5.1. |
| 11 | „import-linter erzwingt die Grenzen“ | **bestätigt durch Versuch** | Siehe 1.2. |
| 12 | ChatGPT: „Zerlegen spart kurzfristig nicht nachweisbar Tokens“ | **zur Hälfte richtig** | Nachgewiesen ist die Ursache-Wirkung beim Testumfang (1.5) und beim Kontext (1.6). Nicht nachgewiesen ist, wie viel vom Codex-Verbrauch darauf entfällt. Siehe R2. |

---

## 3. Zielarchitektur

### 3.1 Bevorzugt: „Ein Kern mit Steckplätzen“

Ein Repo, ein Image, eine HA-App. Innen acht Pakete mit erzwungener Abhängigkeitsrichtung. Kartuschen sind Pakete in diesem Repo, die sich an Steckplätzen anmelden.

```
                    ┌──────────────────────────── Container „Wuschel“ ───────────────────────────┐
                    │                                                                            │
 Home Assistant     │  Prozess 1: LIVE                               Prozess 2: VERWALTUNG       │
 ┌───────────┐  WS  │  ┌───────────┐   ┌──────────────────────┐      ┌──────────────────────┐    │
 │  Traber   │◄────►│  │ adapters  │──►│ lane (eine Schreib-  │◄─────│ management           │    │
 │ (Integr.) │      │  │ ha, mqtt, │   │ reihenfolge)         │ Plan │ Prüfen, Vorschau,    │    │
 └───────────┘      │  │ manuell   │   │ ingest → auswerten → │ +OCC │ Import/Export,       │    │
 MQTT-Broker ──────►│  └───────────┘   │ speichern → sichtbar │      │ Übernahme vorbereiten│    │
                    │        ▲         └───────┬──────────────┘      └─────────┬────────────┘    │
 Browser (Ingress)  │  ┌─────┴─────┐           │ ruft Steckplätze            │ liest Stand      │
 ──────────────────►│  │ api + UI  │   ┌───────▼──────────────┐              │ (nur lesend)     │
                    │  └───────────┘   │ engine (rein, ohne   │              │                  │
                    │                  │ Ein-/Ausgabe)        │              │                  │
                    │                  │ kernel (Modell, Regel│              │                  │
                    │                  │ sprache, Qualität)   │              │                  │
                    │                  └───────▲──────────────┘              │                  │
                    │   Kartuschen:  catalog · wake · calendar · openings ·   │                  │
                    │   environment · tc_migration  (melden sich an, werden   │                  │
                    │   vom Kern aufgerufen, schreiben nie selbst)            │                  │
                    └──────────────────────────┬──────────────────────────────┴──────────────────┘
                                               ▼
                                   PostgreSQL (LXC 108), ein Schreiber
```

| Paket | Aufgabe | Darf importieren |
|---|---|---|
| `kernel` | Modell, Qualität, Nachweise, Zeitlogik, Abläufe, Resolver, Zusammenführung, Regelsprache, Konfigurationsmodell, Uhr. Keine Ein-/Ausgabe. | nichts Internes |
| `store` | Zustand, Speicher (PostgreSQL und Speicher-Double), Historie, Migrationen | `kernel` |
| `engine` | Reine Auswertung eines vorbereiteten Stands (heute `_inputs`, `_evaluate`) | `kernel`, `store` |
| `lane` | Die serielle Instanz: Eingang, Blöcke, Speichern, Wiederanlauf, Fristen, Veröffentlichung, Vorgangsverteiler | bis `engine` |
| `management` | Entwürfe, Prüfung, Import/Export, Übernahme vorbereiten, Aufbewahrung, Einrichtung. Läuft im Verwaltungsprozess. | bis `engine`, **nicht** `lane` |
| `adapters` | HA-Bridge, MQTT, manuelle Quelle, HA-Projektion, Dienstkanal | bis `lane` |
| `api` | HTTP, WebSocket, Ingress, Sicherheit | alles außer `extensions` |
| `extensions/*` | Kartuschen | nur `kernel`, `store` und die Steckplatz-Schnittstelle |
| `app` | Zusammenstecken. Einzige Stelle, die alles kennt. | alles |

**Vier Regeln, die `import-linter` in der CI erzwingt:**
1. Schichten: `app` → `api` → (`adapters` | `management`) → `lane` → `engine` → `store` → `kernel`.
2. Kein Kernpaket importiert `extensions`.
3. Kartuschen sind voneinander unabhängig. Ausnahme, im Manifest erklärt: `tc_migration` darf `catalog` benutzen.
4. Kartuschen importieren weder `lane` noch `management`, `adapters` oder `api`.

### 3.2 Stärkste Alternative: „Kern plus Kartuschen als eigene Dienste“

Wuschel-Kern als Dienst in einem LXC mit Docker; jede Kartusche ein eigener Container, der über eine Zulieferer-Schnittstelle Ergebnisse einliefert.

**Wo sie stärker wäre:** harte Fehlerisolation (eine hängende Kartusche blockiert den Kern nicht), Updates einzelner Kartuschen ohne Neustart des Kerns, freie Sprachwahl je Kartusche.

**Warum sie für Wuschel verliert:**
- **Atomare Veröffentlichung.** Eine Katalogklasse wird bei jedem Titelwechsel in der Auswertung gelesen (`managed_catalog_lookup`), ein Weckplan ist selbst eine Wahrheit. Liegt das in einem anderen Prozess, gibt es entweder zwei Wahrheitsbestände oder ein verteiltes Protokoll. Beides widerspricht Abschnitt 5 des Auftrags.
- **Mehr Ausfallarten statt weniger.** Jede Prozessgrenze braucht Lebenszeichen, Aktualität, Rückstau-Regeln und Wiederanlauf. Genau dort lag der Ausfall vom 03.11.
- **Kosten.** N Images, N Pipelines, N Versionsstände und eine Verträglichkeitsmatrix. Das ist die Toolbox, nur umgekehrt.
- **HAOS.** Eine HA-App kann keine Geschwister-Container starten. Der Docker-Zugriff ist laut Entwicklerdoku nur lesend und nur für Apps ohne Schutzmodus. Die Idee bedeutet also entweder eine eigene HA-App je Kartusche oder den Auszug aus HAOS.

**Wann ich umschwenken würde:** wenn Kartuschen von verschiedenen Leuten gebaut würden, andere Sprachen bräuchten oder riskante Fremdbibliotheken mitbrächten. Nichts davon trifft zu.

### 3.3 Welche Unabhängigkeit brauchst du wirklich?

| Eigenschaft | Brauchst du sie? | Wie erfüllt |
|---|---|---|
| unabhängig **entwickelbar** | **Ja.** Das ist dein eigentliches Problem (Kontext und Testumfang je Änderung). | Pakete mit erzwungenen Grenzen, Tests und CI je Paket |
| unabhängig **installierbar** | Teilweise. | Code-Kartuschen sind im Image und werden in der Oberfläche ein- oder ausgeschaltet. Inhalts-Kartuschen (Vertragspakete) installierst du per Import. |
| unabhängig **aktualisierbar** | Nein für Code. Ja für Inhalte. | Ein Release-Zug. Vertragspakete, Vorlagen und Katalogdaten kommen ohne Release per Import. |
| unabhängig **laufend** | Nein für alles, was Wahrheiten berechnet. Ja für Verwaltungsarbeit und Fremdquellen. | Verwaltungsprozess im selben Container; Zulieferer liefern nur Messwerte. |

Dein WoW-Bild passt damit auf zwei Arten: **Die Bereiche** (Anwesenheit, Schlaf, Media, Klima …) sind Erweiterungen als Daten und funktionieren heute schon so (ADR 0026: ein Paket ist ein Datenformat und führt keinen Code aus). **Die Bausteine** (Katalogpflege, Weckplanung, Kalender) sind Erweiterungen als Code-Pakete im Image.

---

## 4. Trennlinie Kern / Erweiterungen

| Thema aus dem Auftrag | Einordnung | Begründung |
|---|---|---|
| **Verträge** (Vorlagen, Instanzen, Verknüpfungen) | Daten, keine Kartusche aus Code | Bestehendes Importpaket ist die Inhalts-Kartusche. |
| **Regelsprache** | **Kern** | Sie ist der Kern. Kartuschen melden zusätzliche Regelarten an. |
| **Resolver-Strategien**, Formeln, Zusammenführung | **Kern**, als mitgelieferte Bausteine über die vorhandenen Register (`register_formula`, `register_fusion`) | Ändern sich selten, werden von allen Verträgen genutzt. |
| **Katalogpflege einschließlich Titelklassifizierung** | **Kartusche `catalog`** | Größter und am häufigsten geänderter Fachteil (3.295 Zeilen; zusammen mit dem Titelabgleich in 20 von 61 Backend-Commits). Die Auswertung `managed_catalog_lookup` läuft im Kern-Durchlauf, die Pflege als Verwaltungsvorgänge. Die Katalogtabellen bleiben in der gemeinsamen Transaktion. |
| **Abgleich mit dem alten Title Classifier** | **Kartusche `tc_migration`, entfernbar** | Fünf `title_*`-Module (1.696 Zeilen) haben ein Ablaufdatum: Sie entfallen mit dem Abschalten des Title Classifiers, samt den Datenbankoptionen dafür. Die Katalogpflege ersetzt ihn fachlich; es bleibt kein zweites Modul zurück. |
| **Wake Planner** | **Kartusche `wake`** | Reiner Rechenkern, meldet die Regelart `wake_plan`, Einstellungen und die manuelle Ausnahme an. |
| **Kalender** | **Kartusche `calendar`** | Quellenart `ha_calendar`, Zwischenspeicher und die Regelarten `calendar_at`, `calendar_active`, Datumsregeln. |
| **Datenquellen** | HA, MQTT und manuell im **Kern** hinter einem Adapter-Steckplatz; Fremddienste als **externe Zulieferer** | HA und MQTT braucht jede Installation. Stash, Discord und Ähnliches liefern Messwerte über MQTT oder HA; ihr Ausfall ist durch die Aktualitätsregeln schon definiert. |
| **UI-Seiten** | Ordner je Kartusche in `frontend/src/features`, im selben Image | Navigation aus der Liste aktiver Kartuschen. Ein eigenes Repo bringt nichts. |
| **Verwaltung** | **Kern**, eigenes Paket, eigener Prozess | Ohne sie ist Wuschel nicht bedienbar; sie darf aber den Live-Durchlauf nicht ausbremsen. |
| Öffnungen, Umweltformeln | kleine Kartuschen `openings`, `environment` | Je eine Kante, gute erste Probe für die Steckplätze. |

**Was zwingend im Kern bleibt, ohne Ausnahme:** die Schreibreihenfolge, das Speichern, Veröffentlichung und Versionierung, die Qualitäts- und Statuslogik, Zeitregeln und Fristen, Wiederanlauf, die Regelsprache, die HA-Schnittstelle. Keine Kartusche bekommt Zugriff auf Speicher-Commit oder Veröffentlichung.

---

## 5. Sprache, Betrieb, Repository, Tests, CI

### 5.1 Sprache: Python bleibt **[E]**

- **Der Engpass war nie die Sprache.** Der Durchsatz liegt beim Zehn- bis Dreißigfachen des Bedarfs (1.7). Der gemessene Einbruch kam von Verwaltungsarbeit im selben Prozess, und das löst die Prozesstrennung. **[F]**
- **Das Wertvollste ist die Semantik in den Tests.** 1.746 Fälle halten Held, Unknown, Unresolved, Fristen, Kontinuität und Wiederanlauf fest. Ein Neubau in Go oder Rust müsste jeden davon neu beweisen. **[F]** für den Bestand, **[S]** für den Aufwand: ein Mehrfaches des Umbaus.
- **Traber bleibt in jedem Fall Python**, weil HA-Integrationen nur so gehen. Ein Wechsel hieße zwei Sprachen. **[F]**
- **Was für Rust spräche:** Status und Qualität als Typen, bei denen falsche Kombinationen gar nicht darstellbar sind. Für eine Wahrheitsinstanz ist das ein echtes Argument. Der Preis wären lange Korrekturschleifen mit dem Compiler bei jedem Agentenauftrag. **[H]**
- **Was für Go spräche:** echte Parallelität, ein einzelnes Binary. Beides brauchst du bei 90 Quellen nicht. **[H]**
- Freies Threading in Python 3.14 ist offiziell unterstützt, aber wahlfrei. Ich plane nicht damit, weil die Prozesstrennung ohne es auskommt und ich die Verträglichkeit von `asyncpg` und `pydantic` nicht geprüft habe.

### 5.2 Betriebsort: HA-App bleibt für den Go-Live, der Auszug wird vorbereitet **[E]**

Die Frage ist bewusst getrennt von 3 beantwortet. Die Zielarchitektur läuft an jedem dieser Orte.

| Betriebsform | Urteil | Kernargument |
|---|---|---|
| **HA-App** (heute) | **bleibt vorerst** | Gebaut, erprobt, Ingress-Anmeldung, Watchdog, Update per Knopf. Ein Umzug kostet Tokens und löst das Token-Problem nicht. |
| HA-Integration | **nein** | Liefe im Prozess von HA. Rechenlast und Abhängigkeiten träfen HA direkt. Widerspricht „Stabilität von HA geht vor“. |
| Nativer Dienst oder Docker in eigenem LXC | **beste Alternative, später** | Eigene Ressourcengrenze, eigener Neustart, Sicherung wie bei LXC 108. Es fehlen dann Ingress-Anmeldung und Supervisor-Token; nötig wären eine eigene Anmeldung und ein HA-Zugangstoken. |
| Docker-VM | nein | Kein Vorteil gegenüber LXC, mehr Verwaltung. |
| Kern plus Kartuschen-Container | nein | Siehe 3.2. |

**Vorbereitung im Umbau:** Die Supervisor-Bezüge wandern hinter ein kleines Betriebsprofil (`haos_app` oder `standalone`). Dann ist der Umzug später ein kleines Paket statt eines Umbaus. Der Zustand liegt ohnehin schon in PostgreSQL auf LXC 108; unter `/data` liegen nur Installationskennung und Tokens.

**Wann umziehen?** Wenn der Lasttest R3 zeigt, dass Wuschel unter Last HA spürbar bremst, oder wenn HAOS-Neustarts die Wahrheitsinstanz im Betrieb zu oft mitreißen.

### 5.3 Repository **[E]**

- **Ein Repo bleibt.** Mehrere Repos erzeugen für jede übergreifende Änderung zwei PRs, zwei Releases und eine Versionsabstimmung; der Agent braucht trotzdem beide Kontexte. Die Toolbox war teuer wegen der Kopplung, nicht wegen des gemeinsamen Ordners.
- Traber bleibt wie von dir entschieden bis 1.0 im Repo.
- Der Paketname `core_contracts` kann beim Verschieben mechanisch in `wuschel` umbenannt werden. Das ist jetzt billig und nach dem Engine-Anschluss lästig. Fachlich ändert es nichts.

### 5.4 Tests und CI **[E]**

1. **Tests umziehen, nicht neu schreiben:** `tests/<paket>/…` per `git mv`. Die Zuordnung der 70 release-benannten Dateien lässt sich aus dem Importgraphen automatisch erzeugen; die Tabelle liefere ich mit dem Bauauftrag.
2. **Vertragstests für die Steckplätze:** ein kleiner Satz, der bei jeder Kartuschen-Änderung mitläuft.
3. **Wiedergabetest als Wächter des Umbaus:** eine feste Folge von Eingängen wird abgespielt, der gesamte veröffentlichte Strom (Felder, Status, Qualität, Gründe, „seit wann“, Reihenfolge) wird als Datei festgehalten und nach jedem Paket verglichen. Einschließlich Neustart, Fristablauf und Datenbankausfall.
4. **CI nach Paket:** Änderung nur in `extensions/catalog` → dessen Tests, Steckplatz-Vertragstests, Lint, `import-linter`. Änderung im Kern → alles. Image-Bau und Ende-zu-Ende nur bei Kern, API, Oberfläche oder Release.
5. **Oberfläche gleich behandeln:** Testordner nach Funktion statt `alpha7`, `alpha8`, `b23`.
6. **Versionsnummer aus einer Quelle**, kein fester Image-Tag im Test.
7. **Backend-Tests parallel** (verkürzt nur die Wartezeit, spart keine Tokens).

---

## 6. Steckplätze, Manifest und Lebenszyklus einer Kartusche

### 6.1 Die Steckplätze

Abgeleitet aus den 44 vorhandenen Kanten, nichts auf Vorrat:

| Steckplatz | Wofür | Läuft wo |
|---|---|---|
| Regelarten | Modell, Typprüfung und Auswertung einer Regel (`wake_plan`, `managed_catalog_lookup`, `calendar_at` …) | im Durchlauf, rein, ohne Ein-/Ausgabe |
| Vertragstypen, Formeln, Zusammenführung | vorhandene Register | im Durchlauf |
| Konfigurationsabschnitte | eigener Teil des Konfigurationsdokuments samt Prüfung (z. B. Katalogdefinitionen) | Verwaltung |
| Tabellen | eigene Tabellen im gemeinsamen Zustand und in derselben Transaktion | Speicher |
| Vorgänge | benannte Verwaltungsvorgänge mit Klasse `read`, `plan` oder `write`; ersetzt die Verzweigung über 49 Vorgangsnamen in `process` | `plan` im Verwaltungsprozess, `write` als vorbereiteter Stand an den Kern |
| Lebenszyklus-Haken | `on_restore`, `before_commit`, `after_commit` (liefert Ereignisse), `next_due` (Wecker) | im Durchlauf |
| Quellenadapter | liefert Messwerte in den Eingang | Anbindung |
| Oberfläche | API-Routen, Seiten, Abschnitte in Import/Export, Einstellungen mit Standard, Minimum, Maximum | API / Oberfläche |

**Eiserne Regel:** Ein Haken bekommt den vorbereiteten Stand und gibt Änderungen zurück. Speichern und Veröffentlichen macht ausschließlich der Kern. Damit gelten Schreibreihenfolge, Atomarität, Versionierung und OCC automatisch auch für jede Kartusche.

### 6.2 Beispielmanifest

`src/wuschel/extensions/catalog/extension.toml`

```toml
[extension]
id              = "catalog"
name            = "Katalogpflege"          # Anzeigename, deutsch
version         = "1.0.0"
sdk             = 1                         # Version der Steckplätze, gegen die gebaut wurde
requires        = []                        # andere Kartuschen, z. B. tc_migration: ["catalog"]
default_enabled = true
removable       = false                     # true bei tc_migration

[state]
schema = 1                                  # eigene Schemaversion für Migrationen
tables = ["catalog_head", "catalog_content_revision", "catalog_entry_revision",
          "catalog_sighting", "catalog_request", "catalog_import"]

[provides]
rule_kinds        = ["managed_catalog_lookup"]
config_sections   = ["catalogs"]
portable_sections = ["catalog_contents"]
hooks             = ["on_restore", "before_commit", "after_commit", "next_due"]
api_prefix        = "/api/v1/catalog"

[[provides.operations]]
name = "catalog_care_preview"
class = "plan"                              # rechnet auf einem Schnappschuss, schreibt nichts
[[provides.operations]]
name = "catalog_care_write"
class = "write"                             # liefert einen vorbereiteten Stand, der Kern speichert

[[provides.ui_pages]]
route = "/katalogpflege"
title = "Katalogpflege"
icon  = "mdi:bookshelf"

[failure]
on_error  = "quarantine"                    # siehe 6.4
budget_ms = { rule = 5, hook = 50 }         # Richtwerte für die Diagnose, keine Sperre

[[settings]]
key = "sighting_retention_days"
label = "Sichtungen aufbewahren (Tage)"
default = 365
min = 30
max = 3650
restart_required = false
```

### 6.3 Lebenszyklus

| Schritt | Code-Kartusche (im Image) | Inhalts-Kartusche (Vertragspaket) |
|---|---|---|
| **Installation** | Kommt mit dem App-Release. Erscheint unter „Erweiterungen“ als vorhanden. | Datei einlesen unter „Import & Export“; landet als Entwurf. |
| **Aktivierung** | Schalter in der Oberfläche. Geprüft werden: Steckplatz-Version passt, benötigte Kartuschen aktiv, Migration der eigenen Tabellen. Die Aktivierung ist eine neue Konfigurationsversion: atomar, mit OCC, per Rollback umkehrbar. | Prüfen → Vorschau → Übernehmen. Wie heute. |
| **Update** | Mit dem App-Release. Hat sich `state.schema` erhöht, läuft die Migration im Wiederanlauf auf dem vorbereiteten Stand und wird mit ihm gespeichert. Schlägt sie fehl: Quarantäne, der Kern startet trotzdem. | Neues Paket einlesen; die Vorschau zeigt die Unterschiede. |
| **Fehler** | Quarantäne, siehe 6.4. Sichtbar unter Bereitschaft, mit Meldung. | Paket wird bei der Prüfung abgelehnt, nichts wird übernommen. |
| **Deaktivierung** | Schalter. Die Vorschau listet alle Verträge, die Regelarten dieser Kartusche nutzen. Sie werden nach Bestätigung zu `unresolved` mit Grund. Tabellen bleiben erhalten. | Verträge im Entwurf entfernen und übernehmen. |
| **Entfernung** | Nur für entfernbare Kartuschen: Release ohne das Paket. Die Tabellen löscht eine eigene Migration erst nach Bestätigung „Daten entfernen“ und vorherigem Export. | Entfällt (entfernt = deaktiviert). |

### 6.4 Was passiert bei Ausfall?

| Ausfall | Reaktion | Warum keine falsche Wahrheit entsteht |
|---|---|---|
| Regel einer Kartusche wirft einen Fehler | Betroffenes Feld wird `unknown` mit Grund `evaluation_error` | Nie `held`, nie letzter Wert. Der Grundcode existiert schon. |
| Haken einer Kartusche wirft einen Fehler | Kartusche in Quarantäne: ihre Haken werden übersprungen, ihre Regelarten liefern `unknown` mit `dependency_unavailable`. Der Block wird ohne sie neu ausgewertet und gespeichert. | Die übrigen Wahrheiten laufen weiter, die betroffenen sind ehrlich unbekannt. |
| Kartusche hängt (Endlosschleife) | Der Durchlauf steht; `/health/live` schlägt an, der Supervisor startet neu. War beim Absturz dieselbe Kartusche zweimal aktiv, startet der Kern mit ihr in Quarantäne. | Traber zeigt `stale`, nach 120 s `unavailable`. |
| Verwaltungsprozess stürzt ab | Der Vorgang endet mit Fehler, nichts wird übernommen. Neustart des Arbeitsprozesses mit begrenztem Abstand, sichtbare Meldung. | Verwaltung schreibt nie selbst. |
| Verwaltung überlastet | Begrenzte Warteschlange, Ablehnung mit Meldung. Kein Neuverbinden. | Der Live-Durchlauf ist ein anderer Prozess. |
| Datenbank fällt aus | Wie heute: Fortschreibung friert ein, keine Veröffentlichung, `publication_confirmed=false`, geschützte Ereignisse bleiben in Reihenfolge. Danach neue Epoche und Lücke. | Sichtbar wird nur, was gespeichert ist. |
| HA fällt aus | Wie ADR 0005: Aktualität läuft nach ihrer normalen Regel ab, keine `unknown`-Welle. | Sicherheitsrelevante Quellen haben Herzschlag oder Lebenszeichen. |
| Wuschel fällt ganz aus | Traber: bis 120 s letzter Zustand mit `status=stale`, danach `unavailable`. Nach dem Start: neue Epoche, Lücke, Zeitkontexte in Wiederherstellung, erst nach HA-Snapshot bereit. | Jede Engine muss `unavailable` und `unknown` als „keine frische Wahrheit“ behandeln und ihren sicheren Zustand wählen. |

---

## 7. Wirtschaftlichkeit und Codex-Budget

### 7.1 Was heute Tokens kostet

| Kostentreiber | Beleg | Marke |
|---|---|---|
| Kontext: `runtime.py` muss fast immer gelesen werden | 44 % der Backend-Commits; die Datei hat 133.543 Bytes, grob 33.000 Tokens je vollständigem Lesen | [F] für Anteil und Größe, [S] für Tokens (Bytes ÷ 4) |
| Die sechs Kerndateien, die Katalogänderungen mitziehen | 371.313 Bytes, grob 93.000 Tokens; 14 von 20 Katalog-Commits betroffen | [F] / [S] |
| Testumfang: fast jede Änderung kann fast alles treffen | 114 von 127 Testdateien | [F] |
| Backend und Oberfläche ändern sich gemeinsam | 69 % der Backend-Commits | [F] |
| Release-Aufwand | 9 CI-Jobs im vollen Lauf, Versionsnummer an 5 Stellen, eigene Prüfskripte für Store, Image und Versionssperre | [F] |
| Anteil dieser Posten am tatsächlichen Codex-Verbrauch | nicht gemessen | **[R]** |

### 7.2 Vergleich der Wege

| Weg | Einmalkosten | Laufende Kosten danach | Risiko für die Garantien | Urteil |
|---|---|---|---|---|
| **A. Schrittweiser Umbau in Python** (Empfehlung) | mittel bis hoch: grob 10 bis 13 Codex-Arbeitssitzungen **[S]** | Kartuschen-Änderung: eigenes Paket plus Steckplatzdatei statt Kerndateien; Tests des Pakets statt 114 Dateien | gering, wenn der Wiedergabetest vor dem ersten Schritt steht | **empfohlen** |
| **A-klein. Nur Zyklen lösen, Grenzen erzwingen, Tests zuordnen** (Pakete 0 und 1) | niedrig: grob 2 Sitzungen **[S]** | Testauswahl wird möglich; Kontextproblem `runtime.py` bleibt | sehr gering | **Einstieg von A, kein Ersatz** |
| B. Neubau in Python auf grüner Wiese | hoch | wie A | hoch: jede gelöste Randbedingung der Beta wird wieder offen | nein |
| C. Neubau in Go oder Rust | sehr hoch, plus zweite Sprache neben Traber | bei Rust vermutlich höher je Änderung **[H]** | hoch | nein |
| D. Hybrid: neuer Kern, alte Fachteile andocken | hoch | wie A | hoch, zwei Zustandsmodelle in der Übergangszeit | nein |
| E. Nichts umbauen, nur Regeln für Codex | null | wie heute | keines | reicht nicht: Der Testumfang ist eine Eigenschaft des Codes, keine Disziplinfrage |

Ob Weg A in ein einziges Wochenkontingent passt, kann ich nicht belegen. Die Pakete sind deshalb so geschnitten, dass nach jedem ein lauffähiger, gemergter Stand steht.

---

## 8. Migrationsstrategie aus der Beta

**Grundsätze:** Jedes Paket endet mit grüner voller CI auf einem lauffähigen Stand. Pakete 0 bis 3 ändern kein Verhalten; der Wiedergabetest beweist das. Verhalten ändert sich erst in den Paketen 4 und 5. Deshalb zwei Betas: **b25 = Struktur**, **b26 = Betriebshärtung**. Bei einer einzigen Beta ließe sich ein Fehler nicht mehr dem Umbau oder der Härtung zuordnen.

| Paket | Inhalt | Verhalten ändert sich? | Größe [S] | Abnahme |
|---|---|---|---|---|
| **0 Leitplanken** | Wiedergabetest gegen b24 aufzeichnen. `import-linter` in die CI, vorhandene Verstöße als Ausnahmeliste, die nur kürzer werden darf. Drei Bauwerkzeug-Dateien nach `dev/`. Versionsnummer aus einer Quelle. Tests per `git mv` nach Paket ordnen. | nein | klein | Wiedergabe identisch; CI grün |
| **1 Zyklen lösen** | Die ≤ 21 Kanten entfernen, zuerst `value_origin → adapters.ha` (`transform` gehört in den Kern). | nein | klein | Kein Zyklus mehr, auch mit Funktionsimporten; Wiedergabe identisch |
| **2 Zentrale zerlegen** | `runtime.py` in `lane` (Eingang, Speichern, Wiederanlauf, Fristen), `engine` (Auswertung) und einen Vorgangsverteiler mit Register. `Planner(Runtime)` wird durch die gemeinsame `engine` ersetzt. Reines Verschieben. | nein | **groß** | Keine Datei über etwa 600 Zeilen im Durchlauf; Wiedergabe identisch |
| **3 Steckplätze und Kartuschen** | Reihenfolge: `environment` und `openings` (je 1 Kante, Probe), `wake` (6), `calendar` (8), `tc_migration` (6), `catalog` (22). Alle Kartuschen sind fest eingeschaltet; Quarantäne bei Fehlern kommt dazu. | nein im Normalbetrieb, neu nur das Fehlerverhalten | **groß** | Die vier Linter-Regeln ohne Ausnahmeliste grün; Wiedergabe identisch; Quarantäne-Test je Kartusche |
| → **b25** | Veröffentlichen, installieren, Live-Abnahme durch dich | | | Kriterien G1–G5 |
| **4 Prozess und Wiederanlauf** | Verwaltung als eigener Prozess mit eigener Leseverbindung und OCC über die Generation. `startup_timing`, Ursache der Startdauer aus #109 beheben. Notbremse und sichtbare Grenzen für jede Wiederholung (#111 B5, B6). Projektion nur geänderter Felder (#111 A3). Betriebsprofil für den Supervisor. | **ja** | mittel bis groß | Kriterien G6–G10 |
| **5 Bedienung** | Seite „Erweiterungen“ mit Ein- und Ausschalten als Konfigurationsversion. Technik-Einstellungen und Seite „Systemzustand“ (#111 C7, C8). Oberflächentests nach Funktion ordnen. CI nach Paket. | ja, nur Oberfläche | mittel | Kriterien G11–G12 |
| → **b26**, danach Kandidat für 1.0 | | | | alle Kriterien |

**Rückweg jederzeit:** b24 bleibt als veröffentlichtes Image bestehen. Vor b25 und b26 je ein vzdump von LXC 108. Die Pakete 0 bis 3 ändern weder Datenbankschema noch Konfigurationsformat; ein Zurück auf b24 ist bis einschließlich b25 ohne Datenumbau möglich.

**Wie der Zwischenstand lauffähig bleibt:** Die Ausnahmeliste des Linters ist die Fortschrittsanzeige. Jedes Paket verkürzt sie; kein Paket darf sie verlängern.

---

## 9. Go-Live

### 9.1 Zwingend vor dem ersten Engine-Anschluss

Das sind die Dinge, die nach dem Anschluss teuer werden, weil Engines dagegen gebaut sind:

1. **HA-Schnittstelle einfrieren und testen** (`docs/ha-interface.md`): Entitäts-IDs, `unique_id`, Zustandsabbildung, Attributnamen, die 120-Sekunden-Regel. Ein fester Vergleichstest für die Projektion gehört in die Traber-CI.
2. **Vertrags-IDs und Feldnamen** der Verträge, die Engines lesen, sind ab Anschluss stabil. Umbenennen nur noch mit Übergangszeit.
3. **Ausfalltabelle je Engine:** Was tut die Engine bei `unknown`, `unavailable`, `stale`, `held`, `not_applicable`? Das ist Pflicht der Engine, muss aber vor dem Anschluss feststehen (ADR 0028, Punkt 4).
4. **Neustart und Wiederanlauf** sind gemessen, begrenzt und bei Fehler sichtbar (#109).
5. **Selbstschutz gegenüber HA** (#111 A3, B5, B6).
6. **Strukturumbau** (Pakete 0 bis 3), solange niemand angeschlossen ist.

### 9.2 Später ohne teure Migration änderbar

Betriebsort (HA-App oder LXC), Sprache einzelner Teile, Zahl und Zuschnitt der Kartuschen, Aufteilung in Repos, externe Zulieferer, freies Threading, Tabellenlayout im Inneren, Technik der Oberfläche. Keine dieser Entscheidungen berührt, was eine Engine sieht.

### 9.3 Verbindliche Abnahmekriterien

Nach deiner Regel sind **Sperren nur Korrektheit und Stabilität**. Messwerte sind Ziele und stehen als solche da.

**Sicherheit und Korrektheit (Sperre)**
- **G1** Wiedergabetest b24 gegen b25 identisch, einschließlich Neustart, Fristablauf, Datenbankausfall und HA-Ausfall.
- **G2** Die vier Linter-Regeln sind ohne Ausnahmeliste grün.
- **G3** Je Kartusche ein Quarantäne-Test: Fehler in Regel und Haken führen zu `unknown` mit Grund, nie zu `held` oder einem alten Wert; der Kern veröffentlicht weiter.
- **G4** Kein Weg an der Schreibreihenfolge vorbei: Ein Test belegt, dass Kartuschen und Verwaltung weder Speicher-Commit noch Veröffentlichung aufrufen können.
- **G5** Projektionstest für Traber gegen die eingefrorene Schnittstelle.
- **G6** Absturz des Verwaltungsprozesses mitten in einer Übernahme: nichts übernommen, Live-Durchlauf ohne Unterbrechung, Meldung sichtbar.
- **G7** Keine Wiederholung ohne Obergrenze und ohne sichtbare Meldung (Verbindung, Initialisierung, Arbeitsprozess).
- **G8** Wiederherstellung geübt: vzdump von LXC 108 zurückspielen, Wuschel starten, nach dem HA-Snapshot stimmen die Wahrheiten; alles Zeitabhängige ist ehrlich als wiederhergestellt markiert.

**Funktion (Sperre)**
- **G9** Live-Neustart endet in „Bereit“; die Dauer wird angezeigt. Schlägt er fehl, steht der Grund in der Oberfläche.
- **G10** Ereignissturm: geschützte Ereignisse vollständig und in Reihenfolge, Zustandsquellen zusammengefasst mit sichtbarer Lücke, eine Verbindung, kein Neuverbinden.

**Leistung (Ziele, keine Sperre)**
- Neustart live unter 30 s. Messwert bis Veröffentlichung p95 unter 1 s bei 20 pro Sekunde und laufender Verwaltung. HA-Automationen ohne spürbare Verzögerung während eines Sturms.

**Wartbarkeit (Sperre, weil Zweck des Umbaus)**
- **G11** Eine Änderung nur in einer Kartusche löst in der CI nur deren Tests, die Steckplatz-Vertragstests und die Prüfwerkzeuge aus.
- **G12** Jede technische Einstellung aus #111 C7 ist in der Oberfläche änderbar, mit Standard, Grenzen und Zurücksetzen.

---

## 10. Stärkste Gegenargumente zur Empfehlung

1. **Keine harte Isolation.** Eine hängende Kartusche im selben Prozess hält den Durchlauf an. Quarantäne greift erst nach einem Neustart. Wer das ausschließen will, braucht Prozesse, also die Alternative aus 3.2.
2. **Kein Einzel-Update.** Jede Korrektur an der Katalogpflege bleibt ein App-Release mit Neustart der Wahrheitsinstanz. Mit zwölf Engines ist jeder Neustart eine kurze Blindphase. Das ist nur vertretbar, wenn der Neustart schnell wird (G9, Ziel 30 s).
3. **Steckplätze können die falsche Abstraktion sein.** Acht Steckplätze entstehen aus genau einem Stand des Codes. Gegenmaßnahme: nur die 44 vorhandenen Kanten abbilden. Ein Restrisiko bleibt.
4. **Umbau an einem funktionierenden System.** Die 1.031-Zeilen-Methode enthält stillschweigende Reihenfolgen. Der Wiedergabetest deckt nur ab, was aufgezeichnet wurde.
5. **Die Ersparnis ist nicht bewiesen.** Liegt der Codex-Verbrauch überwiegend im Warten auf die CI, im Lesen von Protokollen und im Release-Ablauf, bringt die Struktur weniger als erhofft (R2).
6. **HA-App als Ort.** Die Instanz, die HA auslegt, teilt Ressourcen und Schicksal mit der HA-VM. Streng genommen gehört eine Wahrheitsinstanz auf eigene Füße.
7. **Python.** Laufzeitfehler bleiben möglich, die ein strenges Typsystem verhindern würde. Striktes mypy mildert das, ersetzt es nicht.
8. **Eine Schreibreihenfolge ist auch ein Nadelöhr.** Ein großer Schreibvorgang einer Kartusche blockiert kurz die Messwerte. Die Vorbereitung liegt im Verwaltungsprozess, das Speichern nicht.

---

## 11. Offene Risiken mit kleinstem Test

| Nr. | Risiko | Kleinster Test | Entscheidung, die daran hängt |
|---|---|---|---|
| **R1** | Die Live-Startdauer ist unbekannt (über 60 s), ebenso ihr größter Anteil. | `startup_timing` für Laden, Wiederanlauf und ersten Commit einbauen und einen Live-Neustart messen. | Umfang von Paket 4: reicht Beschleunigen, oder muss der Wiederanlauf in Stufen starten? |
| **R2** | Unbekannt, wofür Codex Tokens verbraucht. | Eine kleine, vergleichbare Katalogänderung vor Paket 0 und nach Paket 3 beauftragen und den Verbrauch vergleichen. | Ob Paket 5 (Oberfläche und CI umordnen) den Aufwand lohnt oder gekürzt wird. |
| **R3** | Unbekannt, ob Wuschel unter Last HA bremst. | Synthetischer Sturm auf der Live-Hardware; dabei Reaktionszeit einer einfachen HA-Automation messen. | Umzug in einen eigenen LXC nach 1.0: ja oder nein. |
| **R4** | Kosten der Zustandsübergabe an den Verwaltungsprozess bei Live-Größe unbekannt. | Schnappschuss in synthetischer Live-Größe (100 Verträge, 11.200 Katalogeinträge) einmal über die Prozessgrenze geben und messen. | Arbeitsprozess liest selbst aus der Datenbank (meine Annahme) oder bekommt den Stand übergeben. |
| **R5** | Der Wiedergabetest deckt Randfälle nicht ab. | Vor Paket 0 prüfen, ob die Aufzeichnung Neustart, Fristablauf, DB-Ausfall, HA-Ausfall, Übernahme und Rollback enthält. | Ob Paket 2 beginnen darf. |

---

## 12. Was du entscheiden musst

| Nr. | Entscheidung | Mein Vorschlag |
|---|---|---|
| **E1** | Zielbild: ein Kern mit Steckplätzen, Kartuschen als interne Pakete, keine eigenen Container oder Repos | ja |
| **E2** | Zuschnitt der Kartuschen: `catalog`, `tc_migration` (entfernbar), `wake`, `calendar`, `openings`, `environment` | ja |
| **E3** | Verwaltung als eigener Prozess im selben Container | ja |
| **E4** | Python bleibt; Sprachfrage geschlossen, solange das Leistungsziel nach Paket 4 erreicht wird | ja |
| **E5** | HA-App bleibt für den Go-Live; Betriebsprofil wird vorbereitet; Umzug nur bei Befund aus R3 | ja |
| **E6** | Zwei Betas: b25 Struktur ohne Verhaltensänderung, b26 Betriebshärtung | ja |
| **E7** | Paketname `core_contracts` beim Verschieben in `wuschel` umbenennen | ja, rein mechanisch in Paket 0 |
| **E8** | Abnahmekriterien G1–G12 als verbindlich | ja |

Nach deiner Freigabe mache ich aus den Paketen 0 bis 3 einen einzigen vollständigen Codex-Auftrag für b25, einschließlich der automatisch erzeugten Zuordnung von Testdateien zu Paketen und der Liste der 21 Kanten.

---

## Anhang A – Messmethode

- Importgraph: Python-AST über alle 94 Dateien unter `src/core_contracts`, relative und absolute Importe aufgelöst, getrennt nach Dateianfang, Funktionsrumpf und `TYPE_CHECKING`. Zyklen als starke Zusammenhangskomponenten (Tarjan).
- Mindestmenge zu entfernender Kanten: 400 zufällige Reihenfolgen, jeweils alle funktionsinternen Kanten entfernt und einzeln wieder zugelassen, solange kein Zyklus entsteht. Ergebnis ist eine Obergrenze.
- Testreichweite: direkte Importe jeder Testdatei, transitiv über den Importgraphen.
- Git: `git log --since=2026-10-20 --no-merges --numstat` auf `main`.
- `import-linter` 2.16 mit grimp 3.19 unter Python 3.14.6, Probekontrakte `forbidden` und `independence`.

## Anhang B – Die 21 Kanten (funktionsinterne Importe)

`value_origin → adapters.ha` · `manual_source → registry` · `manual_source → evidence` · `registry → validation` · `registry → declarative` · `refresh_plan → registry` · `instance_parameters → declarative` · `runtime → observation_block` · `runtime → management` · `authoring → portable` · `editorial → authoring` · `interface_upgrade → portable` · `interface_upgrade → title_sync` · `managed_catalog → catalog_playback` · `apple_tv_catalog → catalog_hierarchy` · `stash_catalog → catalog_hierarchy` · `title_sync → title_sync_runs` · `title_sync → catalog_scope` · `title_transfer → catalog_scope` · `title_transfer → title_sync` · `testing.contract_types → testing.contract_types.evaluate`

## Anhang C – Zuordnung der Dateien (Abschnitt 1.1)

- **Kataloge:** `catalog_care`, `managed_catalog`, `catalog_playback`, `catalog_scope`, `catalog_cleanup`, `catalog_models`, `catalog_hierarchy`, `apple_tv_catalog`, `stash_catalog`, `interface_upgrade` (stuft nur Katalogpakete hoch)
- **Titelabgleich:** `title_transfer`, `title_sync`, `title_sync_runs`, `title_import`, `title_reader`
- **Bauwerkzeuge:** `apple_tv_package`, `stash_package`, `fact_package`
- **Wecken:** `wake/*`
- **Kalender:** `calendar_coverage`, `calendar_rules`, `calendar_inputs`, `calendar_cache`, `dated_rules`
- **Verwaltung:** `authoring`, `portable`, `validation`, `validation_cache`, `management`, `editorial`, `capabilities`, `reconfiguration`, `revision_plan`, `setup`, `provision`, `retention`, `service_jobs`, `notifications`, `manual_restart`, `security`, `user_text`, `history`
- **Anbindung:** `adapters/*`, `ha_context`, `ha_projection`, `mqtt_restore`
- **Zentrale:** `runtime`, `observation_block`, `operation_timing`, `refresh_plan`
- **Wahrheitskern:** alle übrigen 34 Dateien, darunter `text_rules`

## Quellen

- Repository: <https://github.com/Levtos/wuschel> (Stand `0b1fc9d`), darin `docs/uebergabe.md`, `docs/ha-interface.md`, `docs/adr/0005`, `0026`, `0028`, `docs/beta/b23/P6.md`, `P7.md`, `blocked-acceptance.md`
- [Issue #109 – Start bricht live ab](https://github.com/Levtos/wuschel/issues/109)
- [Issue #111 – Go-Live-Härtung](https://github.com/Levtos/wuschel/issues/111)
- [Home Assistant Entwicklerdoku: App-Konfiguration (`docker_api`)](https://developers.home-assistant.io/docs/add-ons/configuration/)
- [Home Assistant 2026.2: Add-ons heißen Apps](https://www.home-assistant.io/blog/2026/02/04/release-20262/)
- [import-linter: Vertragsarten](https://import-linter.readthedocs.io/en/stable/contract_types/)
- [PEP 779: freies Threading offiziell unterstützt](https://discuss.python.org/t/pep-779-criteria-for-supported-status-for-free-threaded-python/84319/123)
