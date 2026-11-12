"""Focused independent-review regressions, shared with real PostgreSQL tests."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.declarative import Evaluation, check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.engines import FORMULAS
from core_contracts.evidence import Observation
from core_contracts.history import HistoryQuery
from core_contracts.persistence import MemoryStore
from core_contracts.registry import RegistryConfig, storage_document
from core_contracts.resolver import Bucket, map_value
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import RegistryValidationError
from core_contracts.validation import validate_document as validate
from dev.fake_ha import FakeHA

from .conftest import activate
from .test_core import NOW
from .test_declarative import (
    definition,
    literal,
    machine_definition,
    registry,
    schema,
    value,
)
from .test_declarative import observation as base_observation
from .test_phase1a import add_source, contract, field
from .test_workbench_api import api_client


def observation(*args, ingest_seq=None, **kwargs):
    item = base_observation(*args, **kwargs)
    return item.model_copy(update={"ingest_seq": ingest_seq}) if ingest_seq is not None else item


async def exercise_latch_restore(runtime, config):
    data = definition(
        dict(
            op="bucket", id="latch", initial=False, lower=2, upper=5, arg=dict(op="input", name="a")
        )
    )
    data["inputs"] = [dict(name="a", schema=schema("number")), dict(name="b", schema=schema())]
    data["schema"]["fields"]["held"] = schema()
    data["outputs"]["held"] = dict(
        op="temporal",
        id="grace",
        operation="grace",
        duration_s=30,
        args=[dict(op="input", name="b")],
    )
    registry(config, data)
    add_source(config, "b")
    config["sources"][1]["source_type"] = "boolean"
    config["sources"][1]["value_schema"] = schema()
    config["sources"][0]["source_type"] = "numeric"
    config["sources"][0]["value_schema"] = schema("number")
    config["bindings"][0]["transform"] = dict(kind="type_cast", target="number")
    config["bindings"][1]["transform"] = dict(kind="type_cast", target="bool")
    config["contracts"][0]["inputs"].append(dict(name="b", ref="source.b"))
    await activate(runtime, config)
    baseline = Bucket(False, 2, 5)
    for seq, number in enumerate([6, 3], 1):
        runtime.clock.advance(1)
        await runtime.submit("observation", observation(runtime, number, ingest_seq=seq))
        assert (
            field(runtime)["value"]
            == baseline.evaluate(value(number), runtime.clock.now_utc()).value
        )
    await runtime.submit("observation", observation(runtime, True, "b", ingest_seq=3))
    runtime.clock.advance(1)
    await runtime.submit(
        "observation", observation(runtime, None, "b", availability="unavailable", ingest_seq=4)
    )
    assert field(runtime)["value"] is True
    assert field(runtime, key="held")["status"] == "held"
    return field(runtime, key="held")["held_until"]


async def test_bucket_restart_resets_only_latch(runtime, config):
    end = await exercise_latch_restore(runtime, config)
    # Also tolerate and erase the pre-review Alpha-4 persisted latch.
    runtime.store.state.tables["node_state"]["fixture.echo"]["rules"]["latch"] = {"latch": True}
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    try:
        await restarted.submit(
            "snapshot",
            [
                observation(restarted, 3, ingest_seq=5).model_copy(
                    update={"observation_kind": "snapshot"}
                )
            ],
        )
        assert field(restarted)["value"] is False
        assert field(restarted, key="held")["held_until"] == end
        assert "latch" not in restarted.state.tables["node_state"]["fixture.echo"]["rules"]
        restarted.clock.advance(31)
        await restarted.submit("tick")
        assert field(restarted, key="held")["status"] == "unknown"
    finally:
        await restarted.stop()


async def test_local_failure_does_not_poison_fusion_temporal_or_commands(
    runtime, config, monkeypatch
):
    broken = definition(
        dict(
            op="compare",
            comparison="gt",
            left=dict(op="formula", name="test.sum", version=1, args=[literal(2, "number")]),
            right=literal(0, "number"),
        )
    )
    broken["type_id"] = "test.broken"
    fusion = definition(
        dict(
            op="fusion",
            strategy="first_healthy",
            args=[dict(op="input", name="a"), dict(op="input", name="b")],
        )
    )
    fusion["type_id"] = "test.survivor"
    fusion["inputs"].append(dict(name="b", schema=schema()))
    temporal = definition(
        dict(
            op="temporal",
            id="dwell",
            operation="dwell",
            duration_s=2,
            args=[dict(op="input", name="a")],
        )
    )
    temporal["type_id"] = "test.wait"
    machine = machine_definition()
    machine["type_id"] = "test.guarded"
    machine["inputs"].append(dict(name="b", schema=schema()))
    machine["schema"]["fields"]["upstream"] = schema()
    machine["outputs"]["upstream"] = dict(op="input", name="b")
    registry(config, broken)
    config["definitions"] = [broken, fusion, temporal, machine]
    config["contracts"] = [
        contract("broken", "broken", ["source.a"]),
        contract("survivor", "survivor", ["fixture.broken", "source.a"]),
        contract("wait", "wait", ["fixture.survivor"]),
        contract("guarded", "guarded", ["fixture.survivor", "fixture.broken"]),
    ]
    await activate(runtime, config)
    monkeypatch.setitem(
        FORMULAS._items, ("test.sum", 1), Mock(side_effect=ArithmeticError("injected"))
    )
    await runtime.submit("observation", observation(runtime, True))
    assert field(runtime, "broken")["status"] == "unknown"
    survivor = field(runtime, "survivor")
    assert survivor["status"] == "valid" and survivor["value"] is True
    assert survivor["quality"] == "degraded"
    assert "partial_evidence" in {r["code"] for r in survivor["reasons"]}
    runtime.clock.advance(3)
    await runtime.submit("tick")
    assert field(runtime, "wait")["value"] is True
    assert runtime.state.tables["node_state"]["fixture.wait"]["rules"]["dwell"]
    command = dict(
        command_id="isolated",
        contract_id="fixture.guarded",
        command="start",
        args={"confirm": True},
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=runtime.clock.now_utc(),
        valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
    )
    result = await runtime.submit("command", command)
    assert result["status"] == "accepted"
    assert field(runtime, "guarded")["value"] == "active"
    assert "evaluation_error" in {
        r["code"] for r in field(runtime, "guarded", "upstream")["reasons"]
    }
    seq = runtime.state.publication_seq
    assert await runtime.submit("command", command) == result
    assert runtime.state.publication_seq == seq


def test_duplicate_transition_and_unreachable_machine_paths(config):
    data = machine_definition()
    data["machine"]["transitions"].append(copy.deepcopy(data["machine"]["transitions"][0]))
    data["outputs"]["value"] = dict(
        op="literal", schema=data["schema"]["fields"]["value"], value="idle"
    )
    with pytest.raises(RegistryValidationError) as error:
        validate(registry(config, data), type_registry())
    issues = {(i.code, i.path) for i in error.value.issues}
    assert (
        "definition.duplicate_transition_event",
        "/definitions/0/machine/transitions/2/event",
    ) in issues
    assert ("definition.unreachable_machine", "/definitions/0/machine") in issues
    data["machine"]["transitions"].pop()
    data["rules"] = {"machine": dict(op="machine")}
    data["outputs"]["value"] = dict(op="ref", name="machine")
    data["machine"]["transitions"].append(
        {**data["machine"]["transitions"][0], "source": "active", "target": "done"}
    )
    validate(config, type_registry())


def test_map_key_must_belong_to_input_catalog(config):
    data = definition(
        dict(op="map", arg=dict(op="input", name="a"), schema=schema(), entries={'"on"': True})
    )
    data["inputs"][0]["schema"] = schema("enum", value_catalog=["on", "off"])
    registry(config, data)["sources"][0]["value_schema"] = data["inputs"][0]["schema"]
    validate(config, type_registry())
    data["outputs"]["value"]["entries"]['"outside"'] = False
    with pytest.raises(RegistryValidationError) as error:
        validate(config, type_registry())
    assert any(i.code == "definition.invalid_map_key" for i in error.value.issues)


@pytest.mark.parametrize(
    "kind,item,key",
    [
        ("bool", True, "true"),
        ("bool", False, "false"),
        ("number", 42.0, "42"),
        ("number", -0.0, "0"),
        ("number", 0.000001, "0.000001"),
        ("text", "on", '"on"'),
        ("text", "true", '"true"'),
    ],
)
def test_canonical_declarative_map_and_legacy_parity(kind, item, key):
    data = definition(dict(op="map", arg=literal(item, kind), schema=schema(), entries={key: True}))
    parsed = DeclarativeDefinition.model_validate(data)
    check_definition(parsed)
    evaluation = Evaluation(parsed, {}, NOW)
    assert evaluation.evaluate(parsed.outputs["value"]).value is True
    # The released registry catalog/registered producer contract remains Python-str based.
    assert map_value(value(item), {str(item): "legacy"}, NOW).value == "legacy"


@pytest.mark.parametrize(
    "kind,item,key",
    [
        ("bool", True, "True"),
        ("number", 42, "42.0"),
        ("number", 42, "042"),
        ("number", 42, '"42"'),
        ("text", "on", "on"),
        ("bool", True, "null"),
    ],
)
def test_noncanonical_map_rejected_at_entry(config, kind, item, key):
    data = definition(dict(op="map", arg=literal(item, kind), schema=schema(), entries={key: True}))
    with pytest.raises(RegistryValidationError) as error:
        validate(registry(config, data), type_registry())
    assert any(
        i.code == "definition.invalid_map_key" and "/outputs/value/entries/" in i.path
        for i in error.value.issues
    )


@pytest.mark.parametrize("draft_id", ["a", "A_0-9", "x" * 128])
async def test_every_valid_draft_id_is_addressable(runtime, aiohttp_client, tmp_path, draft_id):
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.post(
        "/api/v1/registry/drafts",
        headers=headers,
        json={"draft_id": draft_id, "config": {}, "expected_active_revision": 0},
    )
    assert response.status == 200
    path = "/api/v1/registry/drafts/" + draft_id
    assert (await client.get(path, headers=headers)).status == 200
    assert (
        await client.put(path, headers=headers, json={"draft_version": 1, "config": {}})
    ).status == 200
    assert (await client.delete(path, headers=headers, json={"draft_version": 2})).status == 200


@pytest.mark.parametrize(
    "draft_id", ["/", "..", "a/b", "a?b", "a#b", "a%b", "ä", "", "x" * 129, "a b"]
)
async def test_unsafe_draft_ids_rejected(runtime, draft_id):
    with pytest.raises(ValueError, match="invalid_draft_id"):
        await runtime.submit("draft_create", {"draft_id": draft_id, "config": {}})


@pytest.mark.parametrize("failure", ["intent", "rotation", "completion", "completion_ack"])
async def test_rotation_failure_contract(
    runtime, aiohttp_client, tmp_path, monkeypatch, caplog, failure
):
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    old = api.tokens.values["admin"]
    submit = runtime.submit

    async def audit(operation, payload=None):
        if operation == "audit":
            if failure == "intent" and payload["operation"] == "token_rotation_requested":
                raise RuntimeError("persistence_unavailable")
            if (
                failure in {"completion", "completion_ack"}
                and payload["operation"] == "token_rotated"
            ):
                if failure == "completion_ack":
                    await submit(operation, payload)
                raise RuntimeError("persistence_unavailable")
        return await submit(operation, payload)

    monkeypatch.setattr(runtime, "submit", audit)
    if failure == "rotation":
        monkeypatch.setattr(api.tokens, "rotate", Mock(side_effect=OSError("write failed")))
    response = await client.post("/api/v1/tokens/rotate", headers=headers, json={"role": "admin"})
    if failure in {"intent", "rotation"}:
        assert response.status >= 500
        assert api.tokens.values["admin"] == old
        assert "token" not in await response.text()
    else:
        assert response.status == 200
        result = await response.json()
        assert result["status"] == "rotated" and result["audit_status"] == "completion_unconfirmed"
        assert result["token"] == api.tokens.values["admin"] != old
        assert (tmp_path / "admin_token").read_text() == result["token"]
        events = await runtime.store.history("diagnostic_event", HistoryQuery())
        assert any(e["rotation_id"] == result["rotation_id"] for e in events["items"])
        assert result["token"] not in str(events) + caplog.text
    assert old not in caplog.text


async def test_unrelated_source_does_not_write_explain_or_leak_boolean(runtime, config):
    registry(config, definition())
    add_source(config, "b")
    await activate(runtime, config)
    await runtime.submit("observation", observation(runtime, True))
    before = copy.deepcopy(runtime.state.tables["node_state"])
    for seq in range(2, 12):
        runtime.clock.advance(1)
        await runtime.submit("observation", observation(runtime, False, "b", ingest_seq=seq))
    assert runtime.state.tables["node_state"] == before
    assert all(
        "decision" not in step and "value" not in step
        for step in before["fixture.echo"]["trace"]["steps"]
    )
    await runtime.submit("observation", observation(runtime, False, ingest_seq=12))
    assert runtime.state.tables["node_state"] != before


async def exercise_history_times(store):
    data = {
        "a": "2026-01-01T12:00:00Z",
        "b": "2026-01-01T12:00:00.100000+00:00",
        "c": "2026-01-01T13:00:00.1+01:00",
        "d": "2026-01-01T12:00:00.010Z",
        "e": "2026-01-01T12:00:00+00:00",
    }
    state = await store.load()
    state.tables["diagnostic_event"] = {key: {"at": stamp} for key, stamp in data.items()}
    await store.commit(state)
    query = HistoryQuery(
        limit=2,
        since=datetime(2026, 1, 1, 12, tzinfo=UTC),
        until=datetime(2026, 1, 1, 12, 0, 0, 100000, tzinfo=UTC),
    )
    found = []
    while True:
        result = await store.history("diagnostic_event", query)
        found.extend(row["id"] for row in result["items"])
        if not result["next_cursor"]:
            break
        query = query.model_copy(update={"cursor": result["next_cursor"]})
    assert found == ["c", "b", "d", "e", "a"]


async def test_history_time_values_memory():
    await exercise_history_times(MemoryStore())


async def test_alpha3_storage_golden_activation_and_observations(runtime):
    fixtures = Path(__file__).parent / "fixtures"
    document = json.loads((fixtures / "alpha3-registry-v1.json").read_text())
    observation_document = json.loads((fixtures / "alpha3-observation-v1.json").read_text())
    assert storage_document(RegistryConfig.model_validate(document)) == document
    assert (
        Observation.model_validate(observation_document).storage_document() == observation_document
    )
    await activate(runtime, document)
    assert runtime.state.tables["registry_revision"]["1"]["config"] == document
    await runtime.submit("rollback", dict(revision=1, expected_active_revision=1))
    assert runtime.state.tables["registry_revision"]["2"]["config"] == document
    extended = RegistryConfig.model_validate({**document, "fingerprint_version": 2})
    assert storage_document(extended)["fingerprint_version"] == 2
    versioned = Observation.model_validate(
        {**observation_document, "reference_version": 2, "registry_revision": 1}
    )
    assert versioned.storage_document()["reference_version"] == 2


@pytest.mark.parametrize("oversize", ["entities", "bytes"])
async def test_discovery_oversized_responses_fail_closed(
    runtime, aiohttp_client, aiohttp_server, tmp_path, oversize
):
    states = (
        {f"sensor.test_{i}": {"state": "on", "attributes": {}} for i in range(10001)}
        if oversize == "entities"
        else {"sensor.test": {"state": "on", "attributes": {"large": "x" * 4_000_001}}}
    )
    fixture = FakeHA(states)
    ha = await aiohttp_server(fixture.application())
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(ha.make_url("/websocket")), "test-only")
        result = await client.get("/api/v1/ha/entities", headers=headers)
        assert result.status == 503
        assert await result.json() == {"error": "ha_discovery_unavailable"}
        assert adapter.discovery_cache == []
