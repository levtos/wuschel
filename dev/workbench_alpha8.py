"""Alpha 8 review fixture: real engine/API, synthetic values, no external connection."""

from copy import deepcopy

from workbench_post1 import registry as previous_registry
from workbench_post1 import seed as previous_seed

from core_contracts import wiring  # noqa: F401
from core_contracts.evidence import Observation
from core_contracts.runtime import Runtime
from core_contracts.testing.authoring import activate_draft


def registry():
    config = previous_registry()
    number = {"type": "number", "unit": "°C", "required": True, "physical_state": False}
    text = {"type": "text", "required": True, "physical_state": False}
    boolean = {"type": "bool", "required": True, "physical_state": False}
    config["history_retention"] = {"raw_days": 1, "rollup_days": 7}
    config["definitions"].extend(
        [
            {
                "type_id": "vertrag.temperaturbereich",
                "type_version": 1,
                "language_version": 2,
                "display_name": "Temperaturbereich",
                "inputs": [{"name": "temperatur", "schema": number}],
                "parameter_schema": {
                    "untere_grenze": {
                        "schema": number,
                        "minimum": 5,
                        "maximum": 35,
                        "label": "Untere Temperaturgrenze",
                    },
                    "obere_grenze": {
                        "schema": number,
                        "minimum": 5,
                        "maximum": 35,
                        "label": "Obere Temperaturgrenze",
                    },
                },
                "parameter_relations": [
                    {"left": "untere_grenze", "comparison": "lt", "right": "obere_grenze"}
                ],
                "schema": {
                    "fields": {
                        "value": {
                            **text,
                            "type": "enum",
                            "value_catalog": ["kühl", "angenehm", "warm"],
                        }
                    },
                    "fixture": False,
                },
                "outputs": {
                    "value": {
                        "op": "bucket",
                        "id": "temperaturbereich",
                        "arg": {"op": "input", "name": "temperatur"},
                        "classes": ["kühl", "angenehm", "warm"],
                        "schema": {
                            **text,
                            "type": "enum",
                            "value_catalog": ["kühl", "angenehm", "warm"],
                        },
                        "boundaries": [
                            {"at": {"op": "parameter", "name": name}}
                            for name in ("untere_grenze", "obere_grenze")
                        ],
                        "initial": {"unknown": True},
                    }
                },
            },
            {
                "type_id": "vertrag.zuordnungshinweis",
                "type_version": 1,
                "language_version": 2,
                "display_name": "Wert mit fehlender Zuordnung",
                "inputs": [{"name": "wert", "schema": text}],
                "schema": {"fields": {"value": text}, "fixture": False},
                "outputs": {
                    "value": {
                        "op": "configuration_hint",
                        "arg": {"op": "input", "name": "wert"},
                        "when": {"op": "literal", "schema": boolean, "value": True},
                        "code": "unassigned",
                        "message": "Für diesen Testwert fehlt noch die Zuordnung.",
                    }
                },
            },
        ]
    )
    config["sources"].append(
        {
            "source_id": "source.testwert",
            "source_type": "text",
            "source_origin": "local",
            "display_name": "Synthetischer Testwert",
            "dependency_id": "dep.test",
            "physical_source_key": "synthetic.testwert",
            "value_schema": text,
            "freshness": {"mode": "event_stateful", "future_tolerance_s": 1},
        }
    )
    config["bindings"].append(
        {
            "binding_id": "binding.testwert",
            "source_id": "source.testwert",
            "adapter": {"kind": "ha_state", "ha_entity_id": "sensor.synthetic_testwert"},
            "transform": {"kind": "type_cast", "target": "text"},
        }
    )
    config["contracts"].extend(
        [
            {
                "contract_id": "test.temperaturbereich",
                "display_name": "Temperaturbereich im Testzimmer",
                "type_id": "vertrag.temperaturbereich",
                "type_version": 1,
                "enabled": True,
                "inputs": [{"name": "temperatur", "ref": "source.temperature"}],
                "parameters": {"untere_grenze": 19, "obere_grenze": 23},
            },
            {
                "contract_id": "test.zuordnung",
                "display_name": "Testwert ohne Zuordnung",
                "type_id": "vertrag.zuordnungshinweis",
                "type_version": 1,
                "enabled": True,
                "inputs": [{"name": "wert", "ref": "source.testwert"}],
                "parameters": {},
            },
        ]
    )
    return config


async def seed(runtime: Runtime):
    await previous_seed(runtime)
    config = registry()
    draft = await runtime.submit("draft_create", {"config": config})
    await activate_draft(runtime, draft, expected_active_revision=1)
    now = runtime.clock.now_utc()
    await runtime.submit(
        "observation",
        Observation(
            source_id="source.testwert",
            binding_id="binding.testwert",
            adapter="ha_state",
            value_raw="noch nicht zugeordnet",
            observation_kind="report",
            ha_last_reported=now,
            received_at=now,
            epoch_id=runtime.state.epoch_id,
            ingest_seq=10,
            registry_revision=2,
            reference_version=2,
        ),
    )
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    await runtime.submit(
        "draft_create",
        {"display_name": "Wuschel – Einstellungen prüfen", "config": deepcopy(config)},
    )
