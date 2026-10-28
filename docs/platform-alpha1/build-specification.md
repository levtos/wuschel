# Core Contracts — Platform Alpha 1 Build Specification

**Phase 1 · Platform Foundation** · Stand 2026-10-07
**Ersetzt:** `Core_Contracts_Alpha1_Build_Specification.md` (SUPERSEDED: vermischte Phase 1 und Phase 2)
**Zielrepository:** `Levtos/core_contract_app`

**Freigegebene Fortschreibung 17.10.2026 (Alpha 8):** Benni präzisiert
die HA-Ausfall-Semantik gemäß Akte P4-65 §7/§13; siehe
[ADR 0005](../adr/0005-ha-outage-connectivity.md). Die frühere pauschale
HA-Invalidierung in §13 ist damit ersetzt. A1 wurde mit Auflagen freigegeben,
A2 ist implementiert und endet vor Teil B für Review.
[Alpha-8-Plattformformate](../alpha8/platform.md) schreiben außerdem die früheren
test.*-/Fixture-/Scope-Sperren, Instanzparameter, Uhr/Kalender, n-Klassen,
Textregeln, Lebenszyklus/Formeleingänge und Vollständigkeit fort. Für
[Messwert-Aufbewahrung und Skalierung](../alpha8/retention-scaling.md) gilt
ein explizites Produktionsgate. Alle übrigen Phase-1-Regeln bleiben historische
Basis; die neuen Freigaben sind kein Domain-/Consumer-/Live-Auftrag.

**Verbindliche Ergänzung 11.10.2026:** [ADR 0002](../adr/0002-phase1a-producer-and-ownership.md)
setzt E1–E4 und die generischen Phase-1A-Korrekturen um. Nachweismatrix:
[Phase-1A-Fertigstellung](phase1a-completion.md). Kein Phase-2-/Live-Auftrag.

> **Alpha 1 baut ausschließlich das generische Core-Contracts-Plattformfundament: Framework,
> Infrastruktur, Registry, Persistenz, Runtime, Bridge und API. Alpha 1 baut keine produktiven
> fachlichen Contracts und migriert keine Consumer.** Bewiesen wird die Plattform mit synthetischen
> Testcontracts (`test.*`).

## 0. Autorität, Phasen, Leseregeln

### 0.1 Quellen
1. **Primäre Build-Quelle:** dieses Dokument.
2. **Fachliche Rückfallquelle:** `Core_Contracts_Master_Entscheidungsakte_Phase4_Auditrevision.md`
   (Stand P4-65, lokal und privat; nicht im Repository) plus die bestätigten Deltas:
   keine Core-Profile, `installation_id`, kein Shadow-Modus, D1–D3, technische Baseline,
   Thin HA I/O Bridge. Verweise wie `M30-01` beziehen sich auf die Fachakte.
3. **Für Phase 1 wird die Fachakte als Capability-Anforderung gelesen.** Sie beschreibt, welche
   Wahrheitsarten, Quality-Zustände, Evidence-, Temporal-, Restore-, Fusion-, Resolver-,
   State-Machine-, Registry- und API-Fähigkeiten die Plattform tragen können muss. Sie ist **kein**
   Auftrag, die dort beschriebenen fachlichen Contracts in Phase 1 zu programmieren.
   Beispiel: Zeigt die Fachakte, dass ein Contract eine Grace braucht, baut Phase 1 den generischen
   Grace-Mechanismus. Die Grace-Regeln eines konkreten Contracts gehören zu Phase 2.
4. Superseded und nicht als Build-Anweisung zu verwenden: `Core_Contracts_Alpha1_Build_Specification.md`,
   `Core_Contracts_Alpha1_Codex_Build_Prompt.md`, ältere Master-Fassungen.

### 0.2 Phasen

| Phase | Inhalt | Beginn |
|---|---|---|
| **Phase 1 — Platform Foundation (Alpha 1)** | generische Plattform; synthetische Testcontracts | jetzt |
| **Phase 2 — Domain Contracts + Consumer Migration** | reale Contracts einzeln je Domäne, jeweils mit Consumer-Umstellung | erst nach bestandenem Acceptance Gate (§27) |

Phase-2-Ablauf je Domäne (Information, nicht Teil von Alpha 1):
1. Contract aus der Fachakte ableiten.
2. Konkrete Sources und Bindings definieren.
3. Contract-Typ implementieren.
4. Contract isoliert testen.
5. Consumer bzw. Policy auf `CoreContractsClient` umstellen.
6. Rohdaten-Neuinterpretation aus der Policy entfernen.
7. Integration testen.
8. Erst dann die nächste Domäne angehen.

Nicht alle Domänen auf einmal.

### 0.3 Schlüsselwörter
**MUSS** = Pflicht. **SOLL** = Pflicht, sofern im Alpha-1-Rahmen möglich, sonst als Abweichung
berichten. **KANN** = optional. **DARF NICHT** = verboten.

---

## 1. Scope

Alpha 1 liefert eine **funktionsfähige, getestete und abnehmbare Plattform**, auf der Phase 2 reale
Contracts bauen kann:

- Supervisor-verwaltete Home-Assistant-App (eigener Container, außerhalb von HA Core) als echter Produktcode.
- **Thin HA I/O Bridge** (HA-Custom-Integration, reiner Transport).
- **Core Registry** mit stabilen Source-, Binding- und Contract-Instanz-IDs, Revisionen, Validierung,
  Aktivierung, Rollback.
- Generische **Source-Adapter** (HA State, HA Attribut, HA Report/Heartbeat, MQTT).
- Generisches **Evidence-**, **Quality-** und **Reason-Modell**.
- Generische Frameworks für **Resolver**, **Fusion**, **State Machine**, **Temporal** und
  **Scheduler/Clock** sowie eine **Contract-Typ-Registrierung**.
- **PostgreSQL-Persistenz**, Restore-Framework, Writer-Lock, Commit-before-publish, Migrationen.
- **Consumer API** (HTTP/JSON + WebSocket) und **`CoreContractsClient`**.
- **Synthetische Testcontracts** (`test.*`), die den vollständigen Plattformpfad beweisen.
- Minimale **Engineering-/Admin-UI** über Ingress.
- Tests, Packaging (amd64/aarch64), Betriebsdokumentation.

## 2. Ziele

Alpha 1 beantwortet:
**Kann die Plattform zuverlässig Quellen aufnehmen, über stabile IDs binden, generische
Contract-Mechanismen ausführen, persistieren, restaurieren und über eine saubere Consumer-Grenze
bereitstellen?**

Alpha 1 beantwortet ausdrücklich **nicht**, wie ein konkreter fachlicher Contract funktioniert.

## 3. Nicht-Ziele

### 3.1 Keine produktive Fachlogik (Phase 2)
Alpha 1 enthält keine Contract-Typen, Zustandsautomaten, Parameter, Mappings, Prioritäten oder Regeln
für reale Domänen. Insbesondere **nicht**:

- Öffnungen (Fenster/Türen)
- Anwesenheit
- Schlaf/Bio
- Aktivität/Gaming
- Medien
- Klima/Heizung/Komfort
- Beschattung/Safety
- Licht
- Tagesphase
- Weckplanung
- Simulation
- Außenhelligkeit
- Plug-Policy
- Door-Policy

Das gilt auch dort, wo die Fachakte diese Semantik bereits vollständig beschreibt. Es gibt keine
Consumer- oder Policy-Migration. Bestehende Integrationen (`blind_control`, Plug Policy, Door Policy,
Climate, Media, Light) bleiben unverändert.

### 3.2 Technische Nicht-Ziele
Microservices · Kubernetes · Redis · Kafka · RabbitMQ · Cluster · Multi-Writer · Multi-Tenant-Core ·
**Core-Profile** · Cross-HA-Federation · **Shadow-Modus** · Umbrella-UX · Drag-and-Drop-Workbench ·
visuelle Regel-/SM-Editoren · generisches Plugin-System für Dritte · generisches `set_state` ·
produktive Domain-Policy oder Apply-Hosting in Phase 1A · HA-Entity-Projektionen · MQTT als Contract-Bus · freie
Ausdruckssprache (Jinja/Python/eval) · vollständige Legacy-Datenmigration.

## 4. Terminologie

