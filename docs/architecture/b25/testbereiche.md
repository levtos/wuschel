# b25 – Testbereiche: welche Testdatei gehört zu welchem Bereich

Stand: `main@0b1fc9d` (1.0.0b24), gemessen von Claude am 12.11.2026. Arbeitsmaterial für die b25-Aufträge.

## Wie gemessen wurde

Die Backend-Suite lief einmal mit Zeilenabdeckung je Test (`coverage`, Kontext je Test, C-Tracer). Eine Testdatei zählt zu einem Bereich, wenn sie dort mindestens fünf Zeilen **innerhalb von Funktionen** ausführt, die nicht zum Grundrauschen gehören. Grundrauschen sind Zeilen, die mindestens die Hälfte aller Tests mit laufender Zentrale ausführt (zum Beispiel der Katalog-Haken, der bei leerem Katalog sofort zurückkehrt).
106 von 127 Testdateien wurden so gemessen. 21 liefen in der Messumgebung nicht (PostgreSQL, Docker, Lasttests, Release-Prüfungen) und sind von Hand zugeordnet (Bereiche `store`, `load`, `release`, `traber`, `adapters`).

## Ergebnis

| Bereich | Testdateien | Bedeutung |
|---|---|---|
| `wake` | 7 | Kartusche |
| `calendar` | 13 | Kernteil (`rules`), eigener Testbereich |
| `openings` | 11 | Kartusche |
| `environment` | 1 | Kernteil (`rules`), eigener Testbereich |
| `tc_migration` | 26 | Kartusche |
| `catalog` | 54 | Kartusche |
| `api` | 32 | Kernpaket |
| `adapters` | 44 | Kernpaket |
| `management` | 79 | Kernpaket |
| `tooling` | 3 | Bauwerkzeuge |
| `traber` | 4 | HA-Integration und Projektion |
| `release` | 7 | Release- und CI-Prüfungen |
| `store` | 8 | braucht PostgreSQL, läuft nur in der CI |
| `load` | 3 | Lasttests, laufen nur in der vollen CI |

7 gemessene Testdateien berühren keinen der Bereiche und prüfen nur den Kern (`kernel`, `store`, `rules`, `lane`).
Zum Vergleich: Über Importe erreicht heute fast jedes Modul 114 von 127 Testdateien.

## Regel für die Auswahl

