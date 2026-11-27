# HA-Schnittstelle für Engines (ab 1.0.0b23)

**Schnittstellenversion 1, eingefroren mit 1.0.0**

Bis zu einer Schnittstellenversion 2 sind nur Ergänzungen erlaubt: neue Attribute
und neue Verträge. Bestehende Entitäts-IDs, `unique_id`, Zustandsabbildungen und
Attributnamen bleiben erhalten. Umbenennen und Entfernen brauchen eine
Entscheidung von Benni und eine Übergangszeit. Die Festschreibung wird in b26
vorbereitet; sie ist keine Freigabe oder Veröffentlichung von 1.0.0.

Engines lesen öffentliche Wuschel-Verträge über Traber. Quellen, Verknüpfungen
und deren Rohentitäten sind keine Engine-Schnittstelle. Die Entitäts-ID und
`unique_id` bleiben bei Verbindungswechsel, Neuladen und HA-Neustart stabil.

Der Sensorzustand ist die Kernaussage des Vertrags. `primary_field` benennt
das ausgewählte Feld; `fields` enthält weiterhin sämtliche Felder mit ihrem
unveränderten Rohwert sowie `status`, `quality`, `since_at` und `unit`.
Zusätzlich enthält `raw_value` den Rohwert des Primärfelds.

| Feldstatus | HA-Zustand |
| --- | --- |
| `valid`, `held` | Skalarwert; Boolean immer `on`/`off` |
| `not_applicable` | Fester Zustand `not_applicable` |
| `unknown`, `unresolved` | `unknown` |
| Transport unterbrochen, letzte Projektion vorhanden, unter 120 s | Letzter Zustand; Attribut `status=stale`, `quality=degraded` |
| Transportunterbrechung ab 120 s oder Vertrag nicht mehr publiziert | `unavailable` |
| Verbindung offen, aber seit mindestens 120 s kein Projektionspaket | `unknown`; Hauptwert und Felder mit `status=stale`, `quality=degraded` |

`stale` ist eine Transportdiagnose, kein neuer fachlicher Beleg. Engines müssen
die Qualitätsattribute berücksichtigen und dürfen daraus keine frische Wahrheit
ableiten. Ein explizites fachliches `unknown` wird sofort sichtbar. Eine echte
Nichtanwendbarkeit bleibt von fehlendem Wissen unterscheidbar. Bei
`not_applicable` entfällt die numerische HA-Einheit, damit HA den festen
Textzustand akzeptiert; die Einheit im Feld bleibt erhalten.

Zahlen werden im Rohwert nicht gerundet. Traber liefert HA eine
`suggested_display_precision`: Temperatur 1, Prozent/Lux/Pa/Grad/Sekunden/Minuten
0, W/Wh/Stunden/m/s/km/h/mm/hPa/dB 1, kW/km 2, kWh 3 Nachkommastellen.
HA kann eine vom Nutzer gewählte Anzeigegenauigkeit darüberlegen.

## Benannte Medienklassen nach dem Umzug (b31)

Die Angaben gelten, sobald der Umzug der Katalogklassen übernommen wurde.
Traber schlägt für die folgenden Verträge Entitäts-IDs vor. Bestehende,
in Home Assistant individuell vergebene Entitäts-IDs können abweichen.
Die `unique_id` lautet jeweils `<installation_id>:<contract_id>`.

| Vertrag | Vorgeschlagene HA-Entitäts-ID | Primärfeld | Felder nach dem Umzug |
| --- | --- | --- | --- |
| `media.homepods.track_class` | `sensor.wuschel_media_homepods_track_class` | `value` | `value` |
| `media.homepods.track_adjustment` | `sensor.wuschel_media_homepods_track_adjustment` | `volume_offset` | `volume_offset`, `damped` |
| `media.ps5.title_class` | `sensor.wuschel_media_ps5_title_class` | `value` | `value`, `sound_mode`, `light_look`, `volume_offset` |
| `media.pc.title_class` | `sensor.wuschel_media_pc_title_class` | `value` | `value`, `sound_mode`, `light_look`, `volume_offset` |
| `media.appletv.app_class` | `sensor.wuschel_media_appletv_app_class` | `value` | `value`, `content`, `volume_offset` |
| `media.appletv.title_class` | `sensor.wuschel_media_appletv_title_class` | `value` | `value`, `content`, `volume_offset` |
| `media.stash.class` | `sensor.wuschel_media_stash_class` | `value` | `value`, `content`, `volume_offset` |