| Begriff | Bedeutung |
|---|---|
| **Installation** | eine HA-Installation mit genau einer App, eigener DB und eigener Registry; vollständig isolierter Core-Kontext |
| **`installation_id`** | stabile UUID der Installation; einzige Isolationsidentität (§11) |
| **`installation_label`** | reine Anzeige, nie Logik oder ID |
| **Source** | stabile, fachlich benannte Quellidentität (`source_id`, z. B. `source.living_room.terrace_door`), unabhängig von der konkreten Entity |
| **Adapter** | technische Anbindung einer Source (HA State, HA Attribut, MQTT, Scheduler) |
| **Binding** | Zuordnung einer Source zu einer konkreten physischen Quelle (Adapter + Entity/Attribut/Topic) und zu einem Input einer Contract-Instanz |
| **Observation** | einzelner Eingang mit allen Zeitstempeln und Herkunft (§8) |
| **Evidence** | Observation(s), die ein Contract-Ergebnis tragen, mit Referenz |
| **Contract-Typ** | im Code registrierte, versionierte Implementierung (Schema + Producer), z. B. `test.echo` |
| **Contract-Instanz** | konfigurierte Ausprägung eines Contract-Typs mit `contract_id`, Bindings, Parametern, `enabled` |
| **Producer** | genau ein autoritativer Berechnungspfad je Instanz: Resolver, Fusion oder State Machine |
| **Status** | `valid`, `held`, `unknown`, `not_applicable`, `unresolved` |
| **Reason** | maschinenlesbarer Grund aus zentralem Katalog |
| **Registry-Revision** | unveränderliche Gesamtkonfiguration einer Installation |
| **Draft** | bearbeitbarer Entwurf, wird nie ausgewertet |
| **Publication** | atomar committeter und veröffentlichter Berechnungsstand (`publication_seq`) |
| **Runtime-Epoche** | Abschnitt ununterbrochener autoritativer Fortschreibung |

## 5. Ownership

| Ebene | Verantwortung |
|---|---|
| **Core Contracts (Plattform + später Contract-Typen)** | kanonische beobachtete und deterministisch abgeleitete Zustände einschließlich fachlicher Zielwerte, mit Status, Quality, Freshness, Evidence, Reasons |
| **Spätere Module** | Contracts konsumieren, reale Geräteaktionen ausführen, Feedback als Sources zurückmelden |
| **Apply/Controller** | Aktorik ausschließlich außerhalb Core |
| **Bridge** | HA-I/O-Transport, keine Wahrheit |
| **CoreContractsClient** | Zugriff, keine Wahrheit, keine Neuinterpretation |
| **Admin-UI** | Anzeige/Administration über öffentliche API |

Keine konkurrierende Wahrheit in Bridge, Client, API, MQTT, Frontend oder Projektionen.
Policy-Profile bleiben Policy-Konfiguration und sind keine Core-Dimension. Die Plattform führt keine
Apply-Aktionen aus.

## 6. Contract-Framework

### 6.1 Contract-Typ-Registrierung
- Contract-Typen sind **code-definiert**, versioniert und werden in einer internen Typ-Registry
  registriert (`type_id`, `type_version`, Schema, Producer-Art, Parameter-Schema, Input-Deklaration,
  Restore-Deklaration). Das ist **kein** Plugin-System für Dritte, sondern ein Paket-interner
  Registrierungsmechanismus.
- In Alpha 1 sind **nur** die synthetischen Typen aus §6.8 registriert.
- Der interne Registrar bleibt für weitere code-definierte versionierte Typen erweiterbar;
  die App-Ladestelle und G13 erzwingen den Phase-1A-Katalog. Kein zusätzlicher `core.*`-Katalog.
- Contract-Instanzen werden in der Registry konfiguriert (§10) und referenzieren `type_id`/`type_version`.

### 6.2 Contract-Identität
- `contract_id` ist installationslokal, stabil und bedeutungsbezogen. Es gibt keinen Profil- und keinen
  Installations-Präfix.
- Werte nutzen bei Bedarf hierarchische Punktnotation `<state>[.<specialization>]` (M07-03). Es gibt
  **keine** universelle Variant-Mechanik.
- Value-Catalog im Schema, keine Value-UUIDs (M07-04). Anzeige-Metadaten sind von der Identität getrennt (M07-05).

### 6.3 Schema-Framework
Schema = `fields{name: type (bool|number|enum|text|timestamp|object), required, physical_state,
value_catalog?, unit?}`, `available_projection?`, `grace_capable` + Parametername. Regeln (generisch):
- Versionen sind unveränderlich, ohne automatische Migration (M30-17).
- `physical_state` erzwingt `reject` ohne Ersatzwert.
- Es gibt keinen `safe_default` und kein `hold_last`. Halten erfolgt nur über deklarierte Grace.
- „Nicht konfiguriert“ ist Konfigurationsvollständigkeit, nie `not_applicable`.

### 6.4 Producer-Regeln (generisch, M30-08)
- Transformationen erzeugen interne Kandidaten, keine Contracts.
- Ein Resolver mit einem Berechnungsweg darf direkt publizieren. Mehrere Wege liefern Kandidaten,
  und genau eine Fusion publiziert.
- Eine State Machine ist direkter Producer. Hinter einer SM gibt es keine Fusion.
- **Single-Producer je Instanz** (Validierung, §10.3). Der Graph ist azyklisch (Validierung).
- Guards und Inputs referenzieren Contracts bzw. Sources über stabile IDs, nie Entity-IDs (M30-09).

### 6.5 Resolver-Framework
- Interface: typisierte Inputs (aus Bindings oder anderen Contracts) → Ergebnis mit Status, Wert,
  Quality, Reasons, Evidence-Referenzen. Registrierung als Teil eines Contract-Typs. Lifecycle:
  `init` → `evaluate(inputs, ctx)` → `dispose`.
- Fehlerbehandlung: Eine Exception im Resolver wird gefangen und ergibt `unknown` mit Reason
  `evaluation_error`. Der Prozess läuft weiter, und die Diagnose wird geloggt.
- **Generische Operatoren** (wiederverwendbare Knoten, ohne Domain-Parameter): `compare`,
  dreiwertiges `and`/`or`/`not` (`unknown` ≠ `false`), `first_match` mit **strenger Auswahl**
  (M30-04: unbekannter entscheidender Pflicht-Eingang eines höheren Falls → `unresolved` +
  Reason), `map` (ungemappt → `unmapped_value`), `bucket` mit optionaler Hysterese
  (Pflicht-`initial`, ungültiger Eingang → `unknown` + Latch-Reset; einziger zustandsbehafteter
  Operator, M30-10/11/12), `formula` (code-definierter, versionierter Katalog; in Alpha 1 nur
  Testformeln).
- Keine freie Ausdruckssprache, kein verstecktes Zeitgedächtnis, keine Ad-hoc-Timer.

### 6.6 Fusion-Framework
- Kandidaten mit Status, Quality, Messzeit und Evidence werden aufgenommen. Strategien:
  `first_healthy`, `latest` (nach Messzeit), `any_true`, `all_true` (dreiwertig; fehlend oder
  unbekannt ≠ `false`), auch verschachtelt (M09-01/02).
- Ein nicht auflösbarer Konflikt derselben Wahrheit ergibt `unknown` + `degraded` + Reason `conflict`
  (M30-02). Solange tragfähige Kandidaten bleiben, wird degradiert statt `unknown`.
- Ein Erweiterungspunkt für **contractspezifische Fusionsstrategien** (code-definiert, je Contract-Typ)
  wird bereitgestellt und mit einer synthetischen Teststrategie bewiesen. Reale Strategien und
  Quellenprioritäten gehören zu Phase 2.

### 6.7 State-Machine-Framework
- Deklarative SM-Definition je Contract-Typ: Zustände, Initialzustand-Regel, Transitionen mit Guards,
  auslösende Inputs/Events, Commands (je SM explizit), Deadlines (absolute UTC, relativ zu Episodenanker),
  Episodenregeln, optionale Sessions.
- Laufzeit führt: aktueller Zustand, `since_at`, `previous_state`, `episode_id`, `transition_reason`,
  `trigger/source`, Evidence-Referenzen, `registry_revision`, `origin` (`automatic|manual|restore`).
  Diese Felder werden pro Übergang persistiert (M11-11).
