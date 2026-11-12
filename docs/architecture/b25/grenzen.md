# b25 – Paketgrenzen: Zuordnung, Startkonfiguration, Arbeitsliste

Stand: `main@0b1fc9d` (1.0.0b24), erzeugt von Claude am 12.11.2026 aus dem Importgraphen. Grundlage: [Architekturprüfung](../../audits/architekturpruefung-2026-11-11.md).
Diese Datei ist Arbeitsmaterial für die b25-Aufträge. Sie ändert nichts am Programm.

## 1. Zuordnung der 94 Module

Die Dateien bleiben in b25 an ihrem Ort (Ausnahme: Kartuschen und neue Module). Die Zugehörigkeit steht in der Konfiguration der Grenzprüfung.

Regeln:
1. Die Tabelle ist von oben nach unten geordnet. Ein Paket darf nur Pakete importieren, die weiter unten stehen.
2. Kein Kernpaket (`kernel`, `store`, `rules`, `lane`, `management`, `adapters`, `api`) importiert eine Kartusche. Nur `app` und `tooling` dürfen das.
3. Kartuschen dürfen `kernel`, `store`, `rules` und `management` importieren, nicht `lane`, `adapters` oder `api`.
4. Kartuschen kennen einander nicht. Ausnahme: `tc_migration` darf `catalog` benutzen.

| Paket | Module |
|---|---|
| **app – Zusammenstecken** | `app`, `wiring`, `contract_types` |
| **tooling – Bauwerkzeuge für Vorlagen** | `apple_tv_package`, `fact_package`, `stash_package` |
| **api – HTTP, WebSocket, Sicherheit** | `api`, `security` |
| **Kartusche catalog** | `apple_tv_catalog`, `catalog_care`, `catalog_cleanup`, `catalog_hierarchy`, `catalog_models`, `catalog_playback`, `catalog_scope`, `interface_upgrade`, `managed_catalog`, `stash_catalog` |
| **Kartusche tc_migration (entfernbar)** | `title_import`, `title_reader`, `title_sync`, `title_sync_runs`, `title_transfer` |
| **Kartusche wake** | `wake`, `wake.operator`, `wake.settings`, `wake.wake_config`, `wake.wake_planning` |
| **Kartusche openings** | `openings` |
| **testing – Testhilfen** | `testing`, `testing.authoring`, `testing.contract_types`, `testing.contract_types.evaluate` |
| **management – Entwürfe, Prüfung, Import/Export, Einrichtung** | `authoring`, `capabilities`, `editorial`, `management`, `manual_restart`, `portable`, `provision`, `reconfiguration`, `revision_plan`, `setup`, `validation` |
| **adapters – HA, MQTT, Dienstkanal** | `adapters.ha`, `adapters.mqtt`, `ha_projection`, `mqtt_restore`, `service_jobs` |
| **lane – die eine Schreibreihenfolge** | `observation_block`, `runtime` |
| **rules – Konfigurationsmodell, Regelsprache, Steckplätze** | `adapters`, `calendar_cache`, `calendar_coverage`, `calendar_inputs`, `calendar_rules`, `dated_rules`, `declarative`, `engines`, `environment_formulas`, `instance_parameters`, `notifications`, `producer`, `refresh_plan`, `registry`, `validation_cache` |
| **store – Zustand und Speicher** | `history`, `persistence`, `retention` |
| **kernel – Werte und reine Logik, keine Ein-/Ausgabe** | `clock`, `declarative_models`, `evidence`, `field_since`, `freshness_templates`, `fusion`, `ha_context`, `identity`, `lifecycle`, `machine_events`, `manual_source`, `model`, `multibucket`, `numeric_equivalence`, `operation_timing`, `quality`, `resolver`, `schema`, `sensor_profile`, `state_tracking`, `statemachine`, `temporal`, `text_rules`, `user_text`, `value_origin` |