TV-Gerät und TV-Aktivität behalten ihre bisherigen Feldnamen und Codes.
`volume_offset` wird in ganzen Punkten geliefert. Ein Punkt entspricht
0,01 HA-Lautstärke. Für HomePods gilt im bisherigen `value` die Reihenfolge
`mute` bei Dämpfen, `boost` bei positivem Wert, `quieter` bei negativem
Wert und sonst `normal`. Bei Stash erhält ein aktiver privater Slot
Vorrang. Bei mehreren gleichrangigen Slots entscheidet die Slot-Reihenfolge.

Der Apple-TV-Titelvertrag liefert die bereits berechnete Summe aus App- und
Titelkorrektur (−60 bis +60 Punkte). Bei belegter Nichtanwendbarkeit des
Titels gilt die App-Korrektur. Bei unbekanntem Titel bleibt die Summe
unbekannt. Die Engine verwendet die veröffentlichte Summe direkt.

## Was eine Engine bei welchem Zustand tun muss

Wuschel liefert die Wahrheit. Die Engine entscheidet, was sie damit tut, und
begründet ihre Entscheidung sichtbar mit Vertrags-ID, Wert und Zustand. Sie
berechnet die Wahrheit nicht erneut und liest keine Rohentitäten als Ersatz.

| Zustand / Attribut | Bedeutung | Pflicht der Engine |
| --- | --- | --- |
| `unavailable` | Der Vertrag ist nicht verfügbar, etwa nach Ablauf der 120-s-Frist bei Verbindungsverlust. Es gibt keine frische Wahrheit. | Wähle Deinen sicheren Zustand. Verwende den letzten Wert nicht als frische Freigabe. |
| `unknown` (auch bei Feldstatus `unresolved`) | Die Aussage ist unbekannt oder nicht aufgelöst. Ein Auswertungsfehler liefert ebenfalls `unknown`, nie den letzten Wert als Ersatz. | Wähle Deinen sicheren Zustand. Ersetze fehlendes Wissen nicht durch eine eigene Berechnung. |
| `stale` | Nur eine alte Projektion ist vorhanden. Der letzte Zustand kann während der Karenz noch sichtbar sein; bei offenem Socket ohne neue Pakete wird er nach 120 s `unknown`. | Berücksichtige `status` und `quality` und wähle Deinen sicheren Zustand. Leite daraus keine frische Wahrheit ab. |
| `held` | Der Wert wird innerhalb seiner Gnadenfrist gehalten. Er ist kein frisch bestätigter Wert. | Entscheide nach der Sicherheitsregel Deiner Engine, ob Du ihn nutzen darfst; sonst wähle Deinen sicheren Zustand. ADR 0028 erlaubt ihn im Öffnungsbeispiel für Blinzel, für Kuschels Heizfreigabe nicht. |
| `not_applicable` | Die Aussage ist ausdrücklich nicht anwendbar. Das ist von fehlendem Wissen zu unterscheiden. | Behandle sie als nicht anwendbar und wähle den dafür sicheren Zustand Deiner Engine. Deute sie nicht als gültigen Messwert oder Freigabe. |