- **Commands fordern Transitionen an.** Guards entscheiden. Abgelehnte Anforderungen werden mit Reason
  protokolliert (M11-04, M13-01). Es gibt kein generisches `set_state`.
- Ein Event muss keine Transition auslösen. Dasselbe Signal darf je Zustand unterschiedlich wirken.
- Episodenende und Neubeginn sind getrennte Episoden (M11-10). Es gibt keine Session-Pflicht.
- Persistenz und Restore gemäß §9.
- In Alpha 1 existiert nur die synthetische SM `test.state_machine`.

### 6.8 Synthetische Testcontracts (einzige Contract-Typen in Alpha 1)

Alle liegen in `src/core_contracts/testing/contract_types/`, tragen den Namespace `test.*`, sind im
Schema als `fixture: true` markiert und werden in der Admin-UI als „TEST“ gekennzeichnet. Sie sind
**nicht** Teil der fachlichen Contract-Landschaft und enthalten **keine** Home-Automation-Semantik.

| Typ | Zweck (Plattformpfad) |
|---|---|
| `test.echo` | Source → Binding → Evidence → Resolver (Durchreichen) → Persistenz → Publication → Client. Beweist Evidence-Zeitstempel, Freshness-Modi, `unknown`/`input_*`-Reasons. |
| `test.boolean` | Resolver-Operatoren (`compare`, `and/or/not` dreiwertig, `bucket` mit Hysterese, `map`, `first_match` streng → `unresolved`), `not_applicable` bei positiver Feststellung über einen Test-Input. |
| `test.fusion` | Fusion-Strategien, Konflikt, Teil-Evidence, Erweiterungspunkt mit synthetischer Teststrategie. |
| `test.temporal` | `stable_for`/`dwell`, `grace` mit Parameter, Held-Propagation (frühestes Grace-Ende), `delta`, `age`, Edge-Baseline. |
| `test.state_machine` | abstrakte Zustände `a`, `b`, `c`; Guards; Command `request_b`; Deadline; Episoden; Session; Restore. |
| `test.multi` | unabhängige benannte Number-/Bool-/Enum-Felder; Pflichtfeld-Health und available. |

`test.state_machine` v2 ergänzt streng typisiertes `request_b_if {confirm: bool}`;
v1 bleibt parameterlos kompatibel. `test.echo` v2 deklariert HA-request_refresh,
einmal pro negativer Evidence-Phase. Generische Orchestrierung liegt in `producer.py`,
nicht in den Fixture-Definitionen; staged Lifecycle-Hooks gemäß ADR 0002.

Werte und Parameter dieser Typen sind abstrakt (z. B. Zustände `a/b/c`, Schwellen nur im Test gesetzt).

## 7. Quality-Modell (generisch, Frameworkregeln)

### 7.1 Status (M30-01)

| Status | Wert | Quality |
|---|---|---|
| `valid` | gesetzt | `healthy` (oder `degraded` bei Teil-Evidence) |
| `held` | gesetzt + `held_until` | `healthy`, Reason sichtbar; nur innerhalb deklarierter Grace |
| `not_applicable` | `null` | `healthy`; nur bei positiver Feststellung |
| `unknown` | `null` | `degraded`; **immer** mit Reason + betroffenem Input |
| `unresolved` | `null` | `degraded`; Auswahl ohne eindeutigen Gewinner |

Das Framework MUSS diese Invarianten erzwingen (Laufzeitprüfung + Tests):
- `unknown` ohne Reason ist unmöglich.
- `held` ohne `held_until` und ohne deklarierte Grace ist unmöglich.
- `unknown → false`, Ersatzwerte, Ersatz-Fachwerte, `restored` als Status oder Quality, Connectivity
  als Quality und „persistiert = valid“ sind verboten.

### 7.2 Held-Propagation (A-04, Frameworkregel)
Ein Ergebnis, das entscheidend auf `held`-Eingängen beruht, ist `held` mit dem frühesten `held_until`
der ergebnisbestimmenden Eingänge. Eine Auswertung startet oder verlängert keine Grace, und es gibt keine
Grace-Ketten. Temporal-Bedingungen deklarieren `accepts_held` (Default `false`). Ist die Grundlage
abgelaufen, wird neu bewertet.

### 7.3 Aggregation
Status, Quality und Reasons gelten je Feld. `contract_health` wird nur aus Pflichtfeldern gebildet.
`available` ist eine Quality-Projektion der deklarierten Felder: nie `unknown`, kein Fallback (M08-14).

### 7.4 Reason-Katalog (generisch)
`input_unavailable`, `input_unknown`, `input_stale`, `input_restored`, `input_absent`, `unmapped_value`,
`invalid_value`, `no_match`, `conflict`, `partial_evidence`, `source_transition_grace`,
`selection_blocked`, `dependency_unavailable`, `config_incomplete`, `evaluation_error`,
`restore_context_missing`, `restore_context_incompatible`, `restore_context_stale`, `history_gap`,
`deadline_overdue_unprocessed`, `guard_rejected`.

Struktur: `{code, input, source_id?, since, detail?}`. Es gibt keine gerätespezifischen Codes.
Phase 2 darf den Katalog generisch erweitern.

## 8. Evidence und Freshness (generisch)

### 8.1 Observation
```text
source_id, binding_id, adapter (ha_state|ha_attribute|mqtt|scheduler|command),
source_origin ("local" in Alpha 1; reserviert für spätere Installationsherkunft),
value_raw, value_normalized (nur wenn generische Binding-Transformation definiert),
device_time, ha_last_changed, ha_last_updated, ha_last_reported, ha_time_fired, ha_context_id,
mqtt_retained, mqtt_qos, received_at (UTC), observation_kind (live_change|report|snapshot|restore),
ha_restored, assumed_state, epoch_id, ingest_seq
```

### 8.2 Neue Beobachtung (Frameworkregel)
- **Neu:** echter Wert- oder Attributwechsel; echter Report/Heartbeat (`state_reported`, neuer
  Gerätezeitstempel, neue MQTT-Nachricht mit neuer Quellzeit).
- **Nicht neu:** Reconnect, Restore, Snapshot ohne neuere Quellzeitstempel, Re-Evaluation,
  Re-Publication, MQTT-Retain allein, QoS-Duplikat, neuer HA-`last_updated` allein.
- Der Empfangszeitpunkt ist nie Frische. Eine fehlende Messzeit wird nicht durch „jetzt“ ersetzt.

### 8.3 Freshness-Modi je Source (explizit, ohne Default)
`report_heartbeat` (erwartetes Intervall), `liveness_source` (separate Liveness-Source), `periodic_ttl`,
`event_stateful` (Alter ist kein Mangel). Harte Gates: restaurierter Wert, Retain, fehlende Messzeit,
Zeitstempel in der Zukunft über Toleranz. Alle Zahlenwerte sind Konfiguration.

### 8.4 Ein Eingangspfad
`physical_source_key` je Binding. Ist dieselbe physische Quelle über HA **und** MQTT als unabhängige
Evidence gebunden, wird sie bei der Validierung abgewiesen.

## 9. Temporal-, Scheduler- und Restore-Framework

### 9.1 Werkzeuge (alle persistierbar, testbar, restartfähig)

| Werkzeug | Frameworkregel | Restore |
|---|---|---|
| Deadline | absolute UTC, Episodenanker-relativ | Fälligkeit bleibt; Überfälligkeit erkannt und nach Regel ausgewertet; kein Neustart; unbestimmbarer Verarbeitungsstand → `deadline_overdue_unprocessed` |
| `since_at` / Episodenanker | persistiert | weiterverwendbar, wenn belastbar |
| `stable_for` / `dwell` | Kontinuitätsnachweis | eine unüberbrückte Lücke entwertet den Anker (M30-14); Ausnahme §9.3 |
| `grace` | nur deklariert; erzeugt `held` | `grace_until` persistiert; höchstens Restzeit bis zur ursprünglichen Frist; nie neue Grace |
| Hysterese-Latch (`bucket`) | Pflicht-`initial` | **nicht** persistiert; nach Restart `initial` (M30-12, M08-08); über Revision nur bei gleichem Fingerprint |
| Edge-Baseline | `unknown` überschreibt sie nicht; Lückenzeitpunkt unbekannt | persistiert mit Fingerprint; gleicher Fingerprint → weiterverwendbar, sonst frische Basis (M30-03, A-06) |
| SM-Zustand/Episode/Session | – | validieren, weiterverwenden, wenn belastbar |
| Commands | – | nie wiederholt |
| monotone Werte | nur Laufzeit | **nie persistiert, nie restauriert** |

