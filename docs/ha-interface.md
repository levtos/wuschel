# HA-Schnittstelle für Engines (ab 1.0.0b23)

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
