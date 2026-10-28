"""Issue 80 Z-01–Z-11: real activation and stored b15-to-current startup."""

from copy import deepcopy
from datetime import datetime

import pytest

from core_contracts import registry as registry_module
from core_contracts.freshness_templates import upgrade_document
from core_contracts.reconfiguration import context_effects
from core_contracts.registry import legacy_context_compatibility, storage_document
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry

from .conftest import activate, activate_draft
from .test_beta_media import TEMPLATES as MEDIA
from .test_beta_media import command as media_command
from .test_beta_media import configuration as media_configuration
from .test_beta_media import observe as media_observe
from .test_beta_sleep import TEMPLATES as SLEEP
from .test_beta_sleep import command, machine, observe, ready
from .test_declarative import definition, literal, observation, registry, schema
from .test_phase1a import field


async def test_catalog_import_preview_matches_actual_selective_context_reset(runtime, config):
    from core_contracts.portable import payload_digest

    from .test_managed_catalog import change, configured, lookup_rule
    from .test_portable import export, plan, stage

    doc = configured(config)
    doc.update(dependencies=[], sources=[], bindings=[], contracts=[], definitions=[])
    data = definition()
    data.update(type_id="test.catalog_history", language_version=4, inputs=[])
    data["outputs"] = {
        name: dict(
            op="temporal",
            id=name,
            operation="stable_for",
            duration_s=60,
            args=[
                dict(
                    op="compare",
                    comparison="eq",
                    left=lookup_rule(key),
                    right={
                        **literal("other", "enum"),
                        "schema": schema("enum", value_catalog=["other", "example"]),
                    },
                )
            ],
        )
        for name, key in [("value", "Synthetic First"), ("other", "Synthetic Second")]
    }
    data["schema"]["fields"]["other"] = schema()
    doc["definitions"] = [data]
    doc["contracts"] = [
        dict(
            contract_id="test.catalog_history",
            type_id=data["type_id"],
            type_version=1,
            enabled=True,
            parameters={},
            inputs=[],
        )
    ]
    await activate(runtime, upgrade_document(doc))
    before = deepcopy(runtime.state.tables["node_state"]["test.catalog_history"]["rules"])
    runtime.clock.advance(10)
    package = await export(runtime)
    package["catalog_contents"][0]["entries"].append(change())
    package["manifest"]["payload_digest"] = payload_digest(package)
    preview = await plan(runtime, package)
    assert preview["context_effects"] == [
        dict(
            contract_id="test.catalog_history",
            storage="rules",
            slot="value",
            case="b",
            reason="catalog_classification_changed",
        )
    ]
    draft = await stage(runtime, package, preview)
    comparison = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    assert comparison["context_effects"] == preview["context_effects"]
    await activate_draft(runtime, draft, expected_active_revision=1)
    after = runtime.state.tables["node_state"]["test.catalog_history"]["rules"]
    assert after["other"] == before["other"]
    assert after["value"]["temporal"]["since_at"] is None
    assert before["value"]["temporal"]["since_at"] is not None


def temporal_chain(config, operation):
    data = definition()
    kind = "number" if operation == "delta" else "bool"
    output_kind = "number" if operation in {"age", "delta"} else "bool"
    data["inputs"][0]["schema"] = schema(kind)
    data["type_id"] = "test.history"
    data["inputs"].append(dict(name="b", schema=schema(kind)))
    data["outputs"] = {
        name: dict(
            op="temporal",
            id=name,
            operation=operation,
            duration_s=60,
            args=[dict(op="input", name=name)]
            + ([literal(0, "number")] if operation == "delta" else []),
        )
        for name in ("a", "b")
    }
    data["schema"]["fields"] = {name: schema(output_kind) for name in ("a", "b")}
    data["schema"]["available_projection"] = ["a"]
    doc = registry(config, data)
    upstream = definition()
    upstream["type_id"] = "test.upstream"
    upstream["inputs"][0]["schema"] = schema(kind)
    upstream["schema"]["fields"]["value"] = schema(kind)
    doc["definitions"].append(upstream)
    doc["contracts"][0]["inputs"] = [
        dict(name="a", ref="fixture.upstream.value"),
        dict(name="b", ref="source.a"),
    ]
    doc["contracts"].append(
        dict(
            contract_id="fixture.upstream",
            type_id="test.upstream",
            type_version=1,
            enabled=True,
            inputs=[dict(name="a", ref="source.a")],
            parameters={},
        )
    )
    return upgrade_document(doc)