### 9.2 Semantik-Fingerprint
Deterministischer Hash über eigene Definition, Typversion, Parameter und direkte
Eingangsschnittstellen, ohne Anzeigenamen. Die Vertragsschnittstelle umfasst die
referenzierte Felddefinition, nicht die gesamte vorgelagerte Ableitung. Physische
Quellenidentität und Transformation behalten ihre strenge Neuaufnahmegrenze.
Eingangsabhängige Verlaufskontexte werden zusätzlich je Speicherplatz geprüft:
Eine geänderte Ableitung setzt nur die davon abhängige Basis zurück, nicht die
Episode eines kompatiblen Ablaufs. Siehe [Abschluss-A1 §2](../beta/abschluss/A1.md)
und [ADR 0030](../adr/0030-freshness-templates-evidence.md).

### 9.3 Belegbare Kontinuität (technische Auslegung von M30-14)
Ein `since_at`-Anker bleibt über eine Lücke nur gültig, wenn:
(a) die Bedingung `held` akzeptiert und die Lücke innerhalb deklarierter Grace liegt, oder
(b) jede ergebnisbestimmende Source nach der Lücke eine quellseitige Wertänderungszeit vor dem Anker
liefert, bei unverändertem Wert und gültiger Freshness.
Sonst ist der Anker ungültig. Das ist kein Reset von Episoden oder Deadlines. **Review-Punkt.**

### 9.4 Restore-Ablauf (CP-15)
1. **Startfall bestimmen:** Erststart (Instanz nie aktiv), gültiger, veralteter,
   revisionsinkompatibler, teilweiser oder fehlender Kontext. Fehlender Kontext beweist keinen Erststart.
2. **Validieren:** Fingerprint, Vollständigkeit, Deadline-/Grace-/Episoden-Zuordnung, Abhängigkeiten.
3. **Aktuelle Evidence einlesen:** Snapshot, MQTT, Clock.
4. **Neu bewerten** nach Bausteinregel: keine Flanke, keine Grace, kein neuer Beginn, keine erfundene
   Kontinuität und keine rekonstruierten Zwischenereignisse.
5. **Unbestimmbares** wird `unknown` mit Reason.
6. **Lücke** als `history_gap` speichern.
7. **Veröffentlichen:** konsistent in einer Publication.

### 9.5 Revisionsaktivierung (M30-30)
Einmalige sofortige Neuauswertung vorhandener verwendbarer Evidence unter der neuen Revision. Dabei gibt
es keine künstliche Flanke, keine Wiederholung alter Ereignisse, keine Zeitstempel-Auffrischung und keine
neue Grace. Baselines und Latches folgen der Prüfung je Speicherplatz; unveränderte
Eingänge behalten ihre unabhängige Basis. Kompatible Abläufe, Captures, Episoden
und absolute Fristen bleiben erhalten, während reguläre Übergänge mit aktuellen
Werten weiter gelten, etwa das Ende von Besuch/Date bei Schlaf. Die Vorschau
nennt tatsächlich verworfene Kontexte einschließlich flüchtiger Latches über
dieselbe Logik wie die Aktivierung. Der Abhängigkeitsbereich wird als ein
konsistenter Stand veröffentlicht, ohne Mischstand. Ein erster Start mit b15-
Kontexten beweist deren alten Fingerabdruck gegen exakt ihre aktive Revision,
bevor die Darstellung auf den neuen Vergleich überführt wird; CP-15 bleibt gültig.

### 9.6 Scheduler/Clock
Einziger Zeitzugang (§18). Er plant Deadlines, Grace-Enden und zeitbasierte Auswertungen und speist sie
als Events in den Processor.

## 10. Core Registry (zentrale Phase-1-Anforderung)

### 10.1 Zweck
Fach- und Plattformcode arbeitet mit **stabilen kanonischen IDs**. Konkrete HA-Entity-IDs, Attribute oder
MQTT-Topics stehen **ausschließlich** in Bindings. Kein Code außerhalb der Adapter-Schicht kennt
Entity-IDs.

### 10.2 Konfigurationsschema (`config_schema_version = 1` im neuen Repository)
```text
RegistryConfig {
  config_schema_version
  dependencies: [ {dependency_id, kind (ha|mqtt|other), monitor (internal|source)} ]
  sources:   [ {source_id, source_type (state|attribute|event|numeric|text|…), source_origin ("local"),
                freshness {mode, …}, dependency_id, physical_source_key, display_name} ]
  bindings:  [ {binding_id, source_id,
                adapter {kind: ha_state|ha_attribute|mqtt|scheduler,
                         ha_entity_id?, ha_attribute?, mqtt_topic?, mqtt_value_path?, mqtt_time_path?},
                transform? {generisch: type_cast|unit_scale|map_ref}, display_name} ]
  contracts: [ {contract_id, type_id, type_version, enabled,
                inputs: [ {name, ref: source_id | contract_id(.field)} ],
                parameters {…}, display_name, description} ]
  catalogs:  [ {catalog_id, kind: map, version, entries} ]
}
```
Für `monitor=source` ist zusätzlich `source_id` verpflichtend: gesundes, verwendbares
Bool `true` belegt Verfügbarkeit; andernfalls `dependency_unavailable`. Source-Monitor-
Zyklen/ungebundene Monitore und interne `other`-Monitore werden abgelehnt. Der als
Quelladapter reservierte Wert `scheduler` wird bis zu einer echten Implementierung
explizit abgewiesen; Clock-/Temporal-/Deadline-Scheduling wird dadurch nicht beschränkt.
**Verboten** (Validierung lehnt ab): `profile`, `profile_id`, `consumer_ids`, Shadow-/Published-Modi,
`safe_default`, `hold_last`, freie Ausdrücke, Secrets.

### 10.3 Revisionen, Drafts, Validierung, Aktivierung, Rollback
- Die aktive Registry liegt in PostgreSQL. Revisionen sind unveränderlich, fortlaufend und mit SHA-256
  über die kanonische Serialisierung versehen. Es gibt genau eine aktive Revision.
- Drafts sind persistiert, haben OCC (`draft_version`) und werden **nie ausgewertet**.
- Import (Datei/JSON) erzeugt nur einen Draft. Export enthält keine Runtime-Zustände und keine Secrets.
- Validierung (auch als Trockenlauf mit Diff):
  - Schema- und Typprüfung, Referenzen, azyklischer Graph, Single-Producer.
  - Pflicht-Parameter vollständig (kein Code-Default, sonst `config_incomplete`).
  - `enabled`-Konsistenz (aktive Instanz ohne deaktivierten Pflicht-Input).
  - Doppelpfad HA/MQTT, verbotene Felder, geheimnisverdächtige Schlüssel.
- Aktivierung ist explizit, validiert, nutzt `expected_active_revision` (OCC) und ist atomar. Ein Fehler
  lässt die aktive Revision unverändert.
- Rollback ist eine neue Aktivierung des früheren Inhalts unter **neuer** Revisionsnummer. Die Historie
  bleibt erhalten.
- Die Runtime verändert die Registry nie. Ohne aktive Revision ist der Service nicht ready.
- `source_id` ist fest. Die Adapter-Zieladresse eines Bindings darf ausgetauscht werden (Reparatur).
  Es gibt keinen stillen Quellenwechsel.

### 10.4 `enabled` — kein Shadow-Modus
Die Semantik wird auf Framework- und Registry-Ebene gebaut und mit Testcontracts geprüft:
- aktive Revision + `enabled=true` → ausgewertet, persistiert, publiziert (live);
- `enabled=false` → nicht ausgewertet, nicht publiziert; die API meldet `contract_disabled`;
- ein Draft wird nie ausgewertet;
- Wiederaktivierung → Restore-Startfall „fehlender/veralteter Kontext“.

Es gibt keinen Shadow-Betrieb.