Hinweise:
- `adapters/__init__.py` enthält `AdapterConfig` und gehört fachlich zu `rules`. Solange es im Paket `adapters` liegt, kann die Grenzprüfung es nicht getrennt führen; es steht deshalb in keiner Regel. Schritt 3 verschiebt die Klasse in ein eigenes Modul.
- `management` steht in b25 über `lane`, weil `Planner` von `Runtime` erbt. Das wird erst mit dem eigenen Verwaltungsprozess in b26 getrennt.
- Abweichung vom Prüfbericht: Dort ist `kernel` ein Paket. Tatsächlich importieren `producer` und `registry` den Speicher. Deshalb gibt es `kernel` (unter dem Speicher) und `rules` (darüber).
- Abweichung vom Prüfbericht: Kartuschen dürfen `management` importieren, weil Katalog-Upgrade und Titelübernahme den Import/Export-Dienst brauchen.
- Abweichung vom Prüfbericht: Kalender und Umweltformeln sind keine Kartuschen, sondern gehören zu `rules`. Der Kalender ist eine Eingangsart des Kerns wie die Uhr (ADR 0010, 0013); Formeln sind mitgelieferte Bausteine. Beide wurden in der Beta selten geändert (5 und 0 von 61 Backend-Commits).
- Importe unter `TYPE_CHECKING` zählen nicht.

## 2. Startkonfiguration der Grenzprüfung