@pytest.mark.parametrize("operation", ["stable_for", "dwell", "grace", "edge", "delta", "age"])
@pytest.mark.parametrize("case", ["a", "b", "c", "d"])
async def test_temporal_matrix_and_independent_slot(runtime, config, operation, case):
    doc = temporal_chain(config, operation)
    await activate(runtime, doc)
    await runtime.submit("observation", observation(runtime, 15 if operation == "delta" else True))
    runtime.clock.advance(10)
    before = deepcopy(runtime.state.tables["node_state"]["fixture.echo"])
    observations = deepcopy(runtime.state.tables["source_observation_current"])
    changed = deepcopy(doc)
    if case == "a":
        changed["contracts"][0]["display_name"] = "Another label"
    elif case == "b":
        # Same current result, different derivation: old history cannot prove continuity.
        changed["definitions"][1]["type_version"] = 2
        if operation != "delta":
            changed["definitions"][1]["outputs"]["value"] = dict(
                op="and", args=[dict(op="input", name="a"), literal(True)]
            )
        changed["contracts"][1]["type_version"] = 2
    elif case == "c":
        changed["definitions"][0]["type_version"] = 2
        changed["contracts"][0]["type_version"] = 2
    else:
        await runtime.submit("observation", observation(runtime, None))
    draft = await runtime.submit("draft_create", {"config": changed})
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    expected = context_effects(
        runtime.state,
        runtime.config,
        registry_module.validate(changed, runtime.types),
        runtime.types,
        runtime.latches,
    )
    assert preview["context_effects"] == expected
    await activate_draft(runtime, draft)
    node = runtime.state.tables["node_state"]["fixture.echo"]
    if case in {"a", "b"}:
        assert runtime.state.tables["source_observation_current"] == observations
        assert node["rules"]["b"]["temporal"] == before["rules"]["b"]["temporal"]
    if case == "b":
        assert [(e["storage"], e["slot"]) for e in expected] == [("rules", "a")]
        if operation in {"stable_for", "dwell"}:
            assert (
                datetime.fromisoformat(node["rules"]["a"]["temporal"]["since_at"])
                == runtime.clock.now_utc()
            )
            assert (
                node["rules"]["a"]["temporal"]["since_at"]
                != before["rules"]["a"]["temporal"]["since_at"]
            )
    if operation == "edge":
        assert field(runtime, key="a")["value"] is not True
    if case == "d" and operation in {"stable_for", "dwell"}:
        assert node["rules"]["a"]["temporal"]["since_at"] is None
    if case == "c":
        assert any(e["case"] == "c" for e in expected)


async def pc_chain(runtime, config):
    await ready(runtime, config)
    doc = storage_document(runtime.config)
    pc = next(deepcopy(d) for d in SLEEP if d["type_id"] == "device.active")
    doc["definitions"].append(pc)
    # Existing portable power template has a scalar numeric input and thresholds.
    doc["sources"].append(
        dict(
            source_id="source.power",
            source_type="numeric",
            source_origin="local",
            origin="manual",
            dependency_id="dep.manual",
            physical_source_key="synthetic.power",
            value_schema=pc["inputs"][0]["schema"],
            freshness_selection=dict(
                kind="own", rule=dict(mode="event_stateful", future_tolerance_s=0)
            ),
        )
    )
    doc["bindings"].append(
        dict(binding_id="binding.power", source_id="source.power", adapter=dict(kind="manual"))
    )
    doc["contracts"].append(
        dict(
            contract_id="device.pc.power",
            type_id="device.active",
            type_version=1,
            enabled=True,
            parameters={"threshold_w": 100},
            inputs=[dict(name=pc["inputs"][0]["name"], ref="source.power")],
        )
    )
    next(i for i in doc["contracts"][0]["inputs"] if i["name"] == "pc")["ref"] = (
        "device.pc.power.value"
    )
    await activate(runtime, doc)
    await observe(runtime, "power", 0)
    await command(runtime, "start_pre_sleep")
    return doc


