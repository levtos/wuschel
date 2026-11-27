# Schaltungen anmelden und Herkunft ausgeben

A1-Entwurf für [Wuschel #176](https://github.com/Levtos/wuschel/issues/176), Zielversion `1.0.0b32`. Der [Entscheid vom 27.11.2026](https://github.com/Levtos/wuschel/issues/176#issuecomment-7215660352) weist die Ausführung Cosmo zu. Wuschel führt den Herkunftsnachweis. Traber transportiert die Anmeldung. Bei jeder Schaltung ist die beauftragende Engine der Erzeuger. Die Umsetzung berücksichtigt das Claude-Review und den [Nachtrag vom 27.11.2026](https://github.com/Levtos/wuschel/pull/178#issuecomment-7217472556).

## Ausgangslage

- [ADR 0017](../adr/0017-ha-context-origin.md) verlangt positive Herkunftsbelege. Die bestehende Wuschel-API prüft für explizite Meldungen Erzeuger, HA-Prozessepoche und Gültigkeit. Beobachtungen aus Protokoll 2 speichern `HAContext` und Herkunft. Die Regel `known_automatic` prüft ausschließlich den positiven Automatikbeleg.
- Traber hat mit `send_command` bereits einen Rückkanal zur verbundenen Wuschel-Installation. Wuschel hat unter **Einstellungen → HA-Anbindung** bereits ein Formular für `context_producers`. Positiver interaktiver Ursprung ist im Modell vorgesehen. Der HA-Adapter erzeugt heute aus einer gewöhnlichen Zustandsänderung keinen solchen Beleg.
- HA erzeugt für einen WebSocket-Aktionsaufruf einen Kontext und liefert dessen ID in der Antwort. Die Zustandsänderung kann Wuschel vor dieser Antwort erreichen. [HA-WebSocket-Dokumentation](https://contributors.home-assistant.io/core/internals/websocket-api/#call-an-action) · [HA-Kern 2026.10.0](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/websocket_api/commands.py)

## Dienst und Quittung

**`traber.register_context`** meldet genau eine HA-Aktion über ihre originale Kontext-ID an. Die [Ergänzung zu HA-Schnittstelle v1](../ha-interface.md#schaltungen-anmelden) legt Eingang, Antwort und Beispiel für eine Engine fest. Der Dienst wird für Benutzeraufrufe durch HA auf Administratoren begrenzt. Für serverinterne Aufrufe gilt die Vertrauensgrenze des lokalen HA-Kerns.

1. Der Ausführer führt die Aktion über seine bestehende HA-Verbindung aus. Für eine zweite Engine verwendet er bei deren nächster Aktion deren eigene `producer_id`. Er meldet die ID der ausgeführten Aktion zeitnah an. Ein WebSocket-Ausführer liest sie aus `result.context.id`. Code im HA-Kern kann den selbst erzeugten, tatsächlich mitgegebenen HA-Kontext verwenden.
2. Traber sendet die Anmeldung über den bestehenden, authentifizierten Protokoll-2-Rückkanal. Die Fähigkeit `context_registration_v1` wird zusätzlich ausgehandelt. Ein eigener Nachrichtentyp und zugehörige Antwort unterscheiden Anmeldungen von Vertragsbefehlen. Traber setzt intern Anfragen-ID, Installationskennung, HA-Prozessepoche und Frist. Der Eigentümer der verbundenen Wuschel-Sitzung ist auch der einzige zulässige Antwortgeber.
3. Wuschel prüft in seiner seriellen Verarbeitung die Sitzung und Epoche, die Eingaben und den aktuell übernommenen Erzeuger. Nach dauerhaftem Schreiben des Auditbelegs setzt Wuschel den befristeten Automatiknachweis und quittiert. Eine Wiederholung derselben Anmeldung in der gleichen Epoche liefert innerhalb der bekannten Gültigkeit die ursprüngliche Frist. Die Quittung enthält keine Konto- oder Zielinformationen. Ein abweichender Erzeuger für dieselbe Kontext-ID wird abgewiesen. Ein verspäteter Eintrag verlängert einen noch bekannten abgelaufenen Nachweis nicht.
4. Eine bereits getrennte Verbindung oder volle Verarbeitung liefern eine Ablehnung. Bei einem Abbruch nach Weiterleitung oder einer ausbleibenden Antwort meldet Traber `unknown_result`. Eine unklare Quittung ist keine Aussage über den Ausgang der HA-Aktion. Anhängige Anmeldungen sind begrenzt, verfallen und werden bei Neuverbindung verworfen. Der Dienst arbeitet ausschließlich mit der ihm zugeordneten HA-/App-Installation.

Der neue Dienst übernimmt die Laufzeitgrenzen des vorhandenen Kontextindex (600 s, maximal 1.024 Einträge und acht Elternstufen). Die interne Anfrage antwortet spätestens nach der bestehenden Rückkanalfrist von 30 s. Wird die HA-Epoche gewechselt, werden anhängige Anmeldungen sowie flüchtige Kontext- und Nachweisdaten verworfen. Eine verspätete Anfrage der alten Epoche wird abgelehnt. Ein erneut übermittelter alter Identifikator kann ohne passende neue Beobachtung in der aktiven Epoche keine Herkunft belegen.

### Beobachtung und Anmeldung dürfen sich überholen

Wuschel verbindet die Anmeldung mit einem beobachteten Kontext über die genaue ID oder eine nachweisbare Elternkette innerhalb derselben Epoche. Die Anmeldung liefert nur eine ID. Sie trägt weder eine `user_id` noch Elternmetadaten ein. Der ID-Nachweis und die vollständigen HA-Kontextdaten werden separat geführt. Ein ID-Nachweis mit leeren Metadaten darf beim späteren Empfang eines vollständigen Kontexts nicht als Widerspruch gelten. Für widersprüchliche vollständige Kontexte und Zyklen gelten die bestehenden Regeln.

Erreicht der passende Wertwechsel Wuschel zuerst, hält die neue Vorlage die jüngste verwertbare Änderung mit Kontext und HA-Zeitpunkt für die begrenzte Zuordnung bereit. Bis zur Zuordnung kann ihre Herkunft „Unbekannt“ sein. Wuschel darf ihn nach der Anmeldung als Automatik ausgeben, wenn er noch der jüngste Wechsel des Eingangs ist, derselben HA-Epoche angehört und höchstens `lifetime_s` vor Eingang der Anmeldung liegt. Der Zeitpunkt des Wertwechsels bleibt erhalten. Ein späterer Wechsel, eine Lücke oder eine inzwischen unbrauchbare Quelle haben Vorrang. Der vorhandene Audit- und Beobachtungsverlauf bleibt erhalten. Die spätere Zuordnung ist ein eigener Beleg und schreibt den früheren Zustand anderer Verträge nicht um.

Bei Anmeldung zuerst kann ein passender Live-Wertwechsel bis `expires_at` zugeordnet werden. Ab der Frist bleibt ein neuer Wechsel ohne anderen Herkunftsbeleg unbekannt. Die Herkunft eines bereits bestätigten Wechsels bleibt als Aussage über diesen Wechsel erhalten, solange der Eingang verwendbar und die Folge der Beobachtungen lückenlos ist.

## Erzeuger pflegen

Unter **Einstellungen → HA-Anbindung** erhält das vorhandene Formular den Abschnittstitel **Erzeuger** und die Aktion **Erzeuger bearbeiten**. Text: „Hier kannst du festlegen, welche Steuerungen ihre Schaltungen anmelden dürfen. Trage für jede Steuerung eine eigene Kennung ein.“ Beim Erzeuger sind **Name**, **Kennung** und **Rolle** sichtbar. Erklärtext zur Kennung: „Gib diese Kennung beim Anmelden der Schaltung an. Ein gemeinsamer Ausführer verwendet die Kennung der Steuerung, die den Auftrag gegeben hat.“

- **Steuerung** verwendet den vorhandenen Typ `policy`. Vorhandene Einträge mit `apply` bleiben als **Ausführung** verständlich und weiterhin für die bestehende API gültig. Der neue Dienst ist für Erzeuger mit Rolle **Steuerung** vorgesehen.
- Die Übersicht zeigt die übernommenen Einträge. Ohne Einträge erscheint „Du hast noch keine Erzeuger eingerichtet.“ Das Bearbeiten nutzt den bestehenden Entwurf mit **Erzeuger hinzufügen**, **Entfernen**, **In Entwurf speichern**, Vorschau und **Übernehmen**. Schließen folgt dem vorhandenen Umgang mit ungespeicherten Änderungen.
- Pflichtfelder und Eindeutigkeit werden am Feld erklärt. Für eine doppelte Kennung erscheint „Diese Kennung wird bereits verwendet.“ Die Vorschau erklärt bei geänderten oder entfernten Erzeugern, dass Wuschel vorhandene Anmeldungen verwirft. Erst die Übernahme ändert die Zulassung. Die Wartedauer oder der Verbindungsstand sind kein Ersatz für eine Quittung.

## Eingebaute Vorlage „Manuell geändert“

Eine Vorlage für genau eine direkt verknüpfte HA-Quelle (Zustand oder Attribut). Sie verwendet die bestehende Normalisierung und Qualitätsprüfung. Entscheidend ist der jüngste live beobachtete Wechsel des konfigurierten normalisierten Werts. Unveränderte Meldungen, Änderungen anderer Attribute und Momentaufnahmen belegen keinen neuen Wertwechsel. Verknüpfungswechsel, HA-Neustart und Beobachtungslücken führen für die Vorlage zu „Unbekannt“, bis ein neuer verwendbarer Wechsel belegt ist.

| Herkunft | `value` („Manuell“) | Anzeige | Zusatz |
| --- | --- | --- | --- |
| `engine` | `false` | Automatik: Steuerung | `producer_id` |
| `ha_automation` | `false` | Automatik: HA-Automation oder Skript | `automation_id`, soweit belegt |
| `ha_user` | `true` nach Ablauf der Zuordnungsfrist | Manuell: über Home Assistant | `user_id` nur im geschützten Beleg |
| `unattributed` | `true` nach Ablauf der Zuordnungsfrist | Manuell: am Gerät oder ohne Beleg | – |

Die Zuordnungsfrist `attribution_wait_s` beginnt mit dem Empfang des verwendbaren Live-Wechsels. Sie beträgt standardmäßig fünf Sekunden und ist am Vertrag auf 1–60 Sekunden einstellbar. Bis zum Ablauf bleiben `value` und `origin` ohne Automatikbeleg unbekannt. Ein innerhalb der Frist eintreffender Automatikbeleg liefert direkt `false`. Eine gültige später eintreffende Anmeldung kann `true` auf `false` korrigieren. `changed_at` bleibt der HA-Zeitpunkt desselben Wechsels. Bei unbrauchbarer Quelle oder unbelegtem Wechsel bleiben die Ausgaben unbekannt.

`ha_user` bedeutet, dass der direkte HA-Kontext eine `user_id` und keinen Elternkontext enthält. Ein Automatikbeleg hat Vorrang. `unattributed` umfasst Wandschalter, Fernbedienungen, Siri/HomeKit und Integrationen, die ihre Schaltungen nicht anmelden. Die Daten erlauben keine weitere Fall- oder Geräteunterscheidung. „Manuell“ beschreibt den belegten Stand der Zuordnung. Der bisherige Herkunftsnachweis mit `known_automatic` behält seine Semantik.

Die originale HA-`user_id` bleibt im geschützten `HAContext`-Beleg. In Anmeldungen, gewöhnlichen Protokollzeilen und den HA-Sensorattributen stehen ausschließlich die für die jeweilige Schnittstelle bestimmten Felder. Diagnosefreigaben und Aufbewahrung folgen ADR 0017. Bei freigegebener HA-Projektion gilt die bestehende Abbildung `on`/`off`/`unknown`. Neue Verträge werden aus der Vorlage angelegt und wie üblich übernommen.

## Prüffälle für A2

| Ebene | Nachweis |
| --- | --- |
| Echter HA-Kern | Autorisierter Test-Ausführer schaltet zwei synthetische Wertewechsel im Auftrag zweier Engines. Der jeweilige HA-Aktionskontext wird als der richtige Erzeuger quittiert. Unberechtigter Benutzer und Fremdverbindung können keinen Nachweis anlegen. |
| Reihenfolge | Wertwechsel vor und nach Anmeldung ergeben „Automatik“ mit demselben belegten Wechselzeitpunkt. Eine zwischenzeitlich neuere Änderung bleibt maßgeblich. Vollständiger HA-Kontext nach Anmeldung nur mit ID ergibt keinen Scheinwiderspruch. |
| Zuordnungsfrist und Herkunft | Unbelegter Wechsel wird nach der Frist „Manuell“ mit `unattributed`. Direkter HA-Kontext mit Konto ergibt `ha_user`. Anmeldung vor Ablauf vermeidet einen zwischenzeitlichen manuellen Wert. Nach Ablauf korrigiert eine gültige Anmeldung die Herkunft bei gleicher Wechselzeit. Die originale `user_id` bleibt im geschützten Beleg. |
| Ablehnung und Zeit | Unbekannter oder nur im Entwurf angelegter Erzeuger, gesperrte Rolle, widersprüchlicher Erzeuger, ungültige Dauer, abgelaufene Gültigkeit einschließlich Fristgrenze, Commitfehler und Antwortausfall. Wiederholung darf die Frist nicht verlängern. |
| Epochen und Quellen | HA-Neustart verwirft alte Anmeldungen. Ein neues Kontextpaar funktioniert. Momentaufnahme, unveränderte Meldung, Verbindungsunterbrechung, veraltete Quelle und Neuverknüpfung liefern keinen Ersatzbeleg. |
| Oberfläche und Kompatibilität | Vorschau, Entwurf, Übernahme und deutsche Texte für Erzeuger. Neuer Vertrag projiziert `value`, `origin`, `changed_at` und wahlweise befüllte Erzeuger- oder HA-Laufkennung. Eingefrorene HA-v1-Aufzeichnungen, REST-Registrierung, `known_automatic` und `send_command` durch ihre bestehenden Tests absichern. |

A2 umfasst Traber-Dienst und Protokollzusatz, Kontextverarbeitung und Belege, Vorlage, Erzeugerpflege sowie Prüfungen. Vor dem Push einer Traber-Änderung gilt der lokale HA-Kerntest mit den exakten Versionen aus `ha-bridge`; vor der späteren Veröffentlichung folgt die vollständige CI. Messwerte zur Anmeldung und Wiederverbindung dienen der Einordnung. Die Traber-Manifestversion wird bei der Umsetzung auf `1.0.0b32` abgestimmt.

## Text für den App-Changelog in b32

- Steuerungen können Schaltungen über Traber anmelden. Ein gemeinsamer Ausführer meldet sie mit der Kennung der beauftragenden Steuerung.
- Die Vorlage „Manuell geändert“ zeigt den Zeitpunkt und die Herkunft des letzten belegten Wechsels. Du kannst die Zuordnungsfrist am Vertrag einstellen.
- Erzeuger lassen sich in der HA-Anbindung pflegen und per Entwurf übernehmen.

Mit A2 erhält `wuschel/CHANGELOG.md` den b32-Abschnitt für den Supervisor. `CHANGELOG.md` im Repository erhält denselben Eintrag. Die Übernahme und Live-Abnahme folgen ihren eigenen Freigaben.