### 10.5 Spätere Cross-HA-Quellen
Sie sind nicht Teil von Alpha 1. `source_origin` ist im Modell reserviert. Eine spätere Fremdquelle wird
Source-Herkunft mit eigener Dependency und kein Profil. Die Architektur darf das nicht verbauen
(z. B. `source_origin` nicht hart auf `local` prüfen außer in der Validierung von Alpha 1).

## 11. Installation Identity und Isolation

- `installation_id` (UUIDv4) wird beim Erststart erzeugt und liegt in `/data/installation.json` sowie in
  `cc_meta`. Sie wird bei jedem Start geprüft und ist in `/api/v1/info`, im WS-Handshake, in Diagnostics,
  Backup-Metadaten und im Start-Log sichtbar. Sie ist **nicht** Teil von IDs oder Zeilen.

| `/data` | DB | Verhalten |
|---|---|---|
| leer | leer | neue ID, Erststart |
| X | X | Start |
| X | Y | Ablehnung `installation_mismatch` |
| X | leer | Ablehnung; nur mit `initialize_empty_database: true` |
| leer | Y | Ablehnung; nur mit `adopt_installation_id: Y` |

- Isolation über eine eigene App, eine eigene logische DB, eine eigene Rolle und eigene Tokens je
  Installation. Gleiche IDs dürfen in zwei Installationen existieren.
- **Kein Default-Kontext.** Kein „fehlt → `benni`“ in irgendeiner Form.

## 12. Persistenz (PostgreSQL)

### 12.1 Grundsätze
- PostgreSQL ist gesetzt. Bestehende Infrastruktur wird genutzt, mit eigener DB und Rolle je
  Installation. Der Standort ist Deployment-Konfiguration; eine WAN-Kopplung ist bewusst akzeptiert.
- `asyncpg` (Ausgangspunkt 0.32.x; Lockfile entscheidet), kein ORM, keine private API.
  TLS über `ssl=SSLContext`, CA konfigurierbar.
- Kleiner Lesepool und eine dedizierte Writer-Verbindung. Kein PgBouncer im Transaction-Modus.
- Fachliche Zeitstempel kommen aus der App-Clock, nie aus DB-`now()`.

### 12.2 Generische Tabellen (keine domänenspezifischen Tabellen)
`cc_meta`, `cc_schema_migrations`, `runtime_epoch`, `registry_revision`, `registry_activation`,
`registry_draft`, `contract_lifecycle`, `publication`, `contract_state_current`, `contract_state_history`
(Envelope als JSONB), `source_observation_current`, `source_observation_history` (nur Wert- oder
Availability-Wechsel), `node_state` (Temporal/Baseline/Grace/Session mit Fingerprint), `sm_instance`,
`sm_episode`, `sm_transition`, `deadline`, `command_log`, `history_gap`, `diagnostic_event`.

### 12.3 Commit-before-publish
Der Processor bildet je Schritt bzw. Mikro-Batch ein Changeset und schreibt es in **einer** Transaktion.
Erst nach dem Commit folgen In-Memory-Publikationsstand, Deltas und Command-Acks. Ein fehlgeschlagener
Commit führt zu keiner Publication.

### 12.4 DB-Ausfall
- Keine neue Publication, kein Write- oder Command-Ack.
- Die Quality des letzten Standes wird nicht umgeschrieben.
- Readiness meldet `persistence_unavailable`; Subscriber erhalten `publication_confirmed=false`.
- Die fachliche Fortschreibung ist eingefroren. Ingest hält nur die letzte Observation je Binding.
- **Recovery** = logischer Restore (§9.4) + `history_gap(db_outage)` + neue Epoche.
- Es gibt keine erfundenen Zwischenereignisse und keine angenommene Kontinuität über die Ausfallzeit.

### 12.5 Writer-Lock
`pg_try_advisory_lock` auf der Writer-Verbindung. Ohne Lock gibt es keine Fortschreibung
(`writer_lock_unavailable`). Bei Lock- oder Verbindungsverlust: sofort stoppen, Readiness rot, und
Wiederaufnahme nur mit neuer Epoche + logischem Restore.

### 12.6 Migrationen
Nummerierte SQL-Dateien mit Checksumme und eigenem kleinem Runner. Sie laufen unter dem Writer-Lock vor
Readiness. Bei veränderter Checksumme oder unbekannt neuerem Schema wird der Start verweigert.
Expand/Contract. Ein App-Update ändert keine fachliche Semantik automatisch.

## 13. Thin HA I/O Bridge

**Grund:** Die öffentliche HA-WS-API liefert `state_reported` nicht (Event-Filter-Pflicht,
`subscribe_entities` ohne `last_reported`).

- HA-Custom-Integration `core_contracts_bridge` mit einstufigem Config-Flow und einer Instanz. Sie
  registriert nur Admin-WS-Kommandos. Die App ist Client über `ws://supervisor/core/websocket` mit
  `SUPERVISOR_TOKEN`.
- **Keine** Entities, Services, Timer, Speicher, Domain-Logik, Quality- oder Freshness-Entscheidung,
  Grace, Fusion oder fachliche Normalisierung. Kein Puffern oder Verwerfen.
- **Protokoll `bridge_protocol = 1`:**
  - `core_contracts_bridge/info` → `{bridge_version, protocol_version, ha_version, ha_state}`
  - `core_contracts_bridge/subscribe {protocol_version, entity_ids}` → erste Nachricht `snapshot`
    (`State.as_dict` inkl. `last_reported`; `null` = absent), danach geordnet `changed`
    (`old_state`, `new_state`, `time_fired`, `context`) und `reported` (`last_reported`,
    `old_last_reported`, `time_fired`, `context_id`).
  - Snapshot und Listener werden im selben Callback ohne `await` registriert.
- **App-Seite:**
  - Ablauf: Auth → `info` → `get_config` → Lifecycle-Events über Standard-`subscribe_events` → `subscribe`.
  - **G17, Beschluss 17.10.2026:** Fehlende/inkompatible Bridge oder HA-Abbruch
    erzeugen sichtbare Transport-/Connectivity-Diagnose und keine neuen HA-Observations.
    Bereits vorhandene Evidence wird ausschließlich nach ihrer normalen Freshness-
    Semantik und ihren harten Gates bewertet (§8); kein sofortiges pauschales
    `unknown` aller HA-Sources. Ohne verwendbare Evidence bleibt das Ergebnis
    `unknown` mit konkretem Reason, ohne Fallback oder positive Ausfall-Evidence.
    HA ist ein Eintrag im Connectivity-Contract, kein globales Core-Gate.
  - Snapshot-Zeilen sind nur bei neueren Zeitstempeln neue Observations.
  - HA-Neustart = Reconnect + `history_gap(ha_disconnect)`, kein Core-Restore.
    Die Lücke belegt keine Kontinuität (§9.3), erneuert keine Messzeit und
    verlängert keine Grace. Technische Umsetzung und neue Tests erst Alpha 8 A2
    nach Review-Freigabe; [ADR 0005](../adr/0005-ha-outage-connectivity.md).
- **Adapter-Fähigkeit (SOLL):** generischer `request_refresh(source_id)` über HA `call_service`
  (z. B. `homeassistant.update_entity`), nur auf Anforderung eines Contract-Typs und synthetisch
  getestet. Die Plattform ruft sonst keine HA-Services auf.

## 14. Supervisor-App

### 14.1 Repository `Levtos/core_contract_app`
```text
repository.yaml
core_contracts/            # App: config.yaml, Dockerfile, rootfs/, DOCS.md, CHANGELOG.md
src/core_contracts/        # Plattform (registry, adapters, evidence, quality, resolver, fusion,
                           #   statemachine, temporal, clock, persistence, runtime, api)
src/core_contracts/testing/contract_types/   # synthetische test.* Typen (Fixtures)
client/core_contracts_client/
custom_components/core_contracts_bridge/
frontend/                  # Alpha-1-Admin-UI
migrations/
tests/
docs/                      # Spezifikation, Prompt, Audits, API, Betrieb
dev/                       # compose (nur Dev/Test), Fake-HA
```

