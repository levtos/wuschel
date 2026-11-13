"""Synthetic Alpha 10 screenshots against the real serial writer; no external systems."""

from uuid import uuid4

from core_contracts import wiring  # noqa: F401
from core_contracts.runtime import Runtime
from core_contracts.testing.authoring import activate_draft


async def seed(runtime: Runtime) -> None:
    config = {
        "config_schema_version": 3,
        "fingerprint_version": 3,
        "history_retention": {
            "raw_days": 7,
            "rollup_days": 730,
            "batch_size": 1000,
            "interval_s": 60,
        },
        "dependencies": [{"dependency_id": "example.mqtt", "kind": "mqtt", "monitor": "internal"}],
        "sources": [
            {
                "source_id": "source.example_title",
                "display_name": "Synthetischer Titel",
                "source_type": "text",
                "source_origin": "local",
                "dependency_id": "example.mqtt",
                "physical_source_key": "synthetic.title",
                "freshness": {"mode": "event_stateful", "future_tolerance_s": 1},
            }
        ],
        "bindings": [
            {
                "binding_id": "binding.example_title",
                "source_id": "source.example_title",
                "adapter": {"kind": "mqtt", "mqtt_topic": "wuschel/synthetic/title"},
            }
        ],
        "contracts": [],
        "definitions": [],
        "catalogs": [
            {
                "catalog_id": "example.titles",
                "kind": "managed",
                "version": 1,
                "identity_rule": {
                    "version": 1,
                    "fields": ["scope", "media_type", "signal_type", "normalized_key"],
                    "normalizer": "normalize_key",
                },
                "classification_schema": {"type": "enum", "values": ["other", "example"]},
                "unassigned_value": "other",
                "master_depth": 1,
            }
        ],
    }
    draft = await runtime.submit("draft_create", {"config": config})
    await activate_draft(runtime, draft, expected_active_revision=0)
    await runtime.submit(
        "catalog_write",
        {
            "request": {
                "catalog_id": "example.titles",
                "definition_version": 1,
                "expected_active_revision": 1,
                "expected_content_revision": 0,
                "request_id": str(uuid4()),
                "reason": "Synthetischer Bildschirmnachweis",
                "changes": [
                    {
                        "scope": "example",
                        "media_type": "music",
                        "signal_type": "title",
                        "key": "Synthetischer Beispieltitel",
                        "classification": "example",
                    }
                ],
            }
        },
    )
    runtime.mqtt_mode = "disabled"
    runtime.mqtt_external_settings_present = True