Geprüft mit import-linter 2.16 gegen den Stand oben: 8 Regeln, alle erfüllt, 50 Ausnahmen (Modulpaare).
Jede Ausnahme ist ein heute vorhandener verbotener Import. **Die Liste darf nur kürzer werden.** Ein entfernter Import muss aus der Liste gelöscht werden, sonst meldet die Prüfung einen Fehler.
Einfügen in `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "core_contracts"
exclude_type_checking_imports = true

[[tool.importlinter.contracts]]
name = "Schichten: oben darf unten importieren, nie umgekehrt"
type = "layers"
layers = [
    "core_contracts.wiring : core_contracts.app : core_contracts.apple_tv_package : core_contracts.contract_types : core_contracts.fact_package : core_contracts.stash_package",
    "core_contracts.api : core_contracts.security",
    "core_contracts.apple_tv_catalog : core_contracts.catalog_care : core_contracts.catalog_cleanup : core_contracts.catalog_hierarchy : core_contracts.catalog_operations : core_contracts.catalog_models : core_contracts.catalog_playback : core_contracts.catalog_scope : core_contracts.interface_upgrade : core_contracts.managed_catalog : core_contracts.openings : core_contracts.stash_catalog : core_contracts.testing : core_contracts.title_import : core_contracts.title_reader : core_contracts.title_sync : core_contracts.title_sync_runs : core_contracts.title_transfer : core_contracts.wake",
    "core_contracts.adapters.ha : core_contracts.adapters.mqtt : core_contracts.authoring : core_contracts.capabilities : core_contracts.editorial : core_contracts.ha_projection : core_contracts.management : core_contracts.manual_restart : core_contracts.mqtt_restore : core_contracts.portable : core_contracts.provision : core_contracts.reconfiguration : core_contracts.revision_plan : core_contracts.service_jobs : core_contracts.setup : core_contracts.validation",
    "core_contracts.observation_block : core_contracts.retention_ops : core_contracts.runtime",
    "core_contracts.calendar_cache : core_contracts.calendar_coverage : core_contracts.calendar_inputs : core_contracts.calendar_rules : core_contracts.dated_rules : core_contracts.declarative : core_contracts.engines : core_contracts.environment_formulas : core_contracts.instance_parameters : core_contracts.notifications : core_contracts.producer : core_contracts.refresh_plan : core_contracts.registry : core_contracts.slots : core_contracts.validation_cache",
    "core_contracts.history : core_contracts.persistence : core_contracts.retention",
    "core_contracts.clock : core_contracts.declarative_models : core_contracts.evidence : core_contracts.field_since : core_contracts.freshness_templates : core_contracts.fusion : core_contracts.ha_context : core_contracts.identity : core_contracts.lifecycle : core_contracts.machine_events : core_contracts.manual_source : core_contracts.model : core_contracts.multibucket : core_contracts.numeric_equivalence : core_contracts.operation_timing : core_contracts.quality : core_contracts.resolver : core_contracts.schema : core_contracts.sensor_profile : core_contracts.state_tracking : core_contracts.statemachine : core_contracts.temporal : core_contracts.text_rules : core_contracts.user_text : core_contracts.value_origin",
]
ignore_imports = [
    # Schritt 3: diese Zeilen müssen verschwinden
    "core_contracts.catalog_care -> core_contracts.title_sync",
    "core_contracts.catalog_scope -> core_contracts.title_sync",
    "core_contracts.interface_upgrade -> core_contracts.title_sync",
    "core_contracts.manual_source -> core_contracts.registry",
    "core_contracts.registry -> core_contracts.validation",
    "core_contracts.value_origin -> core_contracts.adapters.ha",
    # Schritt 4: diese Zeilen müssen verschwinden
    "core_contracts.runtime -> core_contracts.authoring",
    "core_contracts.runtime -> core_contracts.catalog_cleanup",
    "core_contracts.runtime -> core_contracts.catalog_playback",
    "core_contracts.runtime -> core_contracts.editorial",
    "core_contracts.runtime -> core_contracts.managed_catalog",
    "core_contracts.runtime -> core_contracts.management",
    "core_contracts.runtime -> core_contracts.mqtt_restore",
    "core_contracts.runtime -> core_contracts.portable",
    "core_contracts.runtime -> core_contracts.reconfiguration",
    "core_contracts.runtime -> core_contracts.revision_plan",
    "core_contracts.runtime -> core_contracts.service_jobs",
    "core_contracts.runtime -> core_contracts.title_sync",
    "core_contracts.runtime -> core_contracts.title_sync_runs",
    "core_contracts.runtime -> core_contracts.validation",
    # Schritt 5 (wake): diese Zeilen müssen verschwinden
    "core_contracts.capabilities -> core_contracts.wake.settings",
    "core_contracts.declarative -> core_contracts.wake.operator",
    "core_contracts.declarative -> core_contracts.wake.settings",
    "core_contracts.instance_parameters -> core_contracts.wake.settings",
    "core_contracts.manual_source -> core_contracts.wake.operator",
    "core_contracts.manual_source -> core_contracts.wake.settings",
    # Schritt 5 (catalog, tc_migration): diese Zeilen müssen verschwinden
    "core_contracts.adapters -> core_contracts.apple_tv_catalog",
    "core_contracts.adapters.ha -> core_contracts.apple_tv_catalog",
    "core_contracts.adapters.ha -> core_contracts.stash_catalog",
    "core_contracts.api -> core_contracts.catalog_care",
    "core_contracts.api -> core_contracts.catalog_models",
    "core_contracts.api -> core_contracts.catalog_scope",
    "core_contracts.api -> core_contracts.managed_catalog",
    "core_contracts.api -> core_contracts.title_sync",
    "core_contracts.api -> core_contracts.title_sync_runs",
    "core_contracts.authoring -> core_contracts.catalog_models",
    "core_contracts.authoring -> core_contracts.managed_catalog",
    "core_contracts.declarative -> core_contracts.managed_catalog",
    "core_contracts.portable -> core_contracts.catalog_models",
    "core_contracts.portable -> core_contracts.interface_upgrade",
    "core_contracts.portable -> core_contracts.managed_catalog",
    "core_contracts.producer -> core_contracts.managed_catalog",
    "core_contracts.registry -> core_contracts.catalog_models",
    "core_contracts.validation -> core_contracts.catalog_models",
]

[[tool.importlinter.contracts]]
name = "Kern importiert keine Kartusche"
type = "forbidden"
source_modules = [
    "core_contracts.adapters.ha",
    "core_contracts.adapters.mqtt",
    "core_contracts.api",
    "core_contracts.authoring",
    "core_contracts.calendar_cache",
    "core_contracts.calendar_coverage",
    "core_contracts.calendar_inputs",
    "core_contracts.calendar_rules",
    "core_contracts.capabilities",
    "core_contracts.clock",
    "core_contracts.dated_rules",
    "core_contracts.declarative",
    "core_contracts.declarative_models",
    "core_contracts.editorial",
    "core_contracts.engines",
    "core_contracts.environment_formulas",
    "core_contracts.evidence",
    "core_contracts.field_since",
    "core_contracts.freshness_templates",
    "core_contracts.fusion",
    "core_contracts.ha_context",
    "core_contracts.ha_projection",
    "core_contracts.history",
    "core_contracts.identity",
    "core_contracts.instance_parameters",
    "core_contracts.lifecycle",
    "core_contracts.machine_events",
    "core_contracts.management",
    "core_contracts.manual_restart",
    "core_contracts.manual_source",
    "core_contracts.model",
    "core_contracts.mqtt_restore",
    "core_contracts.multibucket",
    "core_contracts.notifications",
    "core_contracts.numeric_equivalence",
    "core_contracts.observation_block",
    "core_contracts.operation_timing",
    "core_contracts.persistence",
    "core_contracts.portable",
    "core_contracts.producer",
    "core_contracts.provision",
    "core_contracts.quality",
    "core_contracts.reconfiguration",
    "core_contracts.refresh_plan",
    "core_contracts.registry",
    "core_contracts.resolver",
    "core_contracts.retention",
    "core_contracts.revision_plan",
    "core_contracts.retention_ops",
    "core_contracts.runtime",
    "core_contracts.schema",
    "core_contracts.security",
    "core_contracts.slots",
    "core_contracts.sensor_profile",
    "core_contracts.service_jobs",
    "core_contracts.setup",
    "core_contracts.state_tracking",
    "core_contracts.statemachine",
    "core_contracts.temporal",
    "core_contracts.text_rules",
    "core_contracts.user_text",
    "core_contracts.validation",
    "core_contracts.validation_cache",
    "core_contracts.value_origin",
]
forbidden_modules = [
    "core_contracts.apple_tv_catalog",
    "core_contracts.catalog_care",
    "core_contracts.catalog_cleanup",
    "core_contracts.catalog_hierarchy",
    "core_contracts.catalog_operations",
    "core_contracts.catalog_models",
    "core_contracts.catalog_playback",
    "core_contracts.catalog_scope",
    "core_contracts.interface_upgrade",
    "core_contracts.managed_catalog",
    "core_contracts.openings",
    "core_contracts.stash_catalog",
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
    "core_contracts.wake",
]
ignore_imports = [
    # Schritt 3: diese Zeilen müssen verschwinden
    "core_contracts.catalog_care -> core_contracts.title_sync",
    "core_contracts.catalog_scope -> core_contracts.title_sync",
    "core_contracts.interface_upgrade -> core_contracts.title_sync",
    # Schritt 4: diese Zeilen müssen verschwinden
    "core_contracts.runtime -> core_contracts.catalog_cleanup",
    "core_contracts.runtime -> core_contracts.catalog_playback",
    "core_contracts.runtime -> core_contracts.managed_catalog",
    "core_contracts.runtime -> core_contracts.title_sync",
    "core_contracts.runtime -> core_contracts.title_sync_runs",
    # Schritt 5 (wake): diese Zeilen müssen verschwinden
    "core_contracts.capabilities -> core_contracts.wake.settings",
    "core_contracts.declarative -> core_contracts.wake.operator",
    "core_contracts.declarative -> core_contracts.wake.settings",
    "core_contracts.instance_parameters -> core_contracts.wake.settings",
    "core_contracts.manual_source -> core_contracts.wake.operator",
    "core_contracts.manual_source -> core_contracts.wake.settings",
    # Schritt 5 (catalog, tc_migration): diese Zeilen müssen verschwinden
    "core_contracts.adapters -> core_contracts.apple_tv_catalog",
    "core_contracts.adapters.ha -> core_contracts.apple_tv_catalog",
    "core_contracts.adapters.ha -> core_contracts.stash_catalog",
    "core_contracts.api -> core_contracts.catalog_care",
    "core_contracts.api -> core_contracts.catalog_models",
    "core_contracts.api -> core_contracts.catalog_scope",
    "core_contracts.api -> core_contracts.managed_catalog",
    "core_contracts.api -> core_contracts.title_sync",
    "core_contracts.api -> core_contracts.title_sync_runs",
    "core_contracts.authoring -> core_contracts.catalog_models",
    "core_contracts.authoring -> core_contracts.managed_catalog",
    "core_contracts.declarative -> core_contracts.managed_catalog",
    "core_contracts.portable -> core_contracts.catalog_models",
    "core_contracts.portable -> core_contracts.interface_upgrade",
    "core_contracts.portable -> core_contracts.managed_catalog",
    "core_contracts.producer -> core_contracts.managed_catalog",
    "core_contracts.registry -> core_contracts.catalog_models",
    "core_contracts.validation -> core_contracts.catalog_models",
]

[[tool.importlinter.contracts]]
name = "Kartusche catalog kennt keine andere Kartusche"
type = "forbidden"
allow_indirect_imports = true
source_modules = [
    "core_contracts.apple_tv_catalog",
    "core_contracts.catalog_care",
    "core_contracts.catalog_cleanup",
    "core_contracts.catalog_hierarchy",
    "core_contracts.catalog_operations",
    "core_contracts.catalog_models",
    "core_contracts.catalog_playback",
    "core_contracts.catalog_scope",
    "core_contracts.interface_upgrade",
    "core_contracts.managed_catalog",
    "core_contracts.stash_catalog",
]
forbidden_modules = [
    "core_contracts.openings",
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
    "core_contracts.wake",
]
ignore_imports = [
    # Schritt 3: diese Zeilen müssen verschwinden
    "core_contracts.catalog_care -> core_contracts.title_sync",
    "core_contracts.catalog_scope -> core_contracts.title_sync",
    "core_contracts.interface_upgrade -> core_contracts.title_sync",
]

[[tool.importlinter.contracts]]
name = "Kartusche wake kennt keine andere Kartusche"
type = "forbidden"
allow_indirect_imports = true
source_modules = [
    "core_contracts.wake",
]
forbidden_modules = [
    "core_contracts.apple_tv_catalog",
    "core_contracts.catalog_care",
    "core_contracts.catalog_cleanup",
    "core_contracts.catalog_hierarchy",
    "core_contracts.catalog_operations",
    "core_contracts.catalog_models",
    "core_contracts.catalog_playback",
    "core_contracts.catalog_scope",
    "core_contracts.interface_upgrade",
    "core_contracts.managed_catalog",
    "core_contracts.openings",
    "core_contracts.stash_catalog",
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
]

[[tool.importlinter.contracts]]
name = "Kartusche openings kennt keine andere Kartusche"
type = "forbidden"
allow_indirect_imports = true
source_modules = [
    "core_contracts.openings",
]
forbidden_modules = [
    "core_contracts.apple_tv_catalog",
    "core_contracts.catalog_care",
    "core_contracts.catalog_cleanup",
    "core_contracts.catalog_hierarchy",
    "core_contracts.catalog_operations",
    "core_contracts.catalog_models",
    "core_contracts.catalog_playback",
    "core_contracts.catalog_scope",
    "core_contracts.interface_upgrade",
    "core_contracts.managed_catalog",
    "core_contracts.stash_catalog",
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
    "core_contracts.wake",
]

[[tool.importlinter.contracts]]
name = "Titelabgleich kennt nur catalog"
type = "forbidden"
allow_indirect_imports = true
source_modules = [
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
]
forbidden_modules = [
    "core_contracts.openings",
    "core_contracts.wake",
]

[[tool.importlinter.contracts]]
name = "Kartuschen importieren weder lane noch adapters noch api"
type = "forbidden"
allow_indirect_imports = true
source_modules = [
    "core_contracts.apple_tv_catalog",
    "core_contracts.catalog_care",
    "core_contracts.catalog_cleanup",
    "core_contracts.catalog_hierarchy",
    "core_contracts.catalog_operations",
    "core_contracts.catalog_models",
    "core_contracts.catalog_playback",
    "core_contracts.catalog_scope",
    "core_contracts.interface_upgrade",
    "core_contracts.managed_catalog",
    "core_contracts.openings",
    "core_contracts.stash_catalog",
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
    "core_contracts.wake",
]
forbidden_modules = [
    "core_contracts.adapters.ha",
    "core_contracts.adapters.mqtt",
    "core_contracts.api",
    "core_contracts.ha_projection",
    "core_contracts.mqtt_restore",
    "core_contracts.observation_block",
    "core_contracts.retention_ops",
    "core_contracts.runtime",
    "core_contracts.security",
    "core_contracts.service_jobs",
]

[[tool.importlinter.contracts]]
name = "Testhilfen nur über die Zusammensteckung"
type = "forbidden"
allow_indirect_imports = true
source_modules = [
    "core_contracts.adapters.ha",
    "core_contracts.adapters.mqtt",
    "core_contracts.api",
    "core_contracts.authoring",
    "core_contracts.calendar_cache",
    "core_contracts.calendar_coverage",
    "core_contracts.calendar_inputs",
    "core_contracts.calendar_rules",
    "core_contracts.capabilities",
    "core_contracts.clock",
    "core_contracts.dated_rules",
    "core_contracts.declarative",
    "core_contracts.declarative_models",
    "core_contracts.editorial",
    "core_contracts.engines",
    "core_contracts.environment_formulas",
    "core_contracts.evidence",
    "core_contracts.field_since",
    "core_contracts.freshness_templates",
    "core_contracts.fusion",
    "core_contracts.ha_context",
    "core_contracts.ha_projection",
    "core_contracts.history",
    "core_contracts.identity",
    "core_contracts.instance_parameters",
    "core_contracts.lifecycle",
    "core_contracts.machine_events",
    "core_contracts.management",
    "core_contracts.manual_restart",
    "core_contracts.manual_source",
    "core_contracts.model",
    "core_contracts.mqtt_restore",
    "core_contracts.multibucket",
    "core_contracts.notifications",
    "core_contracts.numeric_equivalence",
    "core_contracts.observation_block",
    "core_contracts.operation_timing",
    "core_contracts.persistence",
    "core_contracts.portable",
    "core_contracts.producer",
    "core_contracts.provision",
    "core_contracts.quality",
    "core_contracts.reconfiguration",
    "core_contracts.refresh_plan",
    "core_contracts.registry",
    "core_contracts.resolver",
    "core_contracts.retention",
    "core_contracts.revision_plan",
    "core_contracts.retention_ops",
    "core_contracts.runtime",
    "core_contracts.schema",
    "core_contracts.security",
    "core_contracts.slots",
    "core_contracts.sensor_profile",
    "core_contracts.service_jobs",
    "core_contracts.setup",
    "core_contracts.state_tracking",
    "core_contracts.statemachine",
    "core_contracts.temporal",
    "core_contracts.text_rules",
    "core_contracts.user_text",
    "core_contracts.validation",
    "core_contracts.validation_cache",
    "core_contracts.value_origin",
    "core_contracts.apple_tv_catalog",
    "core_contracts.catalog_care",
    "core_contracts.catalog_cleanup",
    "core_contracts.catalog_hierarchy",
    "core_contracts.catalog_operations",
    "core_contracts.catalog_models",
    "core_contracts.catalog_playback",
    "core_contracts.catalog_scope",
    "core_contracts.interface_upgrade",
    "core_contracts.managed_catalog",
    "core_contracts.openings",
    "core_contracts.stash_catalog",
    "core_contracts.title_import",
    "core_contracts.title_reader",
    "core_contracts.title_sync",
    "core_contracts.title_sync_runs",
    "core_contracts.title_transfer",
    "core_contracts.wake",
]
forbidden_modules = [
    "core_contracts.testing",
]
```