### 14.2 `config.yaml`
`slug: core_contracts`, `arch: [amd64, aarch64]`, `startup: services`, `boot: auto`, `init: true`,
`homeassistant_api: true`, `hassio_api: false`, `ingress: true`, `ingress_port: 8099`,
`panel_admin: true`, `ports: {8787/tcp: null}`, `watchdog: http://[HOST]:[PORT:8787]/health/live`,
`timeout: 30`, `backup: hot`, `services: [mqtt:want]`, `map: [ssl:ro]`.

Optionen:
- PostgreSQL: Host/Port/DB/User, Passwort vom Typ `password`, `sslmode`, CA-Datei.
- `installation_label`, `initialize_empty_database`, `adopt_installation_id`.
- `mqtt_mode` (`supervisor|external|disabled`) mit externen Zugangsdaten vom Typ `password`.
- `log_level`, `log_format`.

Image `ghcr.io/levtos/{arch}-core-contracts`. Kein privileged, kein `docker_api`, kein `host_network`.

### 14.3 Container
- Explizites `FROM` (glibc, Python 3.14). Kein `BUILD_FROM`, kein `build.yaml`.
- Multi-Stage mit `uv sync --frozen`; keine Dependency-Auflösung beim Start.
- Entrypoint bereitet als root nur `/data` und `/data/secrets` vor und startet dann per `setpriv` den
  Python-Prozess dauerhaft **non-root**.
- Ein `HEALTHCHECK` nutzt nur Liveness.

### 14.4 Prozessmodell
Ein Prozess, `asyncio`. Supervisierte Tasks: `ha_adapter`, `mqtt_adapter`, `scheduler`, `api`,
**`processor` (einziger serialisierter autoritativer Pfad)**, `health`.
- Netzwerk-Callbacks verändern nie Zustand.
- Queues sind begrenzt. Ein Überlauf wird sichtbar (`history_gap(ingest_overflow)`) und löst einen
  kontrollierten Resubscribe aus; es gibt kein stilles Verwerfen.
- Mikro-Batching ist zulässig, solange die Reihenfolge erhalten bleibt.

### 14.5 Startup und Shutdown
**Startup:**
1. Config.
2. `installation.json`.
3. DB.
4. Writer-Lock.
5. Migrationen.
6. ID-Prüfung.
7. Epoche.
8. Registry.
9. Restore.
10. Adapter.
11. Erster Snapshot (Timeout).
12. Ready.

**SIGTERM:**
1. Ingest stoppen.
2. Changeset committen oder verwerfen.
3. `going_away` an Subscriber.
4. Epoche beenden.
5. Lock freigeben.

Das Ganze innerhalb von `timeout`.

## 15. Consumer API und CoreContractsClient

- `aiohttp` mit zwei Listenern: `:8787` für Consumer (Bearer-Token, internes Netz) und `:8099` für
  Ingress (Admin, nur Ingress-Quelladresse).
- UI-neutral, JSON, RFC 3339 UTC. Kein DB-Zugriff für Consumer, keine Cross-Imports.

### 15.1 Endpunkte (`api_protocol = 1`)

| Methode | Pfad | Auth |
|---|---|---|
| GET | `/health/live`, `/health/ready` | keine |
| GET | `/api/v1/info` | consumer |
| GET | `/api/v1/types` (registrierte Contract-Typen + Schemas) | consumer |
| GET | `/api/v1/contracts`, `/api/v1/contracts/{id}` | consumer |
| GET | `/api/v1/snapshot?contracts=…` | consumer |
| GET | `/api/v1/ws` | consumer |
| POST/GET | `/api/v1/commands`, `/api/v1/commands/{id}` | consumer |
| GET | `/api/v1/diagnostics`, `/api/v1/diagnostics/problems` | admin bzw. consumer |
| GET | `/api/v1/sources`, `/api/v1/bindings`, `/api/v1/evidence/{source_id}` | admin |
| GET | `/api/v1/history/{contract_id}` | admin |
| GET | `/api/v1/registry/active`, `/registry/revisions[/{n}]`, `/registry/active/export` | admin |
| POST/GET/PUT | `/api/v1/registry/drafts[/{id}]`, `…/validate`, `…/activate` | admin |
| POST | `/api/v1/registry/rollback` | admin |

### 15.2 Envelope
`{contract_id, type {id, version}, enabled, contract_health, fields{name: {status, value, quality,
held_until, since_at, freshness, reasons[], evidence[]}}, available, state_machine?, computed_at,
published_at, publication_seq, registry_revision, epoch_id, fixture}`.

### 15.3 WebSocket
1. `hello {protocol_version, expected_installation_id?}` → `welcome` bzw. `installation_mismatch`.
2. `subscribe {id, contracts}` → atomar `snapshot {epoch_id, publication_seq, contracts}`.
3. `delta {publication_seq, prev_seq, contracts}`.
4. `service_state {ready, publication_confirmed, persistence, ha, bridge, mqtt}`.
5. `resync_required` bei Lücke, neuer Epoche oder langsamem Subscriber (begrenzte Queue). Es gibt kein
   Replay.
6. `ping`/`pong`; beim Shutdown `going_away`.

### 15.4 Revisionsmodell
`registry_revision`, `publication_seq` (monoton, persistent, epochenübergreifend), `epoch_id`. Jeder
Snapshot gehört zu genau einer `publication_seq`.

### 15.5 `CoreContractsClient`
Eigenes Paket (`aiohttp`, `pydantic`).
- `CoreContractsClient(base_url, token, expected_installation_id)` ohne Defaults.
- `info`, `snapshot`, `contract`, `subscribe` (Async-Iterator mit automatischem Resync).
- `connection_state` und `publication_confirmed`.
- `command` mit stabiler ID und Wiederholung bzw. Abfrage.

Der Client ändert nie Status oder Quality. Alte `hass.data`- oder Cross-Import-Wege existieren nicht.

## 16. Commands (Framework)

- Envelope:
  `{command_id, contract_id, command, args, origin {kind, actor, client_name}, issued_at, valid_until,
  expected_registry_revision?}`. `valid_until` ist Pflicht.
- Ergebnisse: `accepted`, `rejected(reason)`, `expired`, `not_supported`, `command_id_conflict`.
- Die Wirkung und das Ergebnis werden in einem Commit persistiert; danach folgt das Ack.
- Gleiche ID + gleicher Inhalt → gespeichertes Ergebnis ohne erneute Wirkung.
- Kein `set_state`, keine freie Wertmutation, keine Wiederholung nach Restore, keine externen Aktionen.
- In Alpha 1 nur synthetisch: `test.state_machine` → `request_b` (mit Guard).
- E3: Typen dürfen geschlossene Argumentmodelle deklarieren, strikt validiert ohne Coercion;
  der vollständige Originalcommand bestimmt die Idempotenz. Kein Argumentmodell bedeutet
  weiterhin ausschließlich leere args. Revisionskonflikt: `rejected/active_revision_conflict`.

## 17. MQTT

`aiomqtt`; Modi `supervisor` (Services-API zur Laufzeit), `external` (Optionen) und `disabled`.
- Nur native Sources und technische Funktionen; kein Contract-Bus, keine Commands, keine Projektion.
- Retained Nachrichten sind nie frisch und nie positive Evidence.
- QoS-Duplikate werden dedupliziert.
- Birth/Will dienen nur der Connectivity.
- Ein Eingangspfad je Beobachtung (§8.4).

## 18. Zeitmodell

- UTC, timezone-aware, `timestamptz`, RFC 3339.
- Monotone Clock nur für Laufzeitdauern, nie persistiert.
- Persistente Fristen als absolute UTC-Zeitpunkte; Neuplanung nach Restart oder Clock-Sprung.
- Sprungerkennung (Wall vs. Monotonic): ein Rückwärtssprung löst keine Doppelauslösung aus (`fired`).
- Kalenderfunktionen in `Europe/Berlin` über `zoneinfo` mit gepinntem `tzdata`.
- DST-Konvention: nicht existente lokale Zeit → erste gültige Zeit danach; mehrdeutige → `fold=0`.
  Getestet; **Review-Punkt**.
- **Clock-Interface:** `now_utc`, `monotonic`, `call_at_utc`, `call_later`, `sleep`. Direktzugriffe sind
  per Ruff verboten.
- **FakeClock** mit `advance` und Sprüngen.

## 19. Security

- `homeassistant_api` only, `hassio_api: false`, kein Privileged-Modus, kein Docker-Socket, kein
  Host-Netz, kein Port-Mapping.
