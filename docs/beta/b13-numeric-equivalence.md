# b13 – Gleichwertige JSON-Zahlen

Verbindlicher Auftrag: [Issue #67](https://github.com/Levtos/wuschel/issues/67).
Diese Entscheidung ersetzt für Zahlenvergleiche die frühere Abgrenzung in #64.

Browser unterscheiden JSON-Zahlenschreibweisen wie `840` und `840.0` nicht.
Wuschel vergleicht endliche Zahlen deshalb nach ihrem exakten Wert, ohne
Toleranz oder Umwandlung von Bool/Text. `840` und `841`, `true` und `1` sowie
`"840"` und `840` bleiben verschieden.

Die Schema-Prüfung des eingereichten Inhalts erfolgt weiterhin. Für gültige
Objekte werden gleichwertige Zahlen aus der gespeicherten Fassung zurückgeführt:
zuerst aus dem gespeicherten Entwurf, dann aus der aktiven Konfiguration und bei
unveränderlichen Versionen aus ihrer gespeicherten Historie. Objekte werden
anhand ihrer Kennung und gegebenenfalls Version zugeordnet, unabhängig von ihrer
Reihenfolge. Auch bei einer anderen echten Bearbeitung bleiben unveränderte
Zahlenwerte in ihrer gespeicherten Darstellung erhalten.

Dies gilt beim Validieren, Speichern, Importieren und Übernehmen. Der Vergleich
unveränderlicher Vorlagen/Kataloge/Sensorprofile und die Änderungsliste verwenden
dieselbe Gleichheit. Der Import prüft zunächst das Originalpaket einschließlich
seiner Prüfsummen; nur der daraus abgeleitete Plan verwendet die gespeicherten
Zahlendarstellungen. Revisions- und Planbindung bleiben strikt.

`canonical`, `checksum`, gespeicherte Fingerabdrücke und historische Daten werden
nicht geändert oder migriert. Ein bestehender Importentwurf kann nach dem Update
ohne Neuimport geprüft und anschließend von Benni bewusst übernommen werden.
Lesende Prüfung speichert oder übernimmt keinen Entwurf.

## Nachweise

- Synthetische Jahreskurve mit `840.0`: Browser-Rundlauf zu `840`, Validierung,
  Entwurfsablage und Übernahme mit unveränderten gespeicherten Bytes und Fingerabdrücken.
- Gegentests für echte Wertänderungen, Bool-/Textwechsel, Sensorprofile,
  historische Vorlagen und einen vorhandenen Importplan ohne Neuimport.
- `workbench-e2e` lädt eine tatsächlich als `840.0` gespeicherte Jahreskurve,
  prüft sie und speichert über die Oberfläche. Der Test belegt `840` auf dem
  Browser-Drahtweg und weiterhin `840.0` im gespeicherten Entwurf, ohne Aktivierung.

Wuschel 1.0.0b13; Traber bleibt unverändert auf 1.0.0b11. Keine DB-Migration.
Live-Nachweis am vorhandenen privaten Importentwurf nur lesend nach dem Update.