## 3. Arbeitsliste: 105 verbotene Importanweisungen

Zeilennummern gelten für den Stand oben und verschieben sich mit jedem Schritt. Maßgeblich ist danach die Ausnahmeliste in `pyproject.toml`.
`fn` = Import innerhalb einer Funktion, `top` = am Dateianfang.

### Schritt 3 – Aufwärtsimporte außerhalb der Zentrale (8)

| Richtung | Datei:Zeile | Art | importiert aus | Namen |
|---|---|---|---|---|
| Kartusche catalog → Kartusche tc_migration | `catalog_care:379` | fn | `title_sync` | `current_mapping` |
| Kartusche catalog → Kartusche tc_migration | `catalog_care:620` | fn | `title_sync` | `current_mapping, keys` |
| Kartusche catalog → Kartusche tc_migration | `catalog_scope:20` | top | `title_sync` | `ImportWrite, current_mapping` |
| Kartusche catalog → Kartusche tc_migration | `interface_upgrade:313` | fn | `title_sync` | `current_mapping` |
| kernel → adapters | `value_origin:107` | fn | `adapters.ha` | `transform` |
| kernel → rules | `manual_source:68` | fn | `registry` | `resolve_ref` |
| kernel → rules | `manual_source:184` | fn | `registry` | `fingerprints` |
| rules → management | `registry:393` | fn | `validation` | `validate_document` |