1. Ändert ein Auftrag **nur** Pfade eines oder mehrerer Bereiche, laufen lokal deren Testdateien.
2. Ändert er irgendetwas am Kern (`kernel`, `store`, `rules`, `lane`, alles, was in keinem Bereich steht), laufen lokal die Tests der betroffenen Module nach Ermessen; die volle Suite läuft in der CI.
3. Die volle Suite läuft unabhängig davon bei jedem PR in der CI. Die Auswahl spart Arbeit im Auftrag, sie ersetzt die CI nicht.
4. Die Zuordnung veraltet mit jedem Umbauschritt. Schritt 6 (#120) erzeugt sie mit derselben Methode neu.

## Inhalt für `tests/areas.json`

```json
{
 "wake": {
  "paths": [
   "src/core_contracts/extensions/wake/"
  ],
  "tests": [
   "tests/test_beta_command_ingress.py",
   "tests/test_beta_completion_context.py",
   "tests/test_beta_media.py",
   "tests/test_beta_sleep.py",
   "tests/test_beta_sleep_boundaries.py",
   "tests/test_beta_wake.py",
   "tests/test_wake_port.py"
  ]
 },
 "calendar": {
  "paths": [
   "src/core_contracts/calendar_cache.py",
   "src/core_contracts/calendar_coverage.py",
   "src/core_contracts/calendar_inputs.py",
   "src/core_contracts/calendar_rules.py",
   "src/core_contracts/dated_rules.py"
  ],
  "tests": [
   "tests/test_alpha9_calendar.py",
   "tests/test_b22_p11.py",
   "tests/test_b22_p8.py",
   "tests/test_b23_coverage.py",
   "tests/test_beta13_numbers.py",
   "tests/test_beta17_corrections.py",
   "tests/test_beta4_revision_origin.py",
   "tests/test_beta_sleep_boundaries.py",
   "tests/test_beta_sleep_calendars.py",
   "tests/test_calendar_inputs.py",
   "tests/test_calendar_rules.py",
   "tests/test_time_templates.py",
   "tests/test_workbench_api.py"
  ]
 },
 "openings": {
  "paths": [
   "src/core_contracts/extensions/openings/"
  ],
  "tests": [
   "tests/test_b23_p6_scale.py",
   "tests/test_b23_type_registry.py",
   "tests/test_beta4_adapters.py",
   "tests/test_beta4_freshness.py",
   "tests/test_beta4_revision_origin.py",
   "tests/test_beta5_projection.py",
   "tests/test_beta5_retained.py",
   "tests/test_beta_openings.py",
   "tests/test_presence_templates.py",
   "tests/test_scope_packaging.py",
   "tests/test_startup_lifecycle.py"
  ]
 },
 "environment": {
  "paths": [
   "src/core_contracts/environment_formulas.py"
  ],
  "tests": [
   "tests/test_environment_formulas.py"
  ]
 },
 "tc_migration": {
  "paths": [
   "src/core_contracts/extensions/tc_migration/"
  ],
  "tests": [
   "tests/test_b20_a1_repro.py",
   "tests/test_b22_p1.py",
   "tests/test_b22_p11.py",
   "tests/test_b22_p2.py",
   "tests/test_b22_p5.py",
   "tests/test_b22_p6.py",
   "tests/test_b22_p7.py",
   "tests/test_beta12_portable.py",
   "tests/test_beta13_numbers.py",
   "tests/test_beta14_portable.py",
   "tests/test_beta3_authoring.py",
   "tests/test_beta4_portable.py",
   "tests/test_beta_completion_context.py",
   "tests/test_beta_media_import.py",
   "tests/test_beta_openings.py",
   "tests/test_catalog_care.py",
   "tests/test_catalog_cleanup.py",
   "tests/test_catalog_scope.py",
   "tests/test_catalog_scope_conflicts.py",
   "tests/test_catalog_title_keys.py",
   "tests/test_portable.py",
   "tests/test_presence_templates.py",
   "tests/test_title_import.py",
   "tests/test_title_reader.py",
   "tests/test_title_sync.py",
   "tests/test_title_sync_pages.py"
  ]
 },
 "catalog": {
  "paths": [
   "src/core_contracts/extensions/catalog/"
  ],
  "tests": [
   "tests/test_alpha4_acceptance.py",
   "tests/test_alpha4_delta2.py",
   "tests/test_alpha4_review.py",
   "tests/test_alpha6.py",
   "tests/test_alpha6_ux12.py",
   "tests/test_alpha7_manual_restart.py",
   "tests/test_alpha7_post1.py",
   "tests/test_alpha9_deadlines.py",
   "tests/test_alpha9_refresh.py",
   "tests/test_alpha9_sensor_origin.py",
   "tests/test_b20_a1_repro.py",
   "tests/test_b22_p1.py",
   "tests/test_b22_p11.py",
   "tests/test_b22_p2.py",
   "tests/test_b22_p5.py",
   "tests/test_b22_p6.py",
   "tests/test_b22_p7.py",
   "tests/test_b22_p8.py",
   "tests/test_b23_p6_scale.py",
   "tests/test_beta12_portable.py",
   "tests/test_beta13_numbers.py",
   "tests/test_beta14_portable.py",
   "tests/test_beta17_corrections.py",
   "tests/test_beta3_authoring.py",
   "tests/test_beta4_freshness.py",
   "tests/test_beta4_portable.py",
   "tests/test_beta4_revision_origin.py",
   "tests/test_beta_command_ingress.py",
   "tests/test_beta_completion_context.py",
   "tests/test_beta_media.py",
   "tests/test_beta_media_import.py",
   "tests/test_beta_openings.py",
   "tests/test_beta_sleep.py",
   "tests/test_beta_sleep_boundaries.py",
   "tests/test_catalog_care.py",
   "tests/test_catalog_cleanup.py",
   "tests/test_catalog_playback.py",
   "tests/test_catalog_scope.py",
   "tests/test_catalog_scope_conflicts.py",
   "tests/test_catalog_title_keys.py",
   "tests/test_declarative.py",
   "tests/test_delta_review.py",
   "tests/test_managed_catalog.py",
   "tests/test_multibucket.py",
   "tests/test_phase1a.py",
   "tests/test_portable.py",
   "tests/test_presence_templates.py",
   "tests/test_remediation.py",
   "tests/test_restore.py",
   "tests/test_runtime.py",
   "tests/test_time_templates.py",
   "tests/test_title_sync.py",
   "tests/test_title_sync_pages.py",
   "tests/test_workbench_api.py"
  ]
 },
 "api": {
  "paths": [
   "src/core_contracts/api.py",
   "src/core_contracts/security.py"
  ],
  "tests": [
   "tests/test_alpha4_acceptance.py",
   "tests/test_alpha4_delta2.py",
   "tests/test_alpha4_review.py",
   "tests/test_alpha6.py",
   "tests/test_alpha6_ux12.py",
   "tests/test_alpha7_discovery.py",
   "tests/test_alpha7_manual_restart.py",
   "tests/test_alpha7_source_migration.py",
   "tests/test_alpha7_version_metadata.py",
   "tests/test_alpha9_context.py",
   "tests/test_alpha9_editorial.py",
   "tests/test_alpha9_manual_source.py",
   "tests/test_b20_a1_repro.py",
   "tests/test_b22_p1.py",
   "tests/test_b22_p10.py",
   "tests/test_b22_p12.py",
   "tests/test_beta3_authoring.py",
   "tests/test_beta4_portable.py",
   "tests/test_beta_media_import.py",
   "tests/test_catalog_cleanup.py",
   "tests/test_catalog_scope.py",
   "tests/test_delta_review.py",
   "tests/test_phase1a.py",
   "tests/test_remediation.py",
   "tests/test_retention.py",
   "tests/test_runtime_version.py",
   "tests/test_setup.py",
   "tests/test_startup_lifecycle.py",
   "tests/test_title_sync.py",
   "tests/test_title_sync_pages.py",
   "tests/test_transports.py",
   "tests/test_workbench_api.py"
  ]
 },
 "adapters": {
  "paths": [
   "src/core_contracts/adapters/ha.py",
   "src/core_contracts/adapters/mqtt.py",
   "src/core_contracts/ha_projection.py",
   "src/core_contracts/mqtt_restore.py",
   "src/core_contracts/service_jobs.py"
  ],
  "tests": [
   "tests/test_alpha4_review.py",
   "tests/test_alpha6.py",
   "tests/test_alpha6_ux12.py",
   "tests/test_alpha7_discovery.py",
   "tests/test_alpha7_post1.py",
   "tests/test_alpha9_calendar.py",
   "tests/test_alpha9_context.py",
   "tests/test_alpha9_notifications.py",
   "tests/test_alpha9_refresh.py",
   "tests/test_alpha9_sensor_origin.py",
   "tests/test_alpha9_service_channel.py",
   "tests/test_b20_a1_repro.py",
   "tests/test_b22_p1.py",
   "tests/test_b22_p11.py",
   "tests/test_b22_p12.py",
   "tests/test_b22_p6.py",
   "tests/test_b22_p7.py",
   "tests/test_b22_p8.py",
   "tests/test_b23_coverage.py",
   "tests/test_b23_p6_scale.py",
   "tests/test_b23_projection.py",
   "tests/test_beta14_observations.py",
   "tests/test_beta4_adapters.py",
   "tests/test_beta4_freshness.py",
   "tests/test_beta4_revision_origin.py",
   "tests/test_beta5_projection.py",
   "tests/test_beta5_retained.py",
   "tests/test_beta_command_ingress.py",
   "tests/test_beta_media.py",
   "tests/test_beta_openings.py",
   "tests/test_catalog_playback.py",
   "tests/test_catalog_scope.py",
   "tests/test_delta_review.py",
   "tests/test_device_timestamp.py",
   "tests/test_ha_projection_reconnect.py",
   "tests/test_ha_request_ids.py",
   "tests/test_managed_catalog.py",
   "tests/test_measurement_ordering.py",
   "tests/test_mqtt_integration.py",
   "tests/test_phase1a.py",
   "tests/test_remediation.py",
   "tests/test_startup_lifecycle.py",
   "tests/test_transports.py",
   "tests/test_workbench_api.py"
  ]
 },
 "management": {
  "paths": [
   "src/core_contracts/authoring.py",
   "src/core_contracts/capabilities.py",
   "src/core_contracts/editorial.py",
   "src/core_contracts/management.py",
   "src/core_contracts/manual_restart.py",
   "src/core_contracts/portable.py",
   "src/core_contracts/provision.py",
   "src/core_contracts/reconfiguration.py",
   "src/core_contracts/revision_plan.py",
   "src/core_contracts/setup.py",
   "src/core_contracts/validation.py"
  ],
  "tests": [
   "tests/test_alpha4_acceptance.py",
   "tests/test_alpha4_delta2.py",
   "tests/test_alpha4_review.py",
   "tests/test_alpha6.py",
   "tests/test_alpha6_ux12.py",
   "tests/test_alpha7_discovery.py",
   "tests/test_alpha7_manual_restart.py",
   "tests/test_alpha7_post1.py",
   "tests/test_alpha7_source_migration.py",
   "tests/test_alpha7_version_metadata.py",
   "tests/test_alpha9_calendar.py",
   "tests/test_alpha9_deadlines.py",
   "tests/test_alpha9_editorial.py",
   "tests/test_alpha9_manual_source.py",
   "tests/test_alpha9_notifications.py",
   "tests/test_alpha9_refresh.py",
   "tests/test_alpha9_sensor_origin.py",
   "tests/test_b20_a1_repro.py",
   "tests/test_b22_p1.py",
   "tests/test_b22_p11.py",
   "tests/test_b22_p12.py",
   "tests/test_b22_p2.py",
   "tests/test_b22_p6.py",
   "tests/test_b22_p7.py",
   "tests/test_b22_p8.py",
   "tests/test_b23_coverage.py",
   "tests/test_b23_management.py",
   "tests/test_b23_p6_scale.py",
   "tests/test_b23_validation.py",
   "tests/test_beta12_portable.py",
   "tests/test_beta13_numbers.py",
   "tests/test_beta14_observations.py",
   "tests/test_beta14_portable.py",
   "tests/test_beta17_corrections.py",
   "tests/test_beta3_authoring.py",
   "tests/test_beta4_adapters.py",
   "tests/test_beta4_freshness.py",
   "tests/test_beta4_portable.py",
   "tests/test_beta4_revision_origin.py",
   "tests/test_beta5_projection.py",
   "tests/test_beta5_retained.py",
   "tests/test_beta_command_ingress.py",
   "tests/test_beta_completion_context.py",
   "tests/test_beta_media.py",
   "tests/test_beta_media_import.py",
   "tests/test_beta_openings.py",
   "tests/test_beta_sleep.py",
   "tests/test_beta_sleep_boundaries.py",
   "tests/test_bootstrap.py",
   "tests/test_calendar_inputs.py",
   "tests/test_catalog_cleanup.py",
   "tests/test_catalog_playback.py",
   "tests/test_catalog_scope.py",
   "tests/test_catalog_scope_conflicts.py",
   "tests/test_completion_templates.py",
   "tests/test_declarative.py",
   "tests/test_delta_review.py",
   "tests/test_first_usable.py",
   "tests/test_ha_freshness.py",
   "tests/test_ha_projection_reconnect.py",
   "tests/test_ha_request_ids.py",
   "tests/test_instance_parameters.py",
   "tests/test_managed_catalog.py",
   "tests/test_partial_evaluation.py",
   "tests/test_phase1a.py",
   "tests/test_portable.py",
   "tests/test_presence_templates.py",
   "tests/test_provision.py",
   "tests/test_remediation.py",
   "tests/test_restore.py",
   "tests/test_retention.py",
   "tests/test_runtime.py",
   "tests/test_setup.py",
   "tests/test_startup_lifecycle.py",
   "tests/test_title_reader.py",
   "tests/test_title_sync_pages.py",
   "tests/test_transports.py",
   "tests/test_version_lifecycle.py",
   "tests/test_workbench_api.py"
  ]
 },
 "tooling": {
  "paths": [
   "src/core_contracts/apple_tv_package.py",
   "src/core_contracts/fact_package.py",
   "src/core_contracts/stash_package.py"
  ],
  "tests": [
   "tests/test_b22_p6.py",
   "tests/test_b22_p7.py",
   "tests/test_b22_p8.py"
  ]
 },
 "traber": {
  "paths": [
   "custom_components/traber/",
   "tests/ha_kernel/",
   "src/core_contracts/ha_projection.py"
  ],
  "tests": [
   "tests/test_b23_projection.py",
   "tests/test_beta5_projection.py",
   "tests/test_bridge_kernel.py",
   "tests/test_ha_projection_reconnect.py"
  ]
 },
 "release": {
  "paths": [
   "dev/",
   ".github/",
   "wuschel/",
   "repository.yaml",
   "hacs.json"
  ],
  "tests": [
   "tests/test_beta14_ci.py",
   "tests/test_beta5_release.py",
   "tests/test_beta5_store_offer.py",
   "tests/test_herd_assets.py",
   "tests/test_image_distribution.py",
   "tests/test_version_lines.py",
   "tests/test_workbench_ci_lifecycle.py"
  ]
 },
 "store": {
  "paths": [],
  "tests": [
   "tests/test_b23_postgres.py",
   "tests/test_backup.py",
   "tests/test_beta3_postgres.py",
   "tests/test_beta4_postgres.py",
   "tests/test_beta_persistence.py",
   "tests/test_postgres.py",
   "tests/test_retention_postgres.py",
   "tests/test_setup_postgres.py"
  ]
 },
 "load": {
  "paths": [],
  "tests": [
   "tests/test_b23_acceptance.py",
   "tests/test_b23_p6.py",
   "tests/test_b23_p7.py"
  ]
 }
}
```

## Nur-Kern-Testdateien

`test_alpha10_runbook.py`, `test_alpha8_platform.py`, `test_configuration_completeness.py`, `test_core.py`, `test_named_formulas.py`, `test_operation_timing.py`, `test_text_rules.py`


## Umgesetzt in Schritt 5 (#119)

Die Kartuschenpfade zeigen auf die vollständigen Ordner unter
`src/core_contracts/extensions/`: `catalog/`, `tc_migration/`, `wake/` und
`openings/`. Die Testzuordnung bleibt unverändert. Neue Formatmodelle in
`rules/` und Änderungen an `slots`, `declarative`, `producer` oder der
allgemeinen API sind weiterhin Kernänderungen: `--changed` meldet dafür
konservativ `ALL`; lokal werden die betroffenen Modul- und Bereichstests
gewählt, die vollständige Suite läuft in CI.

Der Replay-Test behält alle Testkörper und Referenzen; ausschließlich sein
Import von `managed_catalog.head` folgt dem neuen Modulort. Bestehende Tests
ändern sonst nur Importe und Modulpfade, einschließlich der exakten erlaubten
Openings-Datei in der Packagingprüfung und der Wake-Pfade im Bereichsselektor.
Es werden keine Aussagen, Schwellen oder ausgewählten Testdateien gelockert.

## Neumessung und CI in Schritt 6 (#120)

Der JSON-Block oben ist die historische Messung vor dem Umbau. Die aktuelle
ausführbare Zuordnung steht in `tests/areas.json`; die geprüften Quellpfade
bleiben gleich. `dev/area_map.py` misst mit Coverage-C-Tracer und einem Kontext
je Test einschließlich Fixture-Aufbau und -Abbau. Nur Funktionskörper zählen.
Eine Zeile ist Grundrauschen, wenn sie mindestens die Hälfte der Tests erreicht,
die `Runtime.start` ausführen. Nach dessen Abzug zählen pro Bereich und Testdatei
mindestens fünf verschiedene Funktionszeilen, auch über mehrere Testfälle hinweg.
Sammlungs-/Importkontexte zählen nicht. Tests ohne gemessene Funktionszeilen und
die Umgebungsgruppen `store`/`load` behalten ihre explizite Zuordnung; der Bericht
weist diese Fälle aus. Ohne gestartete Zentrale oder bei fehlgeschlagener Suite
wird die Zuordnung nicht ersetzt.

Manuell: CI-Workflow `ci.yml` mit `area_map=true` auf dem zu messenden Branch
starten. Nur dieser explizite `workflow_dispatch` führt den eigenen Job
`area-map` mit PostgreSQL und Docker-MQTT aus. Artefakt `test-areas` enthält
`areas.json`, `areas.measurement.json` und die gemessene Commit-ID.
Die Datei wird geprüft und in den Branch übernommen, niemals automatisch gepusht.

CI verwendet bei vollständig zugeordneten Backend-Bereichsänderungen die
Vereinigung der Testdateien plus `tests/test_replay.py`, Ruff/Format, Mypy strict,
Importgrenzen und Abhängigkeitsprüfung. Image-/Wheel-Bau, Leistungsproben und
Workbench-E2E entfallen in diesem Fall. Gemeinsamer Kern, unbekannte Pfade und
Änderungen an Bau-/Prüfregeln lösen die volle Prüfung aus. Traber und getrennte
Testumgebungen werden nicht durch die Bereichsauswahl verkürzt. Die bisherigen
Frontend-/Dokumentationsregeln gelten weiter. PRs nach `main`, Main-Pushes und
Release-Prüfungen laufen immer vollständig; der manuelle Messjob ist kein
Release-Nachweis.
