"""Deadline v2 keeps episodes, UTC instants and old definition fingerprints distinct."""

import copy
from datetime import timedelta

import pytest

from core_contracts.declarative import compile_machine
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.model import canonical
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.statemachine import Definition, Machine, Transition
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_core import NOW
from .test_declarative import literal, observation, registry, schema


def field(value):
    return FieldValue(
        status="valid", quality="healthy", value=value, since_at=NOW, evidence=("proof",)
    )


def machine(*, absolute=None):
    return Machine(
        Definition(
            states=("a", "b", "c"),
            initial="a",
            commands=(),
            sessions=True,
            deadline_v2=True,
            deadline_by_state={"a": 120, "b": 60},
            required_inputs=("edge",),
            absolute_transition=absolute,
            transitions=(
                Transition(
                    "a",
                    "a",
                    "input",
                    lambda inputs, now: (
                        inputs["edge"].value if inputs["edge"].usable(now) else None
                    ),
                    "interaction",
                    deadline_action="reset",
                ),
                Transition("a", "b", "deadline", lambda inputs, now: True, "elapsed"),
                Transition("b", "c", "deadline", lambda inputs, now: True, "elapsed"),
            ),
        )
    )


def test_full_reset_retains_episode_session_and_since_and_constant_high_does_not_repeat():
    m = machine()
    state = m.initial(NOW, 1, "fingerprint")
    state, _ = m.step(state, "input", {"edge": field(False)}, NOW, 1)
    before = state
    reset, result = m.step(state, "input", {"edge": field(True)}, NOW + timedelta(seconds=60), 1)
    assert result == "accepted"
    assert reset.deadline.at == NOW + timedelta(seconds=180)
    assert reset.deadline_generation == 1
    assert reset.deadline.deadline_id != before.deadline.deadline_id
    assert (reset.episode_id, reset.episode_started_at, reset.session_id, reset.since_at) == (
        before.episode_id,
        before.episode_started_at,
        before.session_id,
        before.since_at,
    )
    again, result = m.step(reset, "input", {"edge": field(True)}, NOW + timedelta(seconds=90), 1)
    assert result == "unchanged" and again.deadline == reset.deadline


def test_unknown_holds_state_and_original_deadline_until_evidence_returns():
    m = machine()
    state = m.initial(NOW, 1, "fingerprint")
    missing = unknown(ReasonCode.INPUT_STALE, "edge", NOW)
    held, result = m.step(state, "deadline", {"edge": missing}, NOW + timedelta(seconds=150), 1)
    assert result == "guard_unknown" and held == state and not held.deadline.fired
    assert m.due_event(held, NOW + timedelta(seconds=150)) == "deadline"
    new, result = m.step(held, "deadline", {"edge": field(False)}, NOW + timedelta(seconds=180), 1)
    assert result == "accepted" and new.state == "b"
    assert new.deadline.at == NOW + timedelta(seconds=240)
    assert new.episode_id == held.episode_id
    final, _ = m.step(new, "deadline", {"edge": field(False)}, NOW + timedelta(seconds=240), 1)
    assert final.state == "c" and final.deadline is None


def test_restore_sets_reset_baseline_and_preserves_original_duration():
    m = machine()
    original = m.initial(NOW, 1, "fingerprint")
    restored = m.prepare(
        original, {"edge": field(True)}, NOW + timedelta(seconds=30), baseline=True
    )
    result, _ = m.step(restored, "input", {"edge": field(True)}, NOW + timedelta(seconds=40), 1)
    assert result.deadline == original.deadline


@pytest.mark.parametrize("stamp", ["2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00"])
def test_absolute_timestamp_uses_offset_and_can_be_replaced_or_removed(stamp):
    from datetime import datetime

    m = machine(
        absolute={"states": ["a"], "target": "c", "at_input": "planned", "priority": "absolute"}
    )
    state = m.initial(NOW, 1, "fingerprint")
    inputs = {"edge": field(False), "planned": field(stamp)}
    state = m.prepare(state, inputs, NOW)
    assert state.absolute_at == datetime.fromisoformat(stamp)
    replacement = m.prepare(
        state, {**inputs, "planned": field((NOW - timedelta(seconds=1)).isoformat())}, NOW
    )
    assert m.due_event(replacement, NOW) == "absolute"
    moved, _ = m.step(replacement, "absolute", inputs, NOW, 1)
    assert moved.state == "c" and moved.since_at == NOW
    removed = m.prepare(
        state, {**inputs, "planned": unknown(ReasonCode.INPUT_STALE, "planned", NOW)}, NOW
    )
    assert removed.absolute_at is None and removed.deadline == state.deadline