Quelle für Zuständigkeit, Begründung und die Auswahl bei `held`:
[ADR 0028](adr/0028-grenze-wuschel-engines.md).
Quelle für Ausfall und sicheren Zustand:
[Prüfbericht, Abschnitt 6.4](audits/architekturpruefung-2026-11-11.md#64-was-passiert-bei-ausfall).
Die Tabelle legt keine neuen fachlichen Aktionen oder Schwellen für Engines fest.

## Vergleichstest der Schnittstellenversion 1

`tests/test_bridge_kernel.py::test_frozen_ha_interface_v1` führt synthetische
Verträge durch Wuschels echte Projektion und Traber bis in den echten HA-Kern.
Der CI-Job `ha-bridge` vergleicht die sichtbare Ausgabe mit kanonischem JSON in
`tests/fixtures/ha_interface/`: Entitäts-ID, `unique_id`, HA-Zustand, sämtliche
HA-Attribute und Verfügbarkeit, zusätzlich die vorgeschlagene Anzeigegenauigkeit.
HA-interne Zeitstempel und Kontexte gehören nicht zur Vertragsausgabe.

Abgedeckt sind Skalar und Mehrfeld mit Primärfeld, gültig (`valid`, fachlich
„ok“), `unknown`, `unresolved`, `held`, `not_applicable`, beide Qualitätsstufen,
Wahrheitswerte (an/aus), Zahl, Text, Zeitpunkt und Auswahlwert. Dazu kommen die
dokumentierten Primärfeldregeln und Einheiten. `stale` entsteht durch den
Transport: verbunden, getrennt seit 119 s bzw. 121 s, sowie offener Socket ohne
Paket seit 120 s. Die genaue Trennungsgrenze bei 120 s und die Erholung mit
identischem Paket werden ebenfalls geprüft.

Ein normaler Testlauf schreibt keine Aufzeichnungen, auch nicht bei fehlender
Datei. Nur `WUSCHEL_INTERFACE_RECORD=1` erlaubt die bewusste Neuaufzeichnung mit
dem Python und den Paketversionen aus dem Job `ha-bridge`, zum Beispiel:

```bash
WUSCHEL_INTERFACE_RECORD=1 .ha-venv/bin/python -m pytest tests/test_bridge_kernel.py -k frozen_ha_interface_v1 -q
```

Jede Änderung der Aufzeichnungen wird nach der Kompatibilitätsregel oben
geprüft und löst immer die volle CI aus.

## Primärfelder der Mehrfeldvorlagen

Die Auswahl hängt nicht von der Reihenfolge der Schlüssel in JSON ab. Die
vorhandenen eingebauten und mitgelieferten Mehrfeldvorlagen sind geprüft:

| Vorlage | Primärfeld | Bedeutung |
| --- | --- | --- |
| `media.playback_facts` | `playing` | Wiedergabe läuft |
| `media.privacy_facts` | `active` | Privatsituation aktiv |
| `media.quiet_facts` (v1/v2) | `entry_door_open` | Türfakt; `track_muted` bleibt ein eigener Fakt in `fields` |
| `climate.thermostat` | `temperature` | Temperatur; Ventilstellung als Nebenfeld |
| `opening_zone.facts` | `unclear` | Bestehende explizite Bereitschaftsprojektion der Zone |
| `wake.plan_view` | `status` | Planstatus |
| `device.console_state`, `device.pc_power`, `media.activity_flow`, `media.device_power_facts`, `media.device_state`, `media.stash_slot`, `sleep.flow`, `sleep.state_view` | `value` | Bereits definierter Hauptzustand |

Weitere Vorlagen verwenden `value`, sonst ihre explizite `available_projection`,
sonst das erste skalare Schemafeld. Für neue Mehrfeldvorlagen muss der gewählte
Hauptzustand vor einer Engine-Anbindung ausdrücklich geprüft werden.

## Wiederanlauf

Jede neue Bridge-Verbindung sendet sofort eine vollständige Projektion. Zusätzlich
wird sie spätestens alle 5 s vollständig erneuert. Ein Bestätigungs-Timeout
wiederholt nur die Projektion mit begrenztem Backoff. Ein voller Eingabepuffer
koalesziert je Verknüpfung und trennt die Bridge nicht. Ein echter
Verbindungsverlust verbindet mit exponentiellem Backoff und Jitter neu; im
Normalfall nach Reload/HA-Neustart stehen die Entitäten innerhalb von 10 s
wieder bereit. Live-Abnahme dieses Verhaltens bleibt Bennis Gate.

Traber vergleicht die geprüften Vertragszeilen einschließlich aller Nebenfelder
mit dem vorherigen Stand. Nur geänderte, neue oder entfallene Verträge lösen
Zustandsschreibungen ihrer Sensoren aus. Verbindungsaufbau und -trennung,
Karenzablauf sowie Wechsel der Installations- oder Epochenkennung aktualisieren
alle Sensoren. Vollständige Projektion, Protokoll und Entitätskennungen bleiben
unverändert; neue Verträge werden weiterhin automatisch angelegt.

## Schaltungen anmelden

> Geplante Ergänzung zu Schnittstellenversion 1 für b32. Der
> [A1-Entwurf](beta/schaltungen-anmelden-a1.md) beschreibt die Freigabe und die
> Prüffälle.

`traber.register_context` meldet die Herkunft einer ausgeführten HA-Aktion an.
Die Engine, in deren Auftrag geschaltet wurde, ist der Erzeuger. Ein gemeinsamer
Ausführer verwendet bei getrennten Aktionen die jeweils beauftragende Engine.
Die Anmeldung läuft über die bestehende Traber-Verbindung zur Wuschel-App
derselben HA-Installation. Für den Aufruf gelten HA-Adminrechte.

| Eingabe | Bedeutung |
| --- | --- |
| `context_id` | Originale HA-Kontext-ID der ausgeführten Aktion. Nicht leer, höchstens 128 Zeichen. |
| `producer_id` | Kennung einer in Wuschel übernommenen Steuerung. Es gilt die Kennung aus der Erzeugerpflege. |
| `lifetime_s` | Optional. Ganze Sekunden von 1 bis 600, Standard 600. |

Bei `status: accepted` liefert Wuschel `expires_at` als ISO-8601-Zeitpunkt in
UTC. Die Quittung bestätigt die Anmeldung. Der Zustand des Geräts folgt aus
der tatsächlichen Beobachtung. Eine Wiederholung in derselben Epoche für
denselben Erzeuger innerhalb der Gültigkeit behält die ursprüngliche Frist.

| Antwort | Bedeutung |
| --- | --- |
| `rejected` · `producer_not_allowed` | Der Erzeuger ist unbekannt oder seine Rolle gestattet diese Anmeldung nicht. |
| `rejected` · `context_conflict`, `context_epoch_conflict`, `context_expired` | Abweichender Erzeuger für dieselbe Kontext-ID, veraltete HA-Epoche oder bekannte abgelaufene Anmeldung. |
| `rejected` · `disconnected`, `unsupported_protocol`, `busy`, `storage_unavailable` | Die Anmeldung konnte über die aktuelle Verbindung nicht verarbeitet oder gesichert werden. |
| `unknown_result` · `timeout` | Die Frist für die Quittung ist abgelaufen. Der Ausgang der Anmeldung ist unbekannt. |

Bei ungültigen Eingabefeldern oder fehlender Berechtigung gilt HA-Validierung.
Aus einer Ablehnung oder einer unklaren Antwort ergibt sich keine Anweisung,
eine Aktion erneut auszuführen. Nach HA-Neustart gilt die neue Epoche.

Beispiel für eine synthetische Engine mit eigenem Ausführer über die
HA-WebSocket-API. Der Ausführer führt zuerst den normalen HA-Aufruf aus.

```json
{"id":41,"type":"call_service","domain":"switch","service":"turn_on",
 "target":{"entity_id":"switch.example"}}
```

Nach Erfolg übernimmt er `result.context.id` genau dieses Aufrufs in eine
zweite Anfrage. Für die Rückmeldung des Anmeldedienstes fordert er die
HA-Antwortdaten an. [HA-WebSocket-API](https://contributors.home-assistant.io/core/internals/websocket-api/#call-an-action)

```json
{"id":42,"type":"call_service","domain":"traber","service":"register_context",
 "return_response":true,
 "service_data":{"context_id":"01KFX00ABCDEF1234567890GHJ",
                 "producer_id":"example.engine","lifetime_s":60}}
```

Die Anmeldequittung steht bei erfolgreichem HA-Aufruf in `result.response`,
zum Beispiel `{"status":"accepted","expires_at":"2026-11-27T10:01:00Z"}`.
`result.context.id` dieser zweiten Anfrage gehört zum Anmeldedienst selbst.

Ein freigegebener Vertrag aus der Vorlage **Manuell geändert** liefert `value`
als Ja bei belegter menschlicher Bedienung, als Nein bei belegter Automatik
und mit Status `unknown` bei offener Herkunft. `changed_at` liefert den
belegten Zeitpunkt des Quellwechsels. Es gilt die bestehende HA-Abbildung
für diese Feldtypen.