### Schritt 4 – alles in `runtime.py` (59)

| Richtung | Datei:Zeile | Art | importiert aus | Namen |
|---|---|---|---|---|
| lane → Kartusche catalog | `runtime:712` | fn | `managed_catalog` | `validate_heads, verify_stored_heads` |
| lane → Kartusche catalog | `runtime:721` | fn | `catalog_playback` | `baseline_pending` |
| lane → Kartusche catalog | `runtime:783` | fn | `catalog_cleanup` | `preview` |
| lane → Kartusche catalog | `runtime:1155` | fn | `catalog_scope` | `process` |
| lane → Kartusche catalog | `runtime:1168` | fn | `catalog_cleanup` | `apply` |
| lane → Kartusche catalog | `runtime:1182` | fn | `catalog_playback` | `flush` |
| lane → Kartusche catalog | `runtime:1229` | fn | `managed_catalog` | `definition, historical_entries` |
| lane → Kartusche catalog | `runtime:1247` | fn | `catalog_models` | `CatalogRestore, CatalogWrite, EntryChange` |
| lane → Kartusche catalog | `runtime:1248` | fn | `managed_catalog` | `affected_contracts, definition, head, historical_entries, invalidate, prepare` |
| lane → Kartusche catalog | `runtime:1262` | fn | `catalog_care` | `CareRequest, change_request` |
| lane → Kartusche catalog | `runtime:1830` | fn | `catalog_playback` | `baseline_pending` |
| lane → Kartusche catalog | `runtime:1867` | fn | `catalog_playback` | `frame` |
| lane → Kartusche catalog | `runtime:1925` | fn | `catalog_playback` | `frame` |
| lane → Kartusche catalog | `runtime:2389` | fn | `catalog_playback` | `finish` |
| lane → Kartusche catalog | `runtime:2692` | fn | `catalog_playback` | `next_due` |
| lane → Kartusche tc_migration | `runtime:789` | fn | `title_sync_runs` | `preview` |
| lane → Kartusche tc_migration | `runtime:968` | fn | `title_sync` | `restore_mapping` |
| lane → Kartusche tc_migration | `runtime:1139` | fn | `title_sync_runs` | `process` |
| lane → Kartusche tc_migration | `runtime:1189` | fn | `title_transfer` | `process` |
| lane → adapters | `runtime:740` | fn | `service_jobs` | `restore_jobs` |
| lane → adapters | `runtime:744` | fn | `mqtt_restore` | `restore` |
| lane → adapters | `runtime:1472` | fn | `service_jobs` | `complete_calendar` |
| lane → adapters | `runtime:1880` | fn | `mqtt_restore` | `retained_replay` |
| lane → adapters | `runtime:1922` | fn | `mqtt_restore` | `remember` |
| lane → adapters | `runtime:2210` | fn | `service_jobs` | `plan_calendars` |
| lane → adapters | `runtime:2813` | fn | `service_jobs` | `due_dates` |
| lane → management | `runtime:196` | fn | `revision_plan` | `build` |
| lane → management | `runtime:388` | fn | `portable` | `read_package, strict_json` |
| lane → management | `runtime:396` | fn | `validation` | `RegistryValidationError` |
| lane → management | `runtime:409` | fn | `validation` | `RegistryValidationError, ValidationIssue` |
| lane → management | `runtime:527` | fn | `validation` | `RegistryValidationError, ValidationIssue` |
| lane → management | `runtime:720` | fn | `authoring` | `migrate` |
| lane → management | `runtime:727` | fn | `reconfiguration` | `migrate_legacy_contexts` |
| lane → management | `runtime:778` | fn | `management` | `OPERATIONS, execute` |
| lane → management | `runtime:874` | fn | `authoring` | `AuthoringError` |
| lane → management | `runtime:875` | fn | `validation` | `RegistryValidationError` |
| lane → management | `runtime:942` | fn | `management` | `capture` |
| lane → management | `runtime:946` | fn | `management` | `commit` |
| lane → management | `runtime:961` | fn | `editorial` | `keep_newer` |
| lane → management | `runtime:1193` | fn | `portable` | `export` |
| lane → management | `runtime:1197` | fn | `portable` | `preview` |
| lane → management | `runtime:1205` | fn | `portable` | `stage` |
| lane → management | `runtime:1394` | fn | `editorial` | `EditorialWrite, put` |
| lane → management | `runtime:1402` | fn | `editorial` | `refresh_areas` |
| lane → management | `runtime:1538` | fn | `authoring` | `authoring` |
| lane → management | `runtime:1591` | fn | `authoring` | `base_fields, guard, open_count` |
| lane → management | `runtime:1607` | fn | `validation` | `RegistryValidationError` |
| lane → management | `runtime:1633` | fn | `authoring` | `bind_plan` |
| lane → management | `runtime:1636` | fn | `editorial` | `EditorialWrite, carry_draft, put` |
| lane → management | `runtime:1663` | fn | `authoring` | `guard` |
| lane → management | `runtime:1679` | fn | `capabilities` | `machine_description` |
| lane → management | `runtime:1708` | fn | `authoring` | `authoring` |
| lane → management | `runtime:1737` | fn | `editorial` | `apply_draft` |
| lane → management | `runtime:1740` | fn | `portable` | `apply` |
| lane → management | `runtime:1787` | fn | `reconfiguration` | `preserve` |
| lane → management | `runtime:1817` | fn | `reconfiguration` | `same_ha_subscription` |
| lane → management | `runtime:1849` | fn | `manual_restart` | `preview` |
| lane → management | `runtime:1853` | fn | `manual_restart` | `execute` |
| lane → management | `runtime:2659` | fn | `authoring` | `archive_versions` |

