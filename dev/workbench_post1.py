"""Small deterministic screenshot fixture for B1/B4/B6/B7/B8, never real HA."""

from copy import deepcopy

from core_contracts import wiring  # noqa: F401
from core_contracts.evidence import Observation
from core_contracts.runtime import Runtime
from core_contracts.testing.authoring import activate_draft


def registry():
    numeric = {"type": "number", "required": True, "physical_state": False}
    specs = [
        ("temperature", "Temperatur im Testzimmer", "numeric", "°C", "periodic_ttl", 900),
        ("minutes", "Zeit seit der Meldung", "numeric", "min", "periodic_ttl", 900),
        ("window", "Fenster im Testzimmer", "boolean", None, "report_heartbeat", 15),
    ]
    sources, bindings, contracts = [], [], []
    for key, name, kind, unit, mode, interval in specs:
        schema = (
            {**numeric, "unit": unit}
            if kind == "numeric"
            else {"type": "bool", "required": True, "physical_state": False}
        )
        sources.append(
            {
                "source_id": f"source.{key}",
                "display_name": name,
                "source_type": kind,
                "source_origin": "local",
                "dependency_id": "dep.test",
                "physical_source_key": f"synthetic.{key}",
                "value_schema": schema,
                "freshness": {"mode": mode, "interval_s": interval, "future_tolerance_s": 1},
            }
        )
        bindings.append(
            {
                "binding_id": f"binding.{key}",
                "source_id": f"source.{key}",
                "adapter": {"kind": "ha_state", "ha_entity_id": f"sensor.synthetic_{key}"},
                "transform": {"kind": "type_cast", "target": schema["type"]},
            }
        )
        contracts.append(
            {
                "contract_id": f"test.{key}",
                "display_name": name,
                "type_id": "test.echo",
                "type_version": 1,
                "enabled": True,
                "inputs": [{"name": "a", "ref": f"source.{key}"}],
                "parameters": {},
            }
        )
    contracts.append(
        {
            "contract_id": "test.optional",
            "display_name": "Fensterzeit – Zusatzwert fehlt",
            "type_id": "test.optional",
            "type_version": 1,
            "enabled": True,
            "inputs": [{"name": "a", "ref": "source.window"}],
            "parameters": {},
        }
    )
    boolean = {"type": "bool", "required": True, "physical_state": False}
    return {
        "config_schema_version": 1,
        "fingerprint_version": 2,
        "catalogs": [],
        "dependencies": [{"dependency_id": "dep.test", "kind": "ha", "monitor": "internal"}],
        "sources": sources,
        "bindings": bindings,
        "contracts": contracts,
        "definitions": [
            {
                "type_id": "test.optional",
                "type_version": 1,
                "inputs": [{"name": "a", "schema": boolean}],
                "schema": {
                    "fields": {"bereit": boolean, "zusatzwert": {**boolean, "required": False}},
                    "available_projection": ["bereit"],
                },
                "outputs": {
                    "bereit": {"op": "literal", "value": True, "schema": boolean},
                    "zusatzwert": {"op": "input", "name": "a"},
                },
            }
        ],
    }


async def seed(runtime: Runtime):
    config = registry()
    draft = await runtime.submit("draft_create", {"config": config})
    await activate_draft(runtime, draft, expected_active_revision=0)
    await runtime.submit("bridge_state", ("connected", "connected"))
    runtime.first_snapshot = True
    for seq, (key, value) in enumerate(
        [("temperature", 21.278315), ("minutes", 14.278315), ("window", False)], 1
    ):
        now = runtime.clock.now_utc()
        await runtime.submit(
            "observation",
            Observation(
                source_id=f"source.{key}",
                binding_id=f"binding.{key}",
                adapter="ha_state",
                value_raw=value,
                observation_kind="report",
                ha_last_reported=now,
                received_at=now,
                epoch_id=runtime.state.epoch_id,
                ingest_seq=seq,
                registry_revision=1,
                reference_version=2,
            ),
        )
    runtime.clock.advance(5)
    now = runtime.clock.now_utc()
    await runtime.submit(
        "observation",
        Observation(
            source_id="source.window",
            binding_id="binding.window",
            adapter="ha_state",
            value_raw=True,
            observation_kind="report",
            ha_last_reported=now,
            received_at=now,
            epoch_id=runtime.state.epoch_id,
            ingest_seq=4,
            registry_revision=1,
            reference_version=2,
        ),
    )
    runtime.clock.advance(60)
    await runtime.submit("tick")
    await runtime.submit(
        "draft_create", {"display_name": "Änderungen im Test", "config": deepcopy(config)}
    )
