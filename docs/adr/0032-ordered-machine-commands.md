# ADR 0032 – Geordnete Übergänge und belegte Fristreset-Ereignisse

- Datum: 26.10.2026
- Status: **angenommen für A2 am 26.10.2026; technisches Review vor Veröffentlichung offen**
- Auftrag: [wuschel#61](https://github.com/Levtos/wuschel/issues/61)
- Ausarbeitung und Testmatrix: [Bereich 4 A1](../beta/bereich-4/A1.md)
- Ergänzt nach Freigabe ADRs [0015](0015-state-machine-deadlines.md) und
  [0017](0017-ha-context-origin.md); ändert keine alte Sprachsemantik.

## Problem und belegter Ist-Zustand

Sprachversionen 1–5 erlauben pro Zustand und Ereignis nur einen Übergang.
`declarative.check_definition` meldet `duplicate_transition_event`.
Der native Ablaufkern beendet die Suche auch beim ersten false-Guard.
`MachineDefinition.coherent` erlaubt einen Fristreset nur bei `event:input`;
ein Command meldet `reset_duration_required`. Ein kurzlebiger Bool-Puls
unterscheidet zwei schnell aufeinanderfolgende Eingangsereignisse nicht sicher.
Die bestehenden `command_log`- und Processor-Grenzen sind dagegen bereits
wiederholsicher und atomar nutzbar.

## Beschlossenes Modell und Format

Nur Sprachversion 6 darf `transition_selection:ordered` setzen. Die deklarierte
Arrayreihenfolge ist die Priorität und Teil der Semantik-Prüfsumme. Für zum
Quellzustand und Event passende Alternativen gilt:

1. Nicht passende Command-Argumente überspringen; typfalsche Argumente vorher
   über das Command-Schema ablehnen.
2. Guard false: nächste Alternative. Guard unknown: keine späteren Alternativen.
3. Guard true: diesen Übergang genau einmal anwenden und Suche beenden.
4. Evaluationsfehler: sichtbarer Fehler, kein false und keine Ersatzalternative.

Ein Eingangsereignis löst höchstens einen Eingangsübergang aus. Fällige
Zeitbedingungen werden anschließend nach ADR 0015 bewertet. Zeitgleiche
verschieden gerichtete Dauer-/Absolutregeln behalten ihre ausdrückliche
Priorität. Ein unbekannter Guard verbraucht keine fällige Frist. Beim
Durchlaufen aller false-Guards wird ein dauerhaft fälliger Dauertermin nur
nach der bereits dokumentierten Deadline-Semantik als verarbeitet markiert;
kein wiederholender Sofortalarm.

Für Sprachversion 6 erlaubt `deadline_action:reset` auch deklarierte Commands.
Ein neuer gültiger Command ist selbst der Ereignisbeweis. Eine Wiederholung
derselben ID liefert das committed Ergebnis und erneuert die Frist nicht.
Reset erhält Zustand, Zustandsbeginn, Episode und Sitzung und setzt neue
Frist-ID/-Generation mit `now + volle Dauer`. Gleiche ID mit anderem Inhalt
bleibt Konflikt. Reset ohne Zieldauer oder zusammen mit neuer/beendeter
Episode bleibt ungültig.

Für normale Eingänge ergänzt `event_match` die Modi `changed|rising|falling`.
Es qualifiziert ausschließlich die aktuelle Processor-Arbeit anhand ihrer
ursprünglichen Evidence, Zeit, `ingest_seq` und Epoch. Ein späterer Tick oder
eine Downstream-Neuveröffentlichung ist kein neues Ereignis. Das ausdrückliche
`reset_trigger:event` verbraucht die aktuelle Identität je Übergang nur einmal.
Ohne Angabe gilt weiter der bisherige Nein→Ja-Reset; kein versteckter Wechsel
für gespeicherte Definitionen. Gleichartige Events in einem früheren Pulsfenster
bleiben getrennt. Fehlende/alte/Retained-/Snapshot-Evidence qualifiziert nicht.
Der erste verwendbare Wert nach einer Beweislücke ist eine Baseline.

Eine optionale generische `entry_label` am Übergang sowie gespeicherte
Eintrittsmetadaten trennen ursprünglichen Eintritt von späterem Fristreset
und Restore. Selbstreset erhält das Eintrittslabel; letzter Auslöser/Command
und dessen Zeit bleiben separat nachvollziehbar. Labels stammen aus der
Definition, keine Schlafbegriffe im allgemeinen Kern.

## HA→Wuschel über den bestehenden Command-Weg

Traber bietet den allgemeinen HA-Dienst `traber.send_command` mit
Contract, Command, Args und optionaler Request-ID. Auf der bestehenden
Admin-WebSocket-Verbindung wird eine zusätzliche Protokoll-2-Fähigkeit
`command_ingress_v1` ausgehandelt. Traber bindet die Subscription exklusiv an
Installation, Client und aktuelle HA-Epoch. Anfragen gehen nur über diesen
Live-Kanal; Antworten bestätigen erst das Processor-/command_log-Ergebnis.
Die konkreten Hüllen, Limits, Gültigkeit und Fehlerfälle stehen im A1 §4.

Es gibt keine dauerhafte Traber-Warteschlange und kein Nachsenden nach
Verbindungsabbruch oder Neustart. Ein erneuter physischer Aufruf bekommt eine
neue UUID; ein Transportretry behält ID und ursprüngliche Hülle. Bei verlorener
Antwort wird zuerst der vorhandene Ergebnisweg verwendet. Kein neuer
Bearer-Schlüssel oder HTTP-Ziel in der HA-Konfiguration; keine allgemeine
Vertragswertüberschreibung. Die Anwendung des Dienstes durch Switch Manager
ist eine spätere Benni-Konfiguration, keine Produktivänderung durch A1.

## Validierung, Restore, Atomarität und Kompatibilität

- Neue Felder nur bei Sprache 6; geschlossene Auswahllisten, vorhandene
  Zustands-/Übergangs-/Ressourcenlimits und vollständige Typprüfung.
- Snapshot, Retained-MQTT, Konfigurationsübernahme und Restore erzeugen kein
  `event_match`. Baselines/Ereignisverbrauch sind gegen Runtime-Epoch und
  Semantik gebunden, keine Beweiskontinuität über eine Lücke erfinden.
- Frist, Zustand, Metadaten und Command-Ergebnis werden gemeinsam committed.
  Bei Fehler weder Bestätigung noch Veröffentlichung; alte Generation wirkungslos.
- Registry 4, Fingerabdruck 4 und Paket 2 bleiben. Neue Definitionen enthalten
  die neuen semantischen Felder; fehlende Defaults alter Definitionen werden
  beim Serialisieren ausgeschlossen. Alte Versionen bleiben byte-semantisch
  und in ihren Ergebnissen unverändert.
- Bestehende JSON-Knotenzustände und `command_log` reichen aus; keine neue
  Tabelle und keine DB-Migration vorgesehen. Eine unerwartete Migration
  benötigt vor Umsetzung einen freigegebenen Designnachtrag.

## Tests und Folgen

A2 prüft Reihenfolge/unknown/alle false, Argumentalternativen, Ressourcenlimits,
zwei schnelle Ereignisse, wiederholte Commands, Idempotenz/Konflikt, Expiry,
Revision, Installation/Epoch/Autorisierung, Trennung/verlorene Antwort,
Snapshot/Retained/Restore, alte Alarme, Commitfehler und die unveränderten
Fingerabdrücke/Ergebnisse alter Sprachversionen. Traber-Änderungen benötigen
vor Push den vorhandenen HA-Kernel-Test mit den CI-identischen Versionen.

P4 benötigt keine neue Fristoperation: `clear` funktioniert bereits. Die
synthetische Probe ist im A1 verlinkt. Kein Schlafzustand und kein Geräte-
oder Gestenname wird in den Plattformkern aufgenommen. Die A2-Umsetzung ist in [A2.md](../beta/bereich-4/A2.md) dokumentiert;
Aktivierung und Veröffentlichung benötigen das zweite Review und Benni-Go.


A2 übernimmt K1–K5 aus Claudes Review sowie die gemeinsame Kalenderquelle
und das unveränderte Fensterlimit aus dem [Issue-Nachtrag](https://github.com/Levtos/wuschel/issues/61#issuecomment-6415883723).
Regex wird begrenzt inline ausgewertet; kein eigener Prozess.