### Schritt 5 – Kartuschen catalog und tc_migration (32)

| Richtung | Datei:Zeile | Art | importiert aus | Namen |
|---|---|---|---|---|
| adapters → Kartusche catalog | `adapters.ha:428` | fn | `stash_catalog` | `metadata` |
| adapters → Kartusche catalog | `adapters.ha:435` | fn | `apple_tv_catalog` | `parse` |
| api → Kartusche catalog | `api:457` | fn | `catalog_models` | `ManagedCatalog` |
| api → Kartusche catalog | `api:458` | fn | `catalog_scope` | `device_scope` |
| api → Kartusche catalog | `api:476` | fn | `catalog_care` | `detail, listing, queue` |
| api → Kartusche catalog | `api:477` | fn | `managed_catalog` | `definition` |
| api → Kartusche catalog | `api:541` | fn | `managed_catalog` | `definition, page` |
| api → Kartusche catalog | `api:569` | fn | `managed_catalog` | `definition, entry_view` |
| api → Kartusche catalog | `api:576` | fn | `managed_catalog` | `definition, head, head_key` |
| api → Kartusche tc_migration | `api:383` | fn | `title_sync` | `current_mapping` |
| api → Kartusche tc_migration | `api:387` | fn | `title_sync` | `public_plan` |
| api → Kartusche tc_migration | `api:406` | fn | `title_sync_runs` | `page_view` |
| api → Kartusche tc_migration | `api:410` | fn | `title_sync_runs` | `cancel` |
| api → Kartusche tc_migration | `api:417` | fn | `title_sync` | `diff_page` |
| management → Kartusche catalog | `authoring:84` | fn | `catalog_models` | `EntryChange` |
| management → Kartusche catalog | `authoring:86` | fn | `managed_catalog` | `definition, definition_digest, head, identity` |
| management → Kartusche catalog | `authoring:808` | fn | `catalog_models` | `EntryChange` |
| management → Kartusche catalog | `authoring:810` | fn | `managed_catalog` | `definition, definition_digest, head, head_key, identity` |
| management → Kartusche catalog | `portable:9` | top | `catalog_models` | `CatalogWrite, EntryChange, ManagedCatalog` |
| management → Kartusche catalog | `portable:11` | top | `managed_catalog` | `definition_digest, head, head_key, identity, invalidate, prepare` |
| management → Kartusche catalog | `portable:685` | fn | `interface_upgrade` | `migration_preview` |
| management → Kartusche catalog | `portable:886` | fn | `interface_upgrade` | `migrate_runtime, migration_preview` |
| management → Kartusche catalog | `portable:895` | fn | `managed_catalog` | `definition` |
| management → Kartusche catalog | `validation:227` | fn | `catalog_models` | `ManagedCatalog` |
| rules → Kartusche catalog | `adapters:7` | top | `apple_tv_catalog` | `AppleTitleConfig` |
| rules → Kartusche catalog | `declarative:660` | fn | `managed_catalog` | `dependencies, suspend_machine` |
| rules → Kartusche catalog | `declarative:858` | fn | `managed_catalog` | `dependencies` |
| rules → Kartusche catalog | `declarative:887` | fn | `managed_catalog` | `lookup` |
| rules → Kartusche catalog | `declarative:998` | fn | `managed_catalog` | `dependencies` |
| rules → Kartusche catalog | `producer:131` | fn | `managed_catalog` | `dependencies` |
| rules → Kartusche catalog | `producer:187` | fn | `managed_catalog` | `dependencies, suspend_machine` |
| rules → Kartusche catalog | `registry:12` | top | `catalog_models` | `ManagedCatalog` |