- Tokens je Installation: `consumer_token` und `admin_token` (≥ 256 Bit, `/data/secrets`, 0600,
  rotierbar), zeitkonstanter Vergleich, Rate-Limit.
- Ingress nur von der Supervisor-Adresse, Panel admin-only.
- Prozess non-root.
- Keine Secrets in Registry, Git, Image, URLs oder Logs (Redaction).

## 20. Konfiguration und Secrets

- `SUPERVISOR_TOKEN`: nur aus der Umgebung, nie gespeichert.
- PostgreSQL: Option vom Typ `password`.
- MQTT: Services-API bzw. Option vom Typ `password`.
- CA: `/ssl`.
- ID und Tokens: `/data`.
- Docker-/Compose-Secrets nur im Dev-Setup, nie eingecheckt.

## 21. Health und Readiness

- `/health/live`: Event-Loop-Heartbeat + Processor-Task lebt. **Einziges Ziel für Watchdog und
  Healthcheck.**
- `/health/ready`: Migrationen, DB, Lock, ID-Prüfung, aktive Registry; erster Bridge-Snapshot
  der Epoche nur wenn aktive Contracts HA-Quellen (auch via Monitor/Liveness) benötigen.
  Komponentenliste mit Reasons.
- Ein späterer HA-Verlust macht Readiness nicht rot (erscheint als Komponente).
- DB- oder Lock-Verlust macht Readiness rot, Liveness bleibt grün.
- Ein Ausfall externer Abhängigkeiten erzeugt nie einen Watchdog-Restart.

## 22. Logging und Diagnostics

- Strukturierte Logs (`text`/`json`). Pflichtereignisse:
  - Start/Stop, Epoche, Migrationen
  - Aktivierung/Rollback
  - Lock, DB-Ausfall/-Recovery
  - Adapter-Verbindungen, Queue-Überlauf, Gaps
  - Übergänge nach/aus `unknown`/`unresolved`
  - abgelehnte Commands
- Diagnostics: Versionen, ID, Epoche, Queues, Commit-Dauer, `publication_seq`, Komponenten,
  Bridge-Version, Subscriptions, Gaps, Dependencies.
- Keine Secrets.

## 23. Migration und Upgrade

- App-Updates: §12.6. Ein Update aktiviert keine Revision.
- Legacy-Repository `Levtos/core-contracts` (HA-Integration v0.2.x) bleibt unverändert und live
  (`blind_control` hängt daran). Alpha 1 nutzt ein neues Repository, eine neue DB und berührt die
  Legacy-DB nicht.
- KANN: Werkzeug `import-legacy` → Draft (strippt `profile*`/`consumer_ids`) mit Bericht. Es übernimmt
  **keine** Domain-Contract-Typen und darf den Build nicht dominieren.

## 24. Backup und Restore

- Das Supervisor-Backup sichert `/data`, aber nicht die externe DB.
- PostgreSQL braucht ein separates konsistentes Backup (Runbook in `docs/operations.md`).
- Ein vollständiger Restore braucht App-Version, `/data`, PG-Backup, Secrets und Versionsmetadaten.
- Ein Supervisor-Restore überschreibt die DB nie.
- Ein PG-Restore ist ein CP-15-Fall (neue Epoche, Gap).
- SOLL: Beim Shutdown wird die letzte `publication_seq` in `/data` vermerkt; beim Start wird ein DB-Stand
  darunter erkannt (`database_older_than_last_shutdown`).

## 25. Dependency- und Build-Baseline

- CPython 3.14.x (GIL), `aiohttp`, `asyncpg` 0.32.x, `pydantic` v2, `aiomqtt`, `tzdata`.
- Werkzeuge: `pyproject.toml`, `uv`, `uv.lock`, Ruff (inkl. `DTZ`/banned-api), mypy `--strict`,
  pytest, pytest-asyncio.
- Frontend: Vite + TypeScript (Svelte 5, falls aus dem Legacy-Repository sinnvoll übernehmbar).
- CI: Lint, Typen, Tests, Frontend-Build, Image-Build für amd64 und aarch64.

Versionen (getrennt):

| Komponente | Version |
|---|---|
| App | `1.0.0a2` (veröffentlichte Basis; neue Veröffentlichung separat freizugeben) |
| API-Protokoll | 1 |
| Konfigurationsschema | 1 |
| DB-Schema | Migrationsnummer |
| Bridge-Protokoll | 1 |
| Client | eigene SemVer |
| Contract-Typen | je Typ |

## 26. Alpha-1-Admin-UI

Funktionales Engineering-Werkzeug über Ingress, ausschließlich über die öffentliche API:
- Installation Status (ID, Label, Versionen, Epoche)
- Liveness/Readiness
- HA-Bridge-Status
- PostgreSQL-/Lock-Status
- Registry-Revisionen
- **Sources**, **Bindings**
- Drafts (JSON-Editor), Validierung mit Diff, Aktivierung, Rollback, Export/Import
- Diagnostics
- **Testcontracts** (als TEST markiert)
- Evidence-/Reason-Debugging (Observation-Zeitstempel, Herkunft, Reasons je Feld)
- Gaps
- Tokens (Admin)

Kein Drag-and-Drop, kein finaler Designstandard, keine Policy-Umbrella-UX, kein visueller
Contract-Editor. UI-Layout-State ist kein Registry- oder Contract-State.

## 27. Acceptance Gate (Ende Phase 1)

Phase 1 ist abgeschlossen und Phase 2 darf beginnen, wenn **alle** Punkte nachgewiesen sind (Test,
CI-Lauf oder dokumentierter Smoke-Test). Nicht im Environment ausführbare Punkte sind ausdrücklich
offen und blockieren das Gate, bis Benni sie in einer echten Umgebung abnimmt.

| # | Kriterium | Nachweis |
|---|---|---|
| G1 | **Installation:** App aus lokalem App-Repository installierbar, startet non-root | Supervisor-Smoke |
| G2 | **Bridge:** liefert `info`, Snapshot (inkl. absent), `state_changed`, `state_reported` in Reihenfolge | Fake-HA-Tests + echter HA-Test |
| G3 | **Registry:** Sources mit stabilen IDs anlegen, auf konkrete HA-/MQTT-Quellen binden, Draft → Validierung → Aktivierung → Rollback | Integrationstests + UI-Smoke |
| G4 | **Isolation:** alle `installation_id`-Fälle (§11) | Tests |
| G5 | **Persistenz:** Registry und generischer Runtime-State überleben Restart | Tests |
| G6 | **Restore:** Deadline rekonstruiert, Grace nicht neu gestartet, monotone Werte nicht restauriert, Edge-Baseline nach Fingerprint-Regel, Latch → `initial`, Lücken nicht erfunden, Startfälle unterscheidbar (synthetisch) | Restore-Suite |
| G7 | **Publication:** `test.*` laufen Source → Binding → Evidence → Resolver/Fusion/SM/Temporal → Persistenz → Publication | E2E |
| G8 | **Client:** Snapshot, Subscription, Lücke/Epoche/langsamer Consumer → Resync, Command-Idempotenz | Tests |
| G9 | **DB-Ausfall:** Commit-before-publish (Fehlerinjektion), Ausfall/Recovery mit neuer Epoche und Gap, Writer-Lock exklusiv | Tests |
| G10 | **Health:** Liveness ≠ Readiness; DB-Ausfall erzeugt keinen Watchdog-Restart | Tests + Smoke |
| G11 | **Quality-Invarianten** (§7) und **Evidence-Regeln** (§8) automatisiert | Unit |
| G12 | **No-Shadow/`enabled`**, **keine Profile** (Registry lehnt `profile*` ab; API ohne Profil) | Tests |
| G13 | **Kein Domain-Code:** Registriert sind ausschließlich `test.*`-Typen; ein Test schlägt fehl, sobald ein Nicht-`test.*`-Typ registriert ist | Test `test_no_domain_contract_types` |
| G14 | **Qualität:** Ruff, mypy `--strict`, alle ausführbaren Tests grün; Multi-Arch-Image gebaut | CI |
| G15 | **Doku:** Betrieb, API, Abweichungen, Build-Time-Verification-Stand | Review |

