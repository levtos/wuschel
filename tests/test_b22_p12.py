"""Field value intervals, restore boundaries and consumer/Traber publication."""

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core_contracts.field_since import track_fields
from core_contracts.ha_projection import snapshot
from core_contracts.quality import FieldValue, Reason, ReasonCode, unknown
from core_contracts.runtime import Runtime

from .conftest import activate
from .test_b22_p11 import Projection
from .test_declarative import definition, literal, observation, registry
from .test_workbench_api import api_client

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def field(value=True, status="valid", since=None):
    if status == "unknown":
        return unknown(ReasonCode.INPUT_UNKNOWN, "synthetic", NOW)
    return FieldValue(
        status=status,
        value=value,
        quality="healthy",
        since_at=since,
        **(
            {
                "held_until": NOW + timedelta(minutes=10),
                "grace_declared": True,
                "reasons": (Reason(code=ReasonCode.GRACE, input="synthetic", since=NOW),),
            }
            if status == "held"
            else {}
        ),
    )


def tracked(current, old=None, *, restoring=False, compatible=True):
    return track_fields(
        {"value": current},
        {"value": old.model_dump(mode="json")} if old else {},
        NOW,
        compatible=compatible,
        restoring=restoring,
    )["value"]


@pytest.mark.parametrize(
    "old,new,same",
    [(840.0, 840, True), ([1.0], [1], True), (True, 1, False), ("A", "A", True), ("A", "B", False)],
)
def test_recomputation_uses_b13_equivalence(old, new, same):
    before = NOW - timedelta(minutes=5)
    assert tracked(field(new), field(old, since=before)).since_at == (before if same else NOW)


@pytest.mark.parametrize("previous,current", [("valid", "held"), ("held", "valid")])
def test_held_boundary_counts_even_when_value_is_unchanged(previous, current):
    old = field(status=previous, since=NOW - timedelta(minutes=5))
    assert tracked(field(status=current), old).since_at == NOW


def test_unknown_boundary_breaks_interval_in_both_directions():
    old = field(since=NOW - timedelta(minutes=5))
    gap = tracked(field(status="unknown"), old)
    assert gap.since_at == NOW
    later = NOW + timedelta(seconds=5)
    result = track_fields(
        {"value": field()},
        {"value": gap.model_dump(mode="json")},
        later,
        compatible=True,
        restoring=False,
    )
    assert result["value"].since_at == later


def test_restore_requires_compatible_status_and_never_invents_held_start():
    old = field(since=NOW - timedelta(minutes=5))
    assert tracked(field(), old, restoring=True).since_at == old.since_at
    assert tracked(field(), old, compatible=False).since_at == NOW
    assert tracked(field(status="unknown"), old, restoring=True).since_at is None
    held = field(status="held", since=old.since_at)
    assert tracked(field(status="held"), held, restoring=True).since_at == old.since_at
    assert tracked(field(status="held"), old, restoring=True).since_at is None
    assert tracked(field(status="held"), held, restoring=True, compatible=False).since_at is None


def test_producer_clocks_are_preserved_and_legacy_valid_field_starts_on_evaluation():
    original = NOW - timedelta(hours=1)
    assert tracked(field(since=original), field(False)).since_at == original
    assert tracked(field(), field()).since_at == NOW
    gap = field(status="unknown")
    assert tracked(gap, gap).since_at is None


def derived(config):
    data = definition({"op": "and", "args": [{"op": "input", "name": "a"}, literal(True)]})
    data["schema"]["fields"]["constant"] = copy.deepcopy(data["schema"]["fields"]["value"])
    data["outputs"]["constant"] = literal(True)
    document = registry(config, data)
    document["contracts"][0]["ha_projection"] = True
    return document


def fields(runtime):
    return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]


def changed_now(runtime, name="value"):
    assert datetime.fromisoformat(fields(runtime)[name]["since_at"]) == runtime.clock.now_utc()


async def test_runtime_transitions_publish_intervals_to_consumer_and_each_traber_field(
    runtime, config, aiohttp_client, tmp_path
):
    await activate(runtime, derived(config))
    await runtime.submit("bridge_state", ("connected", "connected"))
    runtime.clock.advance(1)
    await runtime.submit("observation", observation(runtime, True))
    first = fields(runtime)
    runtime.clock.advance(5)
    await runtime.submit("observation", observation(runtime, True))
    assert fields(runtime)["value"]["since_at"] == first["value"]["since_at"]
    runtime.clock.advance(5)
    await runtime.submit("observation", observation(runtime, False))
    changed_now(runtime)
    runtime.clock.advance(5)
    await runtime.submit("observation", observation(runtime, "unavailable"))
    assert fields(runtime)["value"]["status"] == "unknown"
    changed_now(runtime)
    runtime.clock.advance(5)
    await runtime.submit("observation", observation(runtime, True))
    current = fields(runtime)
    changed_now(runtime)
    assert current["constant"]["since_at"] == first["constant"]["since_at"]
    api, client, _ = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.get(
        "/api/v1/contracts/fixture.echo",
        headers={"Authorization": "Bearer " + api.tokens.values["consumer"]},
    )
    assert response.status == 200
    assert (await response.json())["fields"] == current
    projection = Projection()
    runtime.installation_id = str(uuid4())
    projected = snapshot(runtime)
    projection.accept(object(), runtime.installation_id, projected)
    row = projection.rows["fixture.echo"]
    for name in current:
        assert row["fields"][name]["since_at"] == current[name]["since_at"]


async def test_restart_preserves_compatible_literal_but_not_unproven_external_value(
    runtime, config
):
    await activate(runtime, derived(config))
    await runtime.submit("bridge_state", ("connected", "connected"))
    await runtime.submit("observation", observation(runtime, True))
    before = fields(runtime)
    await runtime.stop()
    runtime.clock.advance(30)
    restarted = Runtime(runtime.store, runtime.clock, runtime.types)
    await restarted.start()
    try:
        restored = fields(restarted)
        assert restored["constant"]["since_at"] == before["constant"]["since_at"]
        assert restored["value"]["status"] == "unknown"
        assert restored["value"]["since_at"] is None
        await restarted.submit("tick")
        assert fields(restarted)["value"]["since_at"] is None
        restarted.clock.advance(5)
        await restarted.submit("bridge_state", ("connected", "connected"))
        await restarted.submit("observation", observation(restarted, True))
        changed_now(restarted)
    finally:
        await restarted.stop()


async def test_revision_incompatibility_starts_new_interval(runtime, config):
    document = derived(config)
    await activate(runtime, document)
    original = fields(runtime)["constant"]["since_at"]
    runtime.clock.advance(20)
    document["contracts"][0]["parameters"] = {}
    document["definitions"][0]["outputs"]["constant"] = {
        "op": "and",
        "args": [literal(True), literal(True)],
    }
    # A new type version is required for a changed recipe, even with the same value.
    document["definitions"][0]["type_version"] = 2
    document["contracts"][0]["type_version"] = 2
    await activate(runtime, document)
    assert fields(runtime)["constant"]["since_at"] != original
    changed_now(runtime, "constant")
