"""Synthetic Alpha 9 review surfaces, solely in the throw-away memory store."""

from workbench_alpha8 import registry as alpha8_registry
from workbench_alpha8 import seed as alpha8_seed

from core_contracts import wiring  # noqa: F401
from core_contracts.runtime import Runtime
from core_contracts.testing.authoring import activate_draft


async def seed(runtime: Runtime):
    await alpha8_seed(runtime)
    runtime.require_history_retention = True
    config = alpha8_registry()
    config["config_schema_version"] = 2
    config.pop("history_retention", None)
    config["dependencies"].append(
        {"dependency_id": "dep.manual", "kind": "manual", "monitor": "internal"}
    )
    config["sources"].append(
        {
            "source_id": "source.angabe",
            "display_name": "Eigene Temperaturangabe im Testzimmer",
            "source_type": "numeric",
            "source_origin": "local",
            "origin": "manual",
            "dependency_id": "dep.manual",
            "physical_source_key": "manual.review",
            "value_schema": {
                "type": "number",
                "required": True,
                "physical_state": False,
                "unit": "°C",
            },
            "freshness": {"mode": "periodic_ttl", "interval_s": 3600, "future_tolerance_s": 0},
        }
    )
    config["bindings"].append(
        {
            "binding_id": "binding.angabe",
            "source_id": "source.angabe",
            "adapter": {"kind": "manual"},
        }
    )
    config["contracts"].append(
        {
            "contract_id": "test.angabe",
            "display_name": "Eigene Angabe im Testzimmer",
            "type_id": "test.echo",
            "type_version": 1,
            "enabled": True,
            "inputs": [{"name": "a", "ref": "source.angabe"}],
            "parameters": {},
        }
    )
    draft = await runtime.submit("draft_create", {"config": config})
    await activate_draft(runtime, draft, expected_active_revision=2)
    for contract in config["contracts"]:
        await runtime.submit(
            "editorial_put",
            {
                "request": {
                    "object": {"kind": "contract", "id": contract["contract_id"]},
                    "expected_editorial_version": 0,
                    "category": "climate",
                    "room": {"mode": "manual", "name": "Testzimmer", "icon": "mdi:home"},
                },
                "_actor": {"kind": "internal", "role": "admin"},
            },
        )
    await runtime.submit(
        "draft_create", {"display_name": "Alpha 9 – Einstellungen prüfen", "config": config}
    )
