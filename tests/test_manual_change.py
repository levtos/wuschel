"""Live HA evidence and serial registrations through the manual-change template."""

from datetime import timedelta
from uuid import uuid4

import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.ha_context import MANUAL_CHANGE_TYPE
from core_contracts.ha_projection import snapshot
from core_contracts.manual_change import register

from .conftest import activate


def test_production_capabilities_publish_manual_change_and_its_pending_reason():
    from core_contracts.capabilities import capabilities
    from core_contracts.contract_types import type_registry

    document = capabilities(None, type_registry())
    declaration = next(item for item in document["types"] if item["type_id"] == MANUAL_CHANGE_TYPE)
    assert declaration["parameter_schema"]["properties"]["attribution_wait_s"]["default"] == 5
    assert any(row["code"] == "attribution_pending" for row in document["reasons"]["codes"])


def configuration(config, *, wait=5):
    config["config_schema_version"] = 2
    config["context_producers"] = [
        {"producer_id": f"fixture.{name}", "display_name": name, "kind": role}
        for name, role in (("one", "policy"), ("two", "policy"), ("apply", "apply"))
    ]
    config["contracts"].append(
        {
            "contract_id": "fixture.manual",
            "type_id": MANUAL_CHANGE_TYPE,
            "type_version": 1,
            "enabled": True,
            "inputs": [{"name": "source", "ref": "source.a"}],
            "parameters": {"attribution_wait_s": wait},
            "ha_projection": True,
        }
    )
    return config


async def connected(runtime, config, *, wait=5):
    register(runtime.types)
    await activate(runtime, configuration(config, wait=wait))
    runtime.installation_id = str(uuid4())
    adapter = BridgeAdapter(runtime, None, "", "")
    adapter.protocol_selected = runtime.bridge_protocol_selected = 2
    runtime.bridge_features = ["context_registration_v1"]
    adapter.contexts.set_epoch("ha-first")
    await runtime.submit("bridge_state", ("connected", "connected"))
    return adapter


def values(runtime):
    return runtime.state.tables["contract_state_current"]["fixture.manual"]["fields"]


def request(adapter, context_id, producer_id="fixture.one", *, lifetime_s=600):
    now = adapter.runtime.clock.now_utc()
    return {
        "kind": "context_register",
        "request_id": str(uuid4()),
        "context_id": context_id,
        "producer_id": producer_id,
        "installation_id": adapter.runtime.installation_id,
        "ha_epoch_id": adapter.contexts.epoch,
        "issued_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=30)).isoformat(),
        "lifetime_s": lifetime_s,
    }


async def change(
    adapter, context_id, *, user=None, parent=None, old="off", new="on", kind="live_change"
):
    runtime = adapter.runtime
    runtime.clock.advance(0.05)
    stamp = runtime.clock.now_utc().isoformat()
    state = {"state": new, "attributes": {}, "last_changed": stamp}
    event = {
        "old_state": {"state": old},
        "time_fired": stamp,
        "context": {"id": context_id, "parent_id": parent, "user_id": user},
    }
    observation = adapter.observation(
        runtime.config.bindings[0], state, kind, event, runtime.config
    )
    await runtime.submit("observation", observation)
    return observation


async def advance(runtime, seconds):
    runtime.clock.advance(seconds)
    await runtime.submit("tick", None)


async def test_engine_before_and_after_change_keep_the_measured_instant(runtime, config):
    adapter = await connected(runtime, config)
    first = await change(adapter, "action-one", user="synthetic-executor")
    assert values(runtime)["value"]["status"] == "unknown"
    assert values(runtime)["origin"]["status"] == "unknown"
    assert values(runtime)["value"]["reasons"][0]["code"] == "attribution_pending"
    await advance(runtime, 2)
    assert values(runtime)["value"]["value"] is None
    response = await adapter.incoming_registration(request(adapter, "action-one"))
    assert response["status"] == "accepted"
    assert values(runtime)["value"]["value"] is False
    assert values(runtime)["origin"]["value"] == "engine"
    assert values(runtime)["producer_id"]["value"] == "fixture.one"
    assert values(runtime)["changed_at"]["value"] == first.ha_time_fired.isoformat().replace(
        "+00:00", "Z"
    )
    assert all(
        not row["fields"]["value"]["value"]
        for row in runtime.state.tables["contract_state_history"].values()
        if row["contract_id"] == "fixture.manual"
    )

    response = await adapter.incoming_registration(request(adapter, "root-two", "fixture.two"))
    assert response["status"] == "accepted"
    await change(
        adapter, "action-two", user="synthetic-executor", parent="root-two", old="on", new="off"
    )
    assert values(runtime)["value"]["value"] is False
    assert values(runtime)["producer_id"]["value"] == "fixture.two"
    projected = next(
        row for row in snapshot(runtime)["contracts"] if row["contract_id"] == "fixture.manual"
    )
    assert projected["primary_field"] == "value"
    assert projected["fields"]["origin"]["value"] == "engine"
    assert "synthetic-executor" not in str(projected)


