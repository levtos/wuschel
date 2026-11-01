from datetime import timedelta

import pytest

from core_contracts.quality import ReasonCode, unknown
from core_contracts.runtime import Runtime
from core_contracts.temporal import Deadline, Temporal, TemporalState
from core_contracts.testing.contract_types import type_registry

from .conftest import activate
from .test_core import NOW, held, valid
from .test_runtime import obs


def test_restore_grace_remaining_and_expired():
    temporal = Temporal(TemporalState(fingerprint="x"))
    temporal.grace(valid(), 10, NOW, trigger=True)
    bad = unknown(ReasonCode.INPUT_UNAVAILABLE, "a", NOW)
    original = temporal.grace(bad, 10, NOW, trigger=True)
    restored = Temporal(TemporalState.model_validate_json(temporal.state.model_dump_json()))
    restored.restore(restored.state, "x", NOW + timedelta(seconds=4), decisive_inputs=[])
    assert (
        restored.grace(bad, 10, NOW + timedelta(seconds=4), trigger=False).held_until
        == original.held_until
    )
    assert restored.grace(bad, 10, NOW + timedelta(seconds=11), trigger=False).status == "unknown"


@pytest.mark.parametrize(
    "evidence,accepts,keep",
    [(True, False, True), (False, False, False), ("held", True, True), ("held", False, False)],
)
def test_continuity_proof(evidence, accepts, keep):
    state = TemporalState(fingerprint="x", since_at=NOW)
    inputs = (
        [held(20)]
        if evidence == "held"
        else [valid(since_at=NOW - timedelta(seconds=1))]
        if evidence
        else []
    )
    temporal = Temporal(state, accepts)
    temporal.restore(state, "x", NOW + timedelta(seconds=4), decisive_inputs=inputs)
    assert (temporal.state.since_at is not None) == keep


def test_baseline_fingerprint_and_no_monotonic_persistence():
    state = TemporalState(fingerprint="x", baseline=valid())
    temporal = Temporal(state)
    temporal.restore(state, "x", NOW, decisive_inputs=[])
    assert temporal.state.baseline == state.baseline
    temporal.restore(state, "y", NOW, decisive_inputs=[])
    assert temporal.state.baseline is None
    assert "monotonic" not in temporal.state.model_dump_json()
    deadline = Deadline(deadline_id="d", episode_id="e", at=NOW)
    assert Deadline.model_validate_json(deadline.model_dump_json()).due(NOW)
    assert not deadline.model_copy(update={"fired": True}).due(NOW + timedelta(seconds=100))


async def test_latch_is_not_restored_and_snapshots_have_no_edge(runtime, config):
    config["contracts"][0].update(
        type_id="test.boolean",
        parameters={"operator": "bucket", "lower": 3, "upper": 5, "initial": False},
    )
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime, 6))
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True
    await runtime.stop()
    restarted = Runtime(runtime.store, runtime.clock, type_registry())
    await restarted.start()
    await restarted.submit(
        "observation", obs(restarted, 4).model_copy(update={"observation_kind": "snapshot"})
    )
    assert restarted.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is False
    await restarted.stop()


@pytest.mark.parametrize(
    "contract_type,parameters,expected",
    [
        ("test.echo", {}, True),
        ("test.boolean", {"operator": "not"}, False),
        ("test.fusion", {"strategy": "test_agreement"}, True),
        (
            "test.temporal",
            {"operation": "stable_for", "duration_s": 1, "accepts_held": False},
            True,
        ),
        ("test.state_machine", {"deadline_s": 30, "sessions": False}, "a"),
    ],
)
async def test_all_fixture_producers_end_to_end(
    runtime, config, contract_type, parameters, expected
):
    config["contracts"][0].update(type_id=contract_type, parameters=parameters)
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    runtime.clock.advance(2)
    await runtime.submit("tick")
    result = runtime.snapshot()["contracts"]["fixture.echo"]
    assert result["fields"]["value"]["value"] == expected
    stored = await runtime.store.load()
    assert stored.tables["contract_state_current"]["fixture.echo"] == result
    assert result["publication_seq"] in runtime.store.state.publications


async def test_overdue_deadline_with_unusable_guard_is_unknown(runtime, config):
    config["contracts"][0].update(
        type_id="test.state_machine", parameters={"deadline_s": 1, "sessions": False}
    )
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime))
    await runtime.submit(
        "command",
        {
            "command_id": "deadline.setup",
            "contract_id": "fixture.echo",
            "command": "request_b",
            "args": {},
            "origin": {"kind": "test", "actor": "test", "client_name": "test"},
            "issued_at": runtime.clock.now_utc(),
            "valid_until": runtime.clock.now_utc() + timedelta(seconds=30),
        },
    )
    await runtime.submit("observation", obs(runtime, availability="unavailable"))
    runtime.clock.advance(2)
    await runtime.submit("tick")
    field = runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]
    assert field["status"] == "unknown"
    assert field["reasons"][0]["code"] == "deadline_overdue_unprocessed"
    machine = runtime.state.tables["node_state"]["fixture.echo"]["machine"]
    assert machine["deadline"]["fired"] is False


async def test_b18_recovery_does_not_mutate_a_published_planner_snapshot(
    runtime, config, monkeypatch
):
    import copy

    from tests.conftest import activate
    from tests.test_phase1a import observation

    await activate(runtime, config)
    await runtime.submit("observation", observation(runtime, True))
    previous = runtime.state
    previous_values = copy.deepcopy(previous.tables["source_observation_current"])
    recovered = await runtime.store.load()

    class GuardedObservation(dict):
        def __setitem__(self, key, value):
            assert runtime.state is not recovered, (
                "published state changed under a background planner"
            )
            super().__setitem__(key, value)

        def __deepcopy__(self, memo):
            return copy.deepcopy(dict(self), memo)

    recovered.tables["source_observation_current"] = {
        key: GuardedObservation(value)
        for key, value in recovered.tables["source_observation_current"].items()
    }

    async def load():
        return recovered

    monkeypatch.setattr(runtime.store, "load", load)
    await runtime.submit("recover", {})
    assert previous.tables["source_observation_current"] == previous_values
