# Wuschel – Übergabe an die Beta

Fortgeschrieben am 15.11.2026 für b26 ([#135](https://github.com/Levtos/wuschel/issues/135)).
Letzter belegter Live-Stand: **b25**, seit 13.11.2026 laut
[Masterplan #115](https://github.com/Levtos/wuschel/issues/115).
**b26 ist der technische Stand auf dem Sammelzweig**, einschließlich der
Korrekturen aus #143 (PR #145). Review, Go, Veröffentlichung, Installation und
Live-Abnahme bleiben einzeln zu belegen; b26 ist hier nicht als installiert bestätigt.
Der Alpha-10-Betriebsstand in §5 ist historisch. Diese Datei fasst die aktuelle
Ausgangslage zusammen; neuere verbindliche Entscheidungen im Issue gelten vor
älteren Zwischenständen und Chats.

---

## 1. Projekt in einem Absatz

Wuschel (früher Core Contracts / Terra / Mimir) ist die Wahrheitsschicht für Bennis Smart Home
(„Einhornzentrale“, Home Assistant). Wuschel sammelt Messwerte aus Quellen, macht daraus geprüfte
Wahrheiten („Verträge“) und stellt sie anderen Modulen bereit. Wuschel handelt selbst nicht – handeln
tun die Policy-Module (künftig Schnüffel, Kuschel, Funkel, Blinzel, Schlummer, Träller, Pförtchen).
Traber ist die HA-Integration, die Wuschel mit Home Assistant verbindet.

Registry-Prinzip (wie ein Symlink): Policies lesen eine feste Vertrags-ID. Welche HA-Entität dahinter
steckt, steht nur in der Verknüpfung und lässt sich austauschen, ohne Policy-Code zu ändern.

## 2. Rollen und Ablauf

- **Benni:** Eigentümer, entscheidet, gibt „Go“. Live / Live Verified ist ausschließlich Bennis Gate.
- **Codex (`agent:codex`):** baut, prüft und führt den autorisierten Release-/Installationsauftrag aus.
- **Analysemodell:** prüft den Sammel-PR und gibt die Release-Empfehlung.
  Für b26 und #136 übernimmt Claude (`levtos-claude`) diese Prüfung selbst
  (Entscheidung Benni vom 14.11.2026, #115). Claude installiert nichts.
- **Ablauf:** nach den [Arbeitsregeln für Agenten](https://github.com/Levtos/control/blob/main/docs/workflow/working-rules.md) (Entscheidung Benni, 24.10.2026). Aufträge kommen als Issue; Codex berichtet als Issue-Kommentar, Claude reviewt im PR (als `levtos-claude`), Benni gibt „Go“ im Issue.
  - **Klein** (Fix, Abhängigkeiten, Tests, CI, Doku): bauen → CI grün → mergen → Release/Installation in einem Durchgang.
  - **Groß** (neue Funktion, DB-Migration, Wahrheitslogik): A1 Design → Review → Go → A2 Bauen → Review → Go → Release/Installation.
  - Bei Rot korrigiert Codex selbst (höchstens 3 Versuche, jeder mit neuer Korrektur), danach STOPP mit Analyse.
  - Lokal nur gezielte Tests; die volle Suite läuft in der CI. Berichte höchstens eine Bildschirmseite.
  - Für b26 gilt #115: je Schritt ein PR nach `b26`, eine Durchsicht am Sammel-PR
    nach `main`, danach Bennis Go in #135. Frühere technische Nachweise und die
    grüne CI einzelner Schritte ersetzen diese Freigabe nicht.

### Arbeitsregeln für Claude (von Benni ausdrücklich eingefordert)

1. Erst die Datenlage vollständig prüfen (selbst prüfen oder Benni fertige Terminal-Befehle geben),
   dann Codex **einen** konsolidierten Prompt geben – keine Freigabe plus Nachträge.
2. Prompts für Codex / Claude Code immer fertig mitliefern, ohne Nachfrage.
3. Keine Fragen stellen, deren Antwort Claude kennt oder im Code/in der Akte nachlesen kann.
4. Benni nie Fehler unterstellen, ohne Beleg. Rückschlüsse als Vermutung kennzeichnen oder erst prüfen.
5. Erst die eigentliche Frage beantworten, Widersprüche ausdrücklich auflösen, klare Logik, notfalls leichte Sprache.
6. Tempo: keine Umwege, keine Diskussionen, die Benni nicht angestoßen hat.
7. Browser-Tests in Chrome (Claude in Chrome).

## 3. Oberfläche und Wörterbuch

- Leitbild: Ein Teenager ohne App-Erfahrung kann alles bedienen. Durchgehend Deutsch, Du-Form,
  kein Denglisch, alles ohne JSON und ohne KI bedienbar.
- Seitenregel: Titel, ein Satz „Hier kannst du …“, genau ein Hauptknopf oben rechts.
- Wörterbuch: Vertrag, Vorlage, Entwurf, Übernehmen, Version (= Vorlagenversion), Konfiguration
  (= übernommener Gesamtstand), Programmversion (App), Quelle, Verknüpfung, Aktualität, Nachweis,
  Bereitschaft, Zusammenführung, Berechnung, Ablauf, HA-Anbindung, Durchlauf.
  „Details“ statt „Was kann ich tun?“, Bearbeiten direkt am Objekt (b22/P3, #97), „Fehlersuche“, „Technische Details“
  für Prüfsummen; „Erweitert“ nur dort, wo man eine eigene Kennung eingeben kann.

## 4. Namen, Farben, Bilder (Einhorn-Herde)

| Einhorn | Modul | Farbe | Icon |
|---|---|---|---|
| Wuschel | Wahrheitsschicht (diese App) | Regenbogen | Vertragsrolle mit Regenbogensiegel |
| Traber | HA-Integration | Silber #A7B1B8 | gestapelte Briefe |
| Schnüffel | Anwesenheit | Orange #E8893A | Lupe mit Haus |
| Kuschel | Klima | Himbeerrot #B8396B | Thermostat-Drehknopf |
| Funkel | Licht | Zitronengelb #D9CB2E | Glühbirne |
| Blinzel | Rollos | Moosgrün #74873A | Sonnenbrille |
| Schlummer | Schlaf/Wecken | Blau #4A78C2 | Wecker |
| Träller | Media | Violett #8E6CC8 | Popcorn-Eimer |
| Pförtchen | Tür | Braun #9A6B45 | Schlüssel |

Mähnenfarben sind Erkennungsfarben, getrennt von den Statusfarben Grün/Gelb/Rot. Originale und
Steckbrief: `Levtos/control` → `assets/herde/` (Issue #60). HA-Seitenleiste kann nur MDI:
`mdi:script-text-outline`.

## 5. Historischer Live-Stand nach Alpha 10 (geprüft 20.10.2026)

Die Tabelle beschreibt ausschließlich den damaligen Stand, nicht die heutige Installation.
Die Bestandsaufnahme vom 30.10. in #80 belegt 85 implementierte Verträge und b19;
#89 nennt b19 mit aktiver Konfiguration 10. Diese Angaben ersetzen keine aktuelle
Gesundheitsprüfung. Umsetzungsnachweise: [Beta-Abschluss](beta/abschluss/A2.md),
[Titelkatalog b17–b19](beta/titelkatalog/A2.md) und [b20](beta/b20/A2.md).

| Bereich | Stand |
|---|---|
| App | Wuschel **1.0.0a10**, Repo `Levtos/wuschel`, Slug `wuschel` (HA: `f18065c9_wuschel`), Images `ghcr.io/levtos/{amd64,aarch64}-wuschel`, Tag `v1.0.0a10` (Pre-Release) |
| Status | „Bereit“, Konfiguration 1 aktiv (enthält nur die Aufbewahrung), 0 Verträge |
| Installationskennung | `1c30cb9f-eec4-41d1-932a-21368e2dcdf1` (neu, ≠ Mimir/Terra) |
| HA-Integration | Traber geladen (HACS, Repo `levtos/wuschel`), Protokoll 2. Bifröst entfernt. |
| Datenbank | PostgreSQL 18.6 auf LXC 108, DB `wuschel`, Rolle `wuschel_app` ohne Sonderrechte, TLS `verify-full` auf `postgresql.local`, pg_hba nur 192.168.178.106 / hostssl / SCRAM. `timescaledb` ist vorgeladen (für die HA-DB). |
| MQTT | `external`, Broker LXC 115 `192.168.178.115:1883`, ohne TLS – verbunden |
| Aufbewahrung | 365 Tage Einzelmessungen, 3650 Tage Stundenwerte (übernommen) |
| Speicher | LXC 108 auf 148 GB vergrößert; Thin-Pool `pve/data` 794 GB, 31 % belegt (Überwachung als eigener Claude-Code-Auftrag, nicht hier) |
| Entfernt | Terra und Mimir deinstalliert, unverschlüsselte Vollsicherung gelöscht. Verschlüsselte Sicherung „Vor Wuschel Alpha 10 Teil B 2026-10-20“ bleibt. |
| Unberührt | Legacy-Integration `benni_core_contracts` v0.2.4 läuft parallel weiter; Title Classifier v3.5.1 mit eigener DB `media_catalog` |
| Später löschen | Alte Datenbanken `mimir` und `core_contracts_app` auf LXC 108 – erst bei Beta-Abnahme mit eigenem Go (Befehl im Runbook `docs/operations/wuschel-new-database.md`) |

Noch live zu bestätigen: Die **Programmversion von Traber** erscheint in Wuschel erst, wenn die
Konfiguration die erste HA-Quelle enthält (Wuschel verbindet sich vorher nicht mit HA). Kommt sie
dann nicht, ist es ein echter Fehler.

## 6. Grundlagen und Dokumente

- Entscheidungsakte: `D:\Dokumente\Core-Contracts-Konsolidierung\final\Core_Contracts_Master_Entscheidungsakte_Phase4_Auditrevision.md`
  (§30, M30-01 … M30-46). Phase 1 der Akte = unsere Alpha; Phase 2 = Beta + 1.0.
- Gap-Matrix: Issue `Levtos/wuschel#24`, `docs/audits/akte-gap-2026-10.md`. G1–G19 erledigt, G4 (Titelkatalog)
  in Alpha 10 gebaut, G20 nur geprüft.
- ADRs 0005–0027 im Repo (Alpha 8–10).
- b26-Betrieb: [Betriebsübersicht](operations.md), [Betriebswerte und Systemzustand](operations/b26-betrieb.md),
  [Selbstschutz und Projektionspause](operations/b26-selbstschutz.md),
  [Korrekturen #143](operations/b26-korrekturen.md).
- [HA-Schnittstellenversion 1](ha-interface.md): in b26 vorbereitet, mit 1.0.0
  eingefroren; bestehende Kennungen, Zustandsabbildungen und Attribute bleiben
  bis Version 2 erhalten. Der Vergleichstest läuft im echten HA-Kern.
- Keine Profile in Wuschel: Bennis Eltern (Vanaheim) bekommen eine eigene Wuschel-Installation.

## 7. Beta-Plan

- Beta = echte Verträge **Bereich für Bereich**, parallel zur Legacy-Integration. Keine DB-Resets mehr.
- Zum Kern gehören laut Akte auch: Weckplanung als 1:1-Port (`wake.plan`, P4-60), Tagesphase
  (`day_phase`), erwartete Sonneneinstrahlung, Klima-Formeln, Titelkataloge (P4-38).
- **1.0-Grenze, geändert am 14.11.2026 (#115, #136):** 1.0 nimmt die Plattform ab.
  Die frühere Forderung aus #80 nach allen künftigen Fachverträgen vor 1.0 ist
  ersetzt. Fehlende allgemeine Verträge und echte Plattformlücken gehören in #136;
  Fachverträge und Ausfallregeln einer Engine stehen spätestens vor deren Anschluss fest.
- Engines folgen nach 1.0 (#74, ADR 0028). Consumer-Umstellung und Abschaltung
  der Legacy-Integration sind kein 1.0-Kriterium mehr und brauchen eigene Aufträge.
- b25 hat die Paketgrenzen, Kartuschen und CI-Auswahl eingeführt. b26 ergänzt
  schnelleren Wiederanlauf mit sichtbaren Startphasen (#130), 55 getrennte
  Betriebseinstellungen und „Systemzustand“ (#131/#143), Lastschutz,
  Projektionspause und Wiederholmeldungen (#132), die vorbereitete
  Schnittstellenversion 1 (#133) und die Bedienkorrekturen (#134/#143).
- Fehlende Messwerte bleiben unbekannt. Commit-Last berücksichtigt nur Ereignis-
  und Zeitpunkt-Commits im Zeitfenster (Standard 60 s, mindestens fünf Proben),
  keine Verwaltungs-Commits. Nach Ruhe erholt sich der Lastschutz ohne Neustart.
  Betriebseinstellungen wirken erst nach Neustart; die Projektionspause endet dabei.
- Die zehn Wiedergabeaufzeichnungen aus b25 bleiben unverändert; neues Verhalten
  wird separat geprüft. Paketgrenzen bleiben ohne Ausnahme. `tests/areas.json`
  wird für #135 im manuellen CI-Job neu gemessen, nicht lokal in der vollen Suite.
- #135 liefert Sammel-PR, Nachprüfung des Korrektur-Diffs #143, Release nach Go
  und Live-Messungen: erster `startup_timing`, Systemzustand nach etwa zehn Minuten,
  Bereitschaft HTTP 200. Erst diese Werte erlauben eine Aussage zu b26 live.
- Die Plattform-Abnahme in #136 umfasst technische Ausfall- und Bedienprüfungen.
  Schwere Bedienprobleme sperren wie Korrektheitsfehler. Technische Fertigstellung,
  Veröffentlichung, Installation und Bennis Live-Abnahme bleiben getrennt.

## 8. Entscheidungen für den Bereich Media / Titelkataloge (20.10.2026)

Aktueller Bereich-5-Stand: [freigegebenes A1](beta/bereich-5/A1.md) und
[A2-Umsetzung mit Prüfnachweisen](beta/bereich-5/A2.md). Zweites Review und
Bennis zweites Go vor b15. Die Titelübernahme und weitere Pflege sind inzwischen
in #80 dokumentiert; dieser ursprüngliche A2-Nachweis ist kein aktueller Live-Befund.
Die Entscheidungen in #75 ersetzen die unten noch als offen bezeichnete
Apple-TV-Klassenbedeutung und den früheren Besuchsquellen-Vorschlag.

**Kataloge:** ein Katalog **pro Gerät**, nicht pro Medienart:
HomePods (Musik), PS5, PC, Stash, Apple TV, später Nintendo Switch.
Begründung: Dasselbe Spiel hat je Gerät eine andere Bedeutung (PC fast immer Headset, PS5 meist Heimkino/Grind).

**Legende:** Jeder Wert bekommt einen Namen und einen Satz Erklärung, sichtbar in der Katalogpflege und
im Zuordnungsdialog. Die alten Zahlen bleiben nur intern, bis die Media-Module umgestellt sind.

| Katalog | Werte (heutige Zahl) |
|---|---|
| Spiele (PS5, PC, Switch) | Heimkino (0) · Grind (1) · Headset (2) |
| Musik (HomePods) | normal (0) · Boost (1) · stumm (2) · **neu: leiser** |
| Stash | normal (0) · privat (≥ 1) |
| Apple TV | Die frühere offene Zweckfrage ist durch #75 und Bereich 5 ersetzt; maßgeblich ist dessen freigegebene Klassenlegende. |

- **Spiele-Klasse 3 („Hintergrund-Grind“, Title_classifier#83) entfällt.** Wer führt, entscheidet die
  TV-Bildquelle (`actual_source`, M30-28 / P4-51 / P4-52 / P4-53). Das eine Spiel mit Klasse 3 wird Grind.
  Issue #83 als „überholt durch den Quellen-Contract“ schließen.
- **Musik „leiser“:** Wunsch von Benni – ungeliebte Lieder bewusst leiser statt Sender wechseln. Stand
  bisher in keinem Dokument. Wuschel: neue Klasse mit Legende; Träller: Absenkung um einstellbaren Wert.
- **Besuch:** Die frühe Quellidee ist durch die Besuchs-/Date-Verträge in Bereich 5 ersetzt. Historischer Zweck: „Besuch da“ (bewusst ein/aus, Ende durch Schlaf wie bei Date Time);
  Träller: Lautstärke-Obergrenze für Musik. Akte: „Multi-User/Gäste zurückgestellt“, „Date Time“ (HomePods aus) ist etwas anderes.
- **Quiet** (Anruf, Etagentür, stummer Song) bleibt eine eigene Wahrheit, **keine** Titelklasse (P4-63).
- **Import aus dem Title Classifier:** in der Beta (Bereich Media), nach Gerät aufgeteilt; Musik- und
  Video-Varianten übernehmen die Klasse des Masters (= heutiges Verhalten). Probelauf (20.10.) war sauber:
  3181 Einträge, 0 abgelehnt, 0 Kollisionen, 6 Varianten mit abweichender Klasse (entschieden: Masterklasse),
  56 ausgeblendet. Privater Bericht im Codex-Ausgabeordner, nicht in GitHub.
- **Frischer Export** vor dem Import (das Panel „Import / Export“ ist nur ein Platzhalter) – in der
  Browser-Konsole von HA (F12 → Konsole):

```js
const hass = document.querySelector("home-assistant").hass;
const data = await hass.connection.sendMessagePromise({ type: "title_classifier/v3/export_entries" });
const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "title_classifier-export.json" });
document.body.appendChild(a); a.click(); a.remove();
console.log("Exportiert:", data.version, "Version,", data.entries?.length, "Einträge");
```

## 9. Offene Punkte (Beta-Backlog)

**UX**
- MQTT- und Traber-Status zeigen vor der ersten Konfiguration „ausgeschaltet“ bzw. „–“. Richtig wäre
  „Wartet auf die erste Konfiguration“ (Ursache: Adapter verbinden sich erst mit aktiver Konfiguration).
- Fenster „Offene Bedingungen“ schließt sich nicht, wenn man darin einen Link anklickt.
- Katalogpflege: Auswahllisten und Navigation unter „Verträge“ sind in b20 umgesetzt; Installation und Live-Abnahme stehen getrennt aus.
- Import, „Aktualität zuordnen“ (25.10.2026, Bereich 2): Der Dialog zeigt alle Quellen eines vollständigen
  Pakets, auch unveränderte aus früheren Bereichen. Außerdem fehlt ein passender Sensortyp für Quellen
  wie Ortung, WLAN-Name oder HA-Verbindung (nur Kontakt/Temperatur/Bewegung/Zähler). Richtig wäre: nur
  neue oder geänderte Quellen zuordnen; für alle übrigen „Einstellungen aus Datei behalten“ voreinstellen.

**Betrieb**
- Proxmox-Thin-Pool überwachen (eigener Claude-Code-Auftrag, nicht im Wuschel-Chat).
- Tatsächliche Datenrate und Speicherbedarf bleiben Betriebsaufgaben. b20 misst Pflegezeiten synthetisch; es führt keinen Speicherumbau aus.
- Gemessener Niederschlag, Connectivity DNS und Eltern-Standort haben getrennte offene Einrichtungsvoraussetzungen (Beta-Abschluss/#80). Temperaturprognose und Tür/Schloss bleiben bewusst vertagt.
- GitHub-Repobeschreibung von `Levtos/wuschel` (Admin-Recht nötig) – prüfen, ob gesetzt.

**Fachlich**
- Parameter-Audit Media (Volume-Matrix, Track-Boost/-Mute, Subwoofer, Denon-Nachlauf) – dort fehlte bisher
  die Stelle, an der „leiser“ aufgefallen wäre.
- Apple-TV-Katalog: Die frühere Zweckfrage ist durch #75 entschieden, kein neuer Klärungsauftrag.
- **Bewusster Schlafstart mit Ambient-Sounds** (Wunsch Benni, 25.10.2026; Bereich 4 Schlummer und
  Bereich 5 Träller). Benni schläft meist mit Fernseher ein, möchte aber manchmal stattdessen
  Ambient-Sounds über die HomePods. Aufteilung nach ADR 0028:
  - Wuschel (Bereich 4): Der Schlaf-Ablauf bekommt einen Befehl „Schlafen bewusst starten“. Er setzt
    Vor-Schlaf mit der Herkunft „bewusst gestartet“ (nicht „vermutet“); die 45-Minuten-Frist läuft wie gewohnt.
  - Wuschel (Bereich 5): `tv_power` (Fernseher an/aus) als Media-Wahrheit, ohnehin geplant.
  - Träller (Engine): Vor-Schlaf und Fernseher aus → Ambient-Sounds auf den HomePods; Fernseher an →
    nichts tun. Sound, Lautstärke und Ausblenden beim Einschlafen sind Träller-Einstellungen.
  - Offen, Bereich 4: Wie wird der Schlaf bewusst gestartet (HA-Knopf, iPhone-Kurzbefehl, Schalter am
    Bett)? Prüfen, ob ein Befehl aus HA über Traber bei Wuschel ankommen kann.
  - Offen, Bereich 5: Gilt die 45-Minuten-Frist auch mit Ambient-Sounds, und was passiert mit dem Sound
    beim Übergang in „Schlaf“ (aus, leiser, weiterlaufen)?
- **Besuch automatisch erkennen** (25.10.2026, Bereich 2, später): Heute gibt es nur die manuelle Quelle
  „Besuch da“. Bekannte Geräte von Familie im eigenen WLAN (z. B. Apple Watches der Eltern an der
  FRITZ!Box, belegt am 21.10.) könnten ein zusätzliches positives Signal liefern. Nur „da“ ist
  belastbar, „weg“ nicht. Alternative: iOS-Kurzbefehl bei WLAN-Verbindung ruft einen HA-Webhook auf
  (funktioniert auch bei geschlossener Companion-App). Erst umsetzen, wenn eine Engine es braucht (Regel 1).

## 10. Sicherheitsregeln (gelten weiter)

- Keine Werkzeuge oder API-Aufrufe, die App-Optionen samt Geheimnissen ausgeben; nachträgliches Filtern ist kein Ersatz.
- Passwortdateien auf LXC 108 (`/root/*_app.password`) nie ausgeben, protokollieren, committen.
- PostgreSQL-Admin-Zugang bekommt Wuschel nie.
- Kein DB-Reset und keine direkten DB-Änderungen ohne Bennis ausdrückliches Go.
- Keine HA-Sicherung vor App-Updates (enthält die Wuschel-Datenbank auf LXC 108 nicht). Vor Releases mit DB-Migration sichert Benni LXC 108 selbst; das Issue sagt, ob das Voraussetzung ist.
- Tests, Prüfungen und Schwellen nie löschen, überspringen oder lockern, um CI grün zu bekommen.
- Legacy v0.2.4, andere Apps und die Einhornzentrale-Konfiguration nicht anfassen.
- DNS-Änderungen (AdGuard/Unbound) nur an Benni melden.
- Echte Titel und private Daten nie in GitHub, Tests oder Fixtures.