### Schritt 5 – Kartusche wake (6)

| Richtung | Datei:Zeile | Art | importiert aus | Namen |
|---|---|---|---|---|
| kernel → Kartusche wake | `manual_source:120` | fn | `wake.operator` | `local_start` |
| kernel → Kartusche wake | `manual_source:121` | fn | `wake.settings` | `WakeException` |
| management → Kartusche wake | `capabilities:12` | top | `wake.settings` | `WakeException, WakeSettings` |
| rules → Kartusche wake | `declarative:738` | fn | `wake.operator` | `build_plan` |
| rules → Kartusche wake | `declarative:739` | fn | `wake.settings` | `WakeSettings` |
| rules → Kartusche wake | `instance_parameters:56` | fn | `wake.settings` | `WakeSettings` |


## Schritt 4 – Steckplätze

`slots` gehört zu rules, `wiring` zu app. Register enthalten nur Einträge;
die Zentrale ruft Haken nach fester Ordnungszahl an ihren bisherigen Stellen auf.
`SUBMIT` bindet vorgelagerte Planer an, `CONFIGURATION` die bisherigen
Konfigurationshelfer. Damit bleiben die vorhandenen Runtime-Aufruf- und
Überschreibungspunkte erhalten, ohne ihre Eigentümer zu importieren.