@pytest.mark.parametrize("case", ["a", "b", "c", "d"])
async def test_episode_captures_memory_and_absolute_deadline_matrix(runtime, config, case):
    doc = await pc_chain(runtime, config)
    assert (await command(runtime, "confirm_sleep"))["status"] == "accepted"
    before = deepcopy(machine(runtime))
    assert before["captures"]["first_sleep_at"]
    assert before["deadline"]
    changed = deepcopy(doc)
    if case == "a":
        changed["contracts"][-1]["display_name"] = "Synthetic label"
    elif case == "b":
        changed["contracts"][-1]["parameters"]["threshold_w"] = 200
    elif case == "c":
        changed["contracts"][0]["parameters"]["wake_settings"]["routine_duration_minutes"] = 61
    else:
        await observe(runtime, "power", None)
    runtime.clock.advance(30)
    draft = await runtime.submit("draft_create", {"config": changed})
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    assert any(
        e["contract_id"] == "fixture.sleep" and e["storage"] == "node"
        for e in preview["context_effects"]
    ) is (case == "c")
    await activate_draft(runtime, draft)
    after = runtime.state.tables["node_state"]["fixture.sleep"].get("machine")
    if case == "c":
        assert after is None or after["episode_id"] != before["episode_id"]
    else:
        assert after is not None
        for key in [
            "state",
            "episode_id",
            "episode_started_at",
            "since_at",
            "captures",
            "deadline",
            "absolute_at",
            "consumed_absolute",
        ]:
            assert after.get(key) == before.get(key)


async def test_real_pc_template_swap_preserves_sleep_deadline_without_wake(runtime, config):
    doc = await pc_chain(runtime, config)
    before = deepcopy(machine(runtime))
    runtime.clock.advance(60)
    pc = next(deepcopy(d) for d in MEDIA if d["type_id"] == "device.pc_state")
    doc["definitions"].append(pc)
    contract = doc["contracts"][-1]
    contract.update(
        type_id="device.pc_state",
        parameters=dict(host_grace_s=10, power_on_w=100, power_off_w=10),
        inputs=[dict(name="watts", ref="source.power"), dict(name="host", ref="source.pc")],
    )
    await activate(runtime, doc)
    after = machine(runtime)
    assert after["state"] == before["state"] == "pre_sleep"
    for name in ("episode_id", "episode_started_at", "since_at", "captures", "deadline"):
        assert after.get(name) == before.get(name)


@pytest.mark.parametrize("damaged", [False, True])
async def test_z11_verified_b15_start_keeps_state_or_honest_unknown(
    runtime, config, monkeypatch, damaged
):
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "context_compatibility", legacy_context_compatibility)
        await ready(runtime, config)
        doc = storage_document(runtime.config)
        doc["definitions"].append(
            next(deepcopy(d) for d in MEDIA if d["type_id"] == "presence.command_context")
        )
        for name in ("visitors", "date"):
            doc["contracts"].append(
                dict(
                    contract_id="fixture." + name,
                    type_id="presence.command_context",
                    type_version=1,
                    enabled=True,
                    parameters={},
                    inputs=[dict(name="sleep", ref="fixture.sleep.value")],
                )
            )
        await activate(runtime, doc)
        for name in ("visitors", "date"):
            await media_command(runtime, "start", contract="fixture." + name)
        await observe(runtime, "pc", True, kind="snapshot")
        await command(runtime, "start_pre_sleep")
    before = deepcopy(machine(runtime))
    assert (
        before["fingerprint"]
        == legacy_context_compatibility(runtime.config, runtime.types)["fixture.sleep"]
    )
    if damaged:
        state = runtime.state.clone()
        state.tables["node_state"]["fixture.sleep"]["fingerprint"] = "not-a-b15-proof"
        state.tables["node_state"]["fixture.sleep"].pop("machine")
        await runtime._commit(state)
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    try:
        if damaged:
            assert (
                restarted.snapshot()["contracts"]["fixture.sleep"]["fields"]["value"]["status"]
                == "unknown"
            )
        else:
            after = machine(restarted)
            for name in ("state", "episode_id", "deadline", "captures", "since_at"):
                assert after.get(name) == before.get(name)
            assert after["fingerprint"] != before["fingerprint"]
        for name in ("visitors", "date"):
            assert (
                restarted.snapshot()["contracts"]["fixture." + name]["fields"]["value"]["value"]
                is True
            )
    finally:
        await restarted.stop()