def test_tied_absolute_and_duration_use_explicit_priority():
    for priority, expected in [("absolute", "absolute"), ("duration", "deadline")]:
        m = machine(
            absolute={"states": ["a"], "target": "c", "at_input": "planned", "priority": priority}
        )
        state = m.initial(NOW, 1, "fingerprint")
        at = NOW + timedelta(seconds=120)
        state = m.prepare(state, {"edge": field(False), "planned": field(at.isoformat())}, NOW)
        assert m.due_event(state, at) == expected


def definition():
    return dict(
        type_id="test.deadline",
        type_version=1,
        language_version=3,
        inputs=[dict(name="a", schema=schema())],
        schema=dict(
            fields={"value": schema("enum", value_catalog=["a", "b", "c"])},
            available_projection=["value"],
        ),
        outputs={"value": {"op": "machine"}},
        machine=dict(
            id="flow",
            states=["a", "b", "c"],
            initial="a",
            reinitialize_when=literal(True),
            unknown_deadline="keep_and_block_transition",
            deadline_by_state={"a": {"duration": 120}, "b": {"duration": 60}},
            transitions=[
                dict(
                    source="a",
                    target="b",
                    event="deadline",
                    guard={"op": "input", "name": "a"},
                    reason="elapsed",
                )
            ],
        ),
    )


async def test_runtime_clock_restore_and_commit_keep_original_deadline(runtime, config):
    data = definition()
    document = registry(copy.deepcopy(config), data)
    document["config_schema_version"] = 2
    await activate(runtime, document)
    await runtime.submit("observation", observation(runtime, True))
    before = copy.deepcopy(runtime.state.tables["node_state"]["fixture.echo"]["machine"])
    runtime.clock.advance(60)
    await runtime._restore("process_restart")
    restored = runtime.state.tables["node_state"]["fixture.echo"]["machine"]
    assert restored["deadline"] == before["deadline"]
    runtime.clock.advance(60)
    await runtime.submit("tick")
    assert (
        runtime.state.tables["node_state"]["fixture.echo"]["machine"]["deadline"]
        == before["deadline"]
    )
    await runtime.submit(
        "observation",
        observation(runtime, True).model_copy(update={"ingest_seq": runtime.state.generation + 1}),
    )
    assert runtime.state.tables["node_state"]["fixture.echo"]["machine"]["state"] == "b"


def test_old_model_serialization_has_no_additive_deadline_fields():
    data = definition()
    data["language_version"] = 2
    data["machine"].pop("unknown_deadline")
    data["machine"].pop("deadline_by_state")
    data["machine"]["deadline_s"] = 120
    old = DeclarativeDefinition.model_validate(data)
    encoded = canonical(old)
    assert (
        "deadline_by_state" not in encoded
        and "deadline_action" not in encoded
        and "unknown_deadline" not in encoded
    )
    assert not compile_machine(old).definition.deadline_v2


@pytest.mark.parametrize(
    "change",
    [
        "old_language",
        "mixed",
        "reset_episode",
        "no_duration",
        "unknown_state",
        "bad_parameter",
        "tie",
        "not_timestamp",
    ],
)
def test_invalid_deadline_definitions_rejected(runtime, config, change):
    data = definition()
    flow = data["machine"]
    if change == "old_language":
        data["language_version"] = 2
    elif change == "mixed":
        flow["deadline_s"] = 60
    elif change == "reset_episode":
        flow["transitions"][0].update(new_episode=True, deadline_action="reset")
    elif change == "no_duration":
        flow["transitions"][0].update(deadline_action="reset", event="input", target="c")
    elif change == "unknown_state":
        flow["deadline_by_state"]["missing"] = {"duration": 60}
    elif change == "bad_parameter":
        flow["deadline_by_state"]["a"]["duration"] = {"op": "parameter", "name": "missing"}
    else:
        flow["absolute_transition"] = dict(states=["a"], target="c", at_input="a")
        if change == "not_timestamp":
            flow["absolute_transition"]["priority"] = "absolute"
    with pytest.raises(ValueError):
        validate(registry(copy.deepcopy(config), data), runtime.types)
