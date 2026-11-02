# ADR 0029 – Entwurfsabschluss, Übernahmebelege und sichere Weiterführung

- Status: **Angenommen** – Go von Benni für A2 b3 am 22.10.2026.
- Datum: 22.10.2026.
- Auftrag: [Wuschel #35](https://github.com/Levtos/wuschel/issues/35).
- Basis: `c8d4dff35048f0c69290ea500b0609e89d531a60`, nach PR #34.
- Konkretisierung: [UX-A1 §§3–6 und Testliste](../beta/ux/A1.md),
  [Konzept §§3–4](../beta/ux/konzept-2026-10-22.md),
  [ADR 0026](0026-import-export.md), [ADR 0028](0028-grenze-wuschel-engines.md).

## Problem und Entscheidung

Eine im Entwurf gespeicherte Grundlage ist heute nicht als Übernahmesperre
durchgesetzt. Die vom Frontend gesendete aktuelle Revision kann einen alten
Gesamtstand daher zur Vollersetzung freigeben (F1). Importpläne bleiben nach
Übernahme am weiter editierbaren Entwurf und werden bei späterer Änderung
weitergetragen (F2). Ein erneutes Prüfen löst diesen Lebenszyklusfehler nicht.

Beschlossen ist ein persistenter Entwurfs-Lebenszyklus mit unveränderlichem
Abschluss, genau zuordenbarem Übernahmebeleg und eigenem Weiterführungsweg.
Wuschel bleibt alleiniger Eigentümer von Registry/Plan und ihrer Prüfung.
Die Oberfläche beschreibt den Serverzustand und bietet passende Aktionen.

## Invarianten

1. Lebenszyklus `open | completed | continued`; Grundlagenzustand separat
   `known | needs_review`, aktuelle Grundlage als Vergleich mit aktivem Stand.
2. Neue Übernahme nur bei open, exakt gespeichertem draft_version und
   **draft.base_revision == state.active_revision** mit passender Checksum.
   expected_active_revision bleibt zusätzliche Konkurrenzsperre. Der Server
   liest Grundlage und Entwurf selbst; keine Reparatur durch Client-Nummerierung.
3. Registryrevision, Editorial/Katalogeffekte, Plan consumed, Entwurf completed
   und Erfolgsbeleg werden in derselben vorhandenen Writer-Transaktion gespeichert.
   Keine Nebenverbindung, kein zweiter nachgelagerter Abschluss-Commit.
4. request_id und Digest binden genau einen Übernahmeauftrag. Derselbe Auftrag
   liefert denselben dauerhaften Erfolgsbeleg, auch nach Antwortverlust/Restart.
   Gleiche ID mit anderem Inhalt ist ein Konflikt. Jede draft_id/version kann
   höchstens einen erfolgreichen Übernahmebeleg besitzen.
5. Erfolgsbeleg nach Auth-Prüfung vor neuen Basis-/Lebenszyklussperren lesen:
   Eine erfolgreiche Wiederholung bleibt erfolgreich, obwohl die alte Basis
   inzwischen veraltet und der Entwurf erledigt ist.
6. Kein Erfolg vor Commit. Unbekannter Ausgang wird erst geklärt und nicht als
   „nichts übernommen“ beschriftet. Commitfehler lässt alle Teilstände unverändert.
7. Weiterführung erzeugt einen neuen Entwurf. Originalabschluss und neuer
   Entwurf samt neuem Plan/Herkunftsverweis werden atomar gespeichert.
8. Kein automatisches Löschen alter Entwürfe. Nur offene zählen zur 100er-Grenze.

## Daten und API

Die konkreten Feldnamen und Endpunkte stehen in UX-A1 §3/§4. Benötigt werden
result_revision/checksum, genauer damaliger Entwurfs-/Planstand, beidseitige
Weiterführungsverweise und persistierte Vergleichsentscheidungen mit Digests.
Eine Aktivierungsrevision enthält künftig selbst den Verweis auf den erzeugenden
Entwurf, dessen Version und Übernahmebeleg. Laufzeit-Nachweise werden dafür
nicht in ein portables Konfigurationspaket übernommen.

Kompatibilität: Authoring-Protokoll 2 für die neue Bestätigungs-
und Belegsemantik. Alte schreibende Clients werden sichtbar zum Neuladen
aufgefordert; kein unsicherer Legacy-Schreibpfad ohne F1/F2. Bestehende lesende
Consumer-Schnittstellen und englische Vertragswerte bleiben unverändert.
Benni hat diese Änderung mit dem A2-b3-Go bestätigt. Der Header
`X-Authoring-Protocol: 2` ist für die betroffenen Authoring-Schreibwege Pflicht.

Gespeicherte Eingabeversionen liegen unveränderlich in `registry_draft_version`.
Beim gleichzeitigen Bearbeiten desselben Entwurfs vergleicht der Server die
zuletzt gelesene gespeicherte Version, die lokale Eingabe und die neue
gespeicherte Version. `reconcile` ist nur eine Vorschau; der folgende Save
braucht weiterhin die geprüfte aktuelle `draft_version`. Er setzt niemals die
aktive Grundlage neu. Fehlende historische Eingabeversion wird nicht geraten.

Mutation eines erledigten Entwurfs ist gesperrt. Wiederöffnen ist lesend;
„Bearbeiten“ beginnt auf dem aktiven Stand oder mit ausdrücklich ausgewähltem
geeignetem offenen Entwurf. Neue Konfiguration während lokaler Eingaben:
bekannte Eingaben sichern, Grundlage als veraltet anzeigen, Weiterführung
verlangen. Das Erhalten begonnener Eingaben setzt keine neue Grundlage.

Ein bestätigter Abschluss bereits wirksamer Änderungen erzeugt keine weitere
Registryrevision, muss jedoch alle noch offenen Planbestandteile einbeziehen.
Er verweist auf den aktuell belegten Stand und behauptet keine historische
Übernahmezuordnung. Historischer Entwurfsexport liest den tatsächlich
abgeschlossenen Stand; ein consumed-Plan wird niemals erneut ausgeführt.

## Zusammenführung

Drei konsistente Stände: nachgewiesene Basis, Entwurf, aktive Konfiguration.
Vergleich nach Objektart/ID und fachlicher Eigenschaft. Mengenreihenfolge ist
irrelevant, fachlich geordnete Folgen bleiben relevant. Fehlend, null und
beabsichtigtes Löschen sind unterscheidbar. Unterschiedliche Änderungen am
selben Pfad, Löschen gegen Benötigen, kollidierende neue Identitäten und
geschützte Vorlagenversionen erhalten eigene, nicht vorausgewählte Entscheidungen.

Import-Weiterführung umfasst Registry, beabsichtigte Entfernungen, Kataloge und
Editorial. Ein neuer Plan wird gegen aktuelle Heads/Versionen vollständig neu
geprüft; nur nachgewiesen verbrauchte Anteile entfallen aus dem ausführbaren Plan.
Vorgesehene Ersatzimport-Entfernungen bleiben erhalten; seit Basis neu entstandene
aktive Objekte verschwinden nicht ohne bewusste Entscheidung. Erneuter aktiver
Wechsel erlaubt einmal automatische Neubewertung; bei weiterem Wechsel Halt.
Die Begrenzung und Auswahl überleben Reload. Abbruch übernimmt nichts.

## Altbestand und Migration

Nur beweisbare damalige Entwurf-/Versions-/Planzuordnung erlaubt automatischen
Abschluss. Der aktuelle Auditpfad garantiert diese Zuordnung nicht für alle
Altbestände. Gleichheit von Inhalten, Zeitnähe und Name „Import“ reichen nicht.

Der Referenzfall enthält zwei Änderungen nach einem früheren Import. Liegen
exakter übernommener Stand und Planverbrauch vollständig belegt vor, werden
nur diese späteren Änderungen weitergeführt. Sonst bleiben Original und beide
Änderungen unter needs_review erhalten; geführter Vergleich mit expliziter
heutiger Änderungsabsicht. Kein stilles Archivieren, Löschen oder erneuter Import.
Spätere Migration erfolgt wiederanlauffähig über die App, ohne direkte DB-Reparatur.

Der freigegebene zusätzliche Belegpfad nutzt den Digest des gestagten
`portable.config`, dessen kanonische Prüfsumme in einer nach dem Staging
aktivierten Registryrevision und zusätzlich die nachgewiesenen Editorial- und
Katalogwirkungen des Pakets. Nur zusammen erlauben diese Belege Basis B und
Planverbrauch; siehe [A1 §5.1](../beta/ux/A1.md#51-verbindliche-ergänzung-für-a2-b3-benni-22102026).

## Alternativen und Folgen

- Nur im Frontend ausblenden: schützt API und alte Fenster nicht; abgelehnt.
- Bei activate immer den aktuellen expected_active_revision senden: bewahrt
  genau die ungesicherte Vollersetzung; abgelehnt.
- Alte Pläne löschen oder base_revision hochsetzen: verliert Herkunft oder
  umgeht noch offene Katalog-/Editorialkonflikte; abgelehnt.
- Entwurf nach Commit in separatem Schritt erledigen: Antwortverlust erzeugt
  wieder einen offenen ausführbaren Plan; abgelehnt.

Die Entscheidung benötigt persistente Metadaten/Belege und eine Migration.
Er verhindert Datenverlust und Wiederholung, erhöht aber den Authoring-Vertrag.
Beleg-/Provenienzreferenzen dürfen nicht vor den zugehörigen historischen
Konfigurationen gelöscht werden. Eine spätere Aufräumregel ist ein eigener
expliziter Vertrag, kein implizites Löschen in diesem Paket.

## Bedienung am Objekt (b22, P3)

Der globale Schalter „Ansehen | Bearbeiten“ entfällt gemäß #97. Ein Objekt öffnet
oder wählt seinen Entwurf über die vorhandene `ensureDraft`-Semantik. Die neue
Bedienung umgeht weder Basisprüfung noch Speicherkonkurrenz oder Bestätigungen.
„Änderungen übernehmen“ bleibt der ausdrücklich bestätigte Übergang zum aktiven
Stand. Umbenennen und Aktivierungsschalter schreiben zunächst in den Entwurf;
Weckplanung bearbeitet ausschließlich ihren Parameter. Die Entitätsreparatur
ändert nur die Adresse der vorhandenen Verknüpfung und erhält logische IDs.

## Tests und Gates

Prüfumfang: UX-1-01…09, UX-2-01…12, UX-5-09…12; Commit-Fehler,
Antwortverlust nach Commit, Restart/Wiederholung, zwei Fenster, geänderte
Katalog-/Editorialstände, fehlende Basis, Ersatzimport-Entfernungen und zwei
spätere Änderungen im Altimport. Ausschließlich synthetische Daten.

Schnitt: b3. Umsetzung und konkrete Nachweise stehen in
[A2-b3](../beta/ux/A2-b3.md). Nach dem PR und seinen Prüfungen gilt STOPP:
kein Merge, Tag, Release oder Installationsschritt ohne neues Go.