@pytest.mark.parametrize("sleep_value", ["awake", "sleep"])
async def test_visitors_and_date_apply_current_sleep_level_on_revision(
    runtime, config, sleep_value
):
    doc = media_configuration(config, "presence.command_context")
    upstream = definition(dict(op="input", name="a"))
    upstream["type_id"] = "test.sleep_view"
    upstream["inputs"][0]["schema"] = schema("text")
    upstream["schema"]["fields"]["value"] = schema("text")
    doc["definitions"].append(upstream)
    doc["contracts"][0]["inputs"][0]["ref"] = "fixture.sleep_view.value"
    doc["contracts"].append(
        dict(
            contract_id="fixture.sleep_view",
            type_id="test.sleep_view",
            type_version=1,
            enabled=True,
            inputs=[dict(name="a", ref="source.sleep")],
            parameters={},
        )
    )
    date = deepcopy(doc["contracts"][0])
    date["contract_id"] = "fixture.date"
    doc["contracts"].append(date)
    await activate(runtime, doc)
    await media_observe(runtime, "sleep", "awake")
    for key in ("fixture.media", "fixture.date"):
        assert (await media_command(runtime, "start", contract=key))["status"] == "accepted"
    changed = deepcopy(doc)
    changed["definitions"][-1]["type_version"] = 2
    changed["definitions"][-1]["outputs"]["value"] = literal(sleep_value, "text")
    changed["contracts"][1]["type_version"] = 2
    await activate(runtime, changed)
    for key in ("fixture.media", "fixture.date"):
        result = runtime.snapshot()["contracts"][key]["fields"]["value"]
        assert result["value"] is (sleep_value != "sleep")
        assert result["status"] == "valid"


@pytest.mark.parametrize("case", ["a", "b", "c", "d"])
async def test_volatile_latch_matrix_is_in_preview(runtime, config, case):
    doc = temporal_chain(config, "delta")
    data = doc["definitions"][0]
    data["schema"]["fields"] = {name: schema() for name in ("a", "b")}
    data["outputs"] = {
        name: dict(
            op="bucket", id=name, arg=dict(op="input", name=name), lower=10, upper=20, initial=False
        )
        for name in ("a", "b")
    }
    await activate(runtime, doc)
    await runtime.submit("observation", observation(runtime, 25))
    await runtime.submit("observation", observation(runtime, 15))
    assert field(runtime, key="a")["value"] is True
    changed = deepcopy(doc)
    if case == "b":
        changed["definitions"][1]["type_version"] = 2
        changed["contracts"][1]["type_version"] = 2
    elif case == "c":
        changed["definitions"][0]["type_version"] = 2
        changed["contracts"][0]["type_version"] = 2
    elif case == "d":
        await runtime.submit("observation", observation(runtime, None))
    else:
        changed["contracts"][0]["display_name"] = "Synthetic rename"
    draft = await runtime.submit("draft_create", {"config": changed})
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    reset = {e["slot"] for e in preview["context_effects"] if e["storage"] == "latch"}
    assert reset == ({"a"} if case == "b" else {"a", "b"} if case == "c" else set())
    await activate_draft(runtime, draft)
    if case in {"b", "c"}:
        assert field(runtime, key="a")["value"] is False
        assert field(runtime, key="b")["value"] is (case == "b")
    elif case == "a":
        assert field(runtime, key="a")["value"] is True
    else:
        assert field(runtime, key="a")["status"] == "unknown"
        assert runtime.latches["fixture.echo:a"].latch is False


@pytest.mark.parametrize("case", ["a", "b", "c", "d"])
async def test_event_match_basis_is_selective_without_revision_event(runtime, config, case):
    doc = temporal_chain(config, "edge")
    doc["definitions"][0]["language_version"] = 6
    doc["definitions"][0]["outputs"] = {
        name: dict(op="event_match", id=name, mode="changed", arg=dict(op="input", name=name))
        for name in ("a", "b")
    }
    await activate(runtime, doc)
    await runtime.submit("observation", observation(runtime, False))
    await runtime.submit("observation", observation(runtime, True))
    before = deepcopy(runtime.state.tables["node_state"]["fixture.echo"]["events"])
    changed = deepcopy(doc)
    if case == "b":
        changed["definitions"][1]["type_version"] = 2
        changed["contracts"][1]["type_version"] = 2
    elif case == "c":
        changed["definitions"][0]["type_version"] = 2
        changed["contracts"][0]["type_version"] = 2
    elif case == "d":
        await runtime.submit("observation", observation(runtime, None))
    else:
        changed["contracts"][0]["display_name"] = "Synthetic rename"
    draft = await runtime.submit("draft_create", {"config": changed})
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    assert {e["slot"] for e in preview["context_effects"] if e["storage"] == "events"} == (
        {"a"} if case == "b" else set()
    )
    await activate_draft(runtime, draft)
    assert field(runtime, key="a")["value"] is not True
    if case in {"a", "b"}:
        assert runtime.state.tables["node_state"]["fixture.echo"]["events"]["b"] == before["b"]