@pytest.mark.parametrize(
    ("account", "parent", "origin"),
    [
        (None, None, "unattributed"),
        ("synthetic-user", None, "ha_user"),
        ("synthetic-user", "unknown-parent", "unattributed"),
    ],
)
async def test_manual_after_the_configured_deadline_and_later_correction(
    runtime, config, account, parent, origin
):
    adapter = await connected(runtime, config, wait=8)
    first = await change(adapter, "late", user=account, parent=parent)
    assert runtime.next_evaluation == first.received_at + timedelta(seconds=8)
    await advance(runtime, 5)
    assert values(runtime)["value"]["status"] == "unknown"
    await advance(runtime, 3)
    assert values(runtime)["value"]["value"] is True
    assert values(runtime)["origin"]["value"] == origin
    stamp = values(runtime)["changed_at"]["value"]

    await change(adapter, "same-attribute", old="on", new="on", user="synthetic-other")
    assert values(runtime)["changed_at"]["value"] == stamp
    assert values(runtime)["origin"]["value"] == origin
    response = await adapter.incoming_registration(request(adapter, "late"))
    assert response["status"] == "accepted"
    assert values(runtime)["value"]["value"] is False
    assert values(runtime)["changed_at"]["value"] == stamp
    raw = await runtime.store.get("source_observation_history", first.reference)
    assert raw["ha_context"]["user_id"] == account


async def test_ha_automation_and_older_context_do_not_replace_a_newer_change(runtime, config):
    adapter = await connected(runtime, config)
    await change(adapter, "old")
    await adapter.event(
        {
            "kind": "automatic_context",
            "ha_epoch_id": "ha-first",
            "producer_id": "script.fixture",
            "context": {"id": "script-root"},
        },
        runtime.config,
    )
    await change(adapter, "child", parent="script-root", old="on", new="off")
    assert values(runtime)["origin"]["value"] == "ha_automation"
    assert values(runtime)["automation_id"]["value"] == "script.fixture"
    assert (await adapter.incoming_registration(request(adapter, "old")))["status"] == "accepted"
    assert values(runtime)["origin"]["value"] == "ha_automation"
    assert (await adapter.incoming_registration(request(adapter, "child")))["status"] == "accepted"
    assert values(runtime)["origin"]["value"] == "engine"
    assert values(runtime)["automation_id"]["status"] == "not_applicable"


async def test_rejections_expiry_commit_failure_and_new_epoch(runtime, config):
    adapter = await connected(runtime, config)
    for producer in ("fixture.missing", "fixture.apply"):
        assert await adapter.incoming_registration(request(adapter, "wrong", producer)) == {
            "status": "rejected",
            "reason": "producer_not_allowed",
        }
    short = request(adapter, "expires", lifetime_s=1)
    receipt = await adapter.incoming_registration(short)
    assert receipt["status"] == "accepted"
    assert (
        await adapter.incoming_registration(request(adapter, "expires", lifetime_s=600)) == receipt
    )
    assert (await adapter.incoming_registration(request(adapter, "expires", "fixture.two")))[
        "reason"
    ] == "context_conflict"
    await advance(runtime, 1)
    assert (await adapter.incoming_registration(request(adapter, "expires")))[
        "reason"
    ] == "context_expired"
    await change(adapter, "expires")
    await advance(runtime, 5)
    assert values(runtime)["origin"]["value"] == "unattributed"

    queued = request(adapter, "old")
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    adapter.contexts.set_epoch("ha-new")
    await runtime.submit("bridge_state", ("connected", "connected"))
    assert values(runtime)["value"]["status"] == "unknown"
    assert (await adapter.incoming_registration(queued))["reason"] == "context_epoch_conflict"
    assert not adapter.contexts.engine_proofs
    runtime.store.available = False
    assert (await adapter.incoming_registration(request(adapter, "uncommitted")))[
        "reason"
    ] == "storage_unavailable"
    assert not adapter.contexts.engine_proofs