Danach folgen ein unabhängiger Opus-Review, die Prüfung der Befunde und Korrekturen durch Codex.
**Erst dann beginnt Phase 2.** Live bleibt Bennis Gate.

## 28. Tests

- **Unit:** Quality-Invarianten, Reasons, Freshness-Modi und harte Gates, Observation-Neuheit,
  Resolver-Operatoren (dreiwertig, streng, `map`, `bucket`), Fusion-Strategien und Konflikt,
  Held-Propagation (frühestes Ende, keine Ketten), Temporal-Werkzeuge (`stable_for` mit Lücke:
  12:00:00 wahr, 12:00:05 unknown, 12:00:08 wahr → frühestens 12:00:18 erfüllt), SM-Framework (Guards,
  abgelehnte Commands, Episoden, Deadlines), Fingerprint.
- **Restore** (synthetisch): alle Zeilen aus §9.1 und alle Startfälle aus §9.4.
- **PostgreSQL** (Compose): Commit/Rollback, Fehlerinjektion vor Commit, Crash vor/nach Commit,
  DB-Ausfall/Recovery, Writer-Lock (zweite Instanz), Lock-Verlust, Migrationen (Checksumme, neueres
  Schema), alle ID-Fälle, `pg_dump`-Restore.
- **Bridge:** Fake-HA-WS-Server (Snapshot, absent, `changed`, `reported`, Reihenfolge, HA-Restart mit
  `restored`, Bridge-Reload, Reconnect, Gap, Snapshot ohne neue Zeitstempel, fehlende bzw. inkompatible
  Bridge ohne Fallback); zusätzlich ein echter HA-Dev-Container, falls verfügbar.
- **Consumer:** Snapshot, Deltas, `prev_seq`-Lücke, Epoche, langsamer Subscriber, Resync,
  verlorene Command-Antwort, doppelte ID, falsche `expected_installation_id`, `publication_confirmed`,
  `contract_disabled`.
- **MQTT:** Retain, QoS-Duplikate, alte Zeitstempel, Heartbeats, Doppelpfad abgewiesen.
- **Zeit:** FakeClock, Vorwärts- und Rückwärtssprung, DST-Lücke und Doppelstunde.
- **Registry:** Draft nicht ausgewertet, atomare Aktivierung, OCC, Rollback als neue Revision,
  Import → Draft, verbotene Felder, Single-Producer, Zyklus, fehlender Parameter, `enabled`.
- **Supervisor/Container:** Start, Restart, SIGTERM, Watchdog nur Liveness, non-root, `/data`-Rechte,
  Backup/Restore-Smoke.
- **Scope-Wächter:** `test_no_domain_contract_types` (G13) sowie ein Lint-/Grep-Test, der verbietet, dass
  außerhalb von `testing/` und `docs/` Domain-Begriffe als Contract-Typ-ID registriert werden.

## 29. Build-Time Verification

Python-3.14-Kompatibilität aller Dependencies (`asyncpg` 0.32.x, `aiomqtt`); Wheels für amd64/aarch64;
Supervisor-Build mit explizitem `FROM`; Multi-Arch-Image; lokales App-Repository; Admin-WS-Recht des
Supervisor-Users für Bridge-Kommandos; Event-Filter-Registrierung für `state_reported` und Last;
HA-Sendepuffer; Snapshot/Subscription über HA-Restart; MQTT über die Services-API; non-root `/data`;
SIGTERM über `init: true`; Ingress-Adresse und -Header; interner App-Hostname; DB-Ausfall; Writer-Lock;
Backup/Restore; Wiederverwendbarkeit des Frontends.

Scheitert ein Punkt: zuerst eine technische Korrektur innerhalb dieser Spezifikation; keine neue
Fachsemantik.

## 30. Vertagt (Phase 2 oder später)

| Punkt | Phase |
|---|---|
| alle realen Domain-Contracts (Öffnungen, Anwesenheit inkl. Distanz/Richtung/Reconciliation, Bio/Schlaf, Weckplanung, Tagesphase, Aktivität, Medien, Privacy, Klima-Katalog, Außenhelligkeit, Simulation, Strahlung) | Phase 2, einzeln |
| kanonischer Connectivity-Contract mit konkreten Abhängigkeiten (WAN, Zigbee2MQTT …) | Phase 2; das generische Dependency-Modell ist Phase 1 |
| Consumer-/Policy-Migration (`blind_control`, Plug, Door, Climate, Media, Light) | Phase 2, je Contract |
| HA-Entity-Projektionen | nach Phase 1, nur als Spiegel |
| Workbench-/Umbrella-UX | später |
| Cross-HA-Quellen | später (`source_origin`) |
| Parameter-Audit | mit dem jeweiligen Domain-Contract |
| History-Partitionierung/Aufbewahrung | Betrieb |

## 31. Herkunft der Capability-Anforderungen (Nachweis, kein Bauauftrag)

| Framework-Fähigkeit | Herkunft in der Fachakte (Beispiel einer späteren Nutzung, nicht Alpha 1) |
|---|---|
| fünf Status, Reason-Pflicht | M30-01, P4-31a |
| Held-Propagation, keine Grace-Ketten | A-04 (z. B. spätere Quellumschalt-Grace) |
| deklarierte Grace | M30-13, P4-56, P4-65 §1 (spätere Öffnungs- und Anwesenheits-Contracts) |
| Heartbeat-Freshness, `state_reported` | M14-04, P4-65 §3 |
| strenge Auswahl, `unresolved` | M30-04 (spätere Aktivitätsauswahl) |
| Fusion + Konflikt + contractspezifische Strategie | M30-02, M30-08, P4-65 §2 |
| SM mit Guards, Commands, Episoden, Deadlines | M11-*, M30-18/19 (spätere Schlaf-SM) |
| `stable_for`-Kontinuität | M30-14, A-05 |
| Edge-Baseline vs. Latch | M30-03, M30-12, A-06 |
| Revisionsaktivierung | M30-30 |
| Restore bausteinspezifisch | M12-11 (CP-15) |
| Dependency-Reasons | P4-64 §7 (I-1) |
| Source-Refresh-Anforderung | P4-62 §7 (spätere Reconciliation) |
| Registry mit stabilen IDs | M06-04, M06-08, M06-11 |

---

## DOCUMENTATION DELTA (Platform-Korrektur)

Ergänzend zum Delta der superseded Spezifikation (DD-1 bis DD-8 gelten inhaltlich weiter:
Profile entfernt, `installation_id`, D1–D3, technische Baseline, No-Shadow, UX-Scope):

- **DD-9 Phasentrennung:** Neuer Beschluss „Phase 1 = Platform Foundation (Alpha 1), Phase 2 =
  Domain Contracts + Consumer-Migration je Domäne; Phase 2 erst nach Acceptance Gate (§27)“.
  Vermerk in der Fachakte (§18/§30.10) und in `docs/README.md` beider Repositories.
- **DD-10 Superseded:** `Core_Contracts_Alpha1_Build_Specification.md` und
  `Core_Contracts_Alpha1_Codex_Build_Prompt.md` (lokal und in `Levtos/core-contracts/docs/alpha1/`)
  mit Banner „SUPERSEDED — vermischte Phase 1 und 2; nicht ausführen“ versehen. Issue
  `Levtos/core-contracts#46` mit Begründung geschlossen.
- **DD-11 Repository:** Die App-Entwicklung erfolgt in `Levtos/core_contract_app`. `Levtos/core-contracts`
  bleibt die Legacy-Integration bis zur Ablösung in Phase 2.

## SELBSTPRÜFUNG

Gezielt gesucht nach Öffnung/Fenster, Anwesenheit, Bio/Schlaf, Aktivität, Medien, Klima, Beschattung.
Jedes Vorkommen ist ausschließlich (a) vertagte Domain-Logik (§3.1, §30), (b) Herkunft einer
Capability (§31), (c) Scope-Wächter-Test (§28, G13) oder (d) das konzeptionelle Registry-Beispiel
`source.living_room.terrace_door` (nur Source-ID-Format, keine Logik). Es gibt keinen
Implementierungsauftrag für Domain-Contracts. Keine Profil-, Shadow- oder Variant-Reste.
Commit-before-publish, Liveness-Watchdog und die Bridge ohne Fachlogik sind festgeschrieben.
**Keine BUILD-SPEC BLOCKER.**
