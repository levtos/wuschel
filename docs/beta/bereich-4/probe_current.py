"""A1 evidence only: exercise the unmodified b10 implementation with synthetic data.

Run from the repository root: uv run python docs/beta/bereich-4/probe_current.py
No candidate implementation, HA, database, or network access is used.
"""

import copy
import json
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from core_contracts.calendar_cache import CalendarWindow
from core_contracts.declarative import (
    DefinitionValidationErrors,
    check_definition,
    compile_machine,
)
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.model import checksum
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.statemachine import Definition, Machine, MachineState, Transition

NOW = datetime(2032, 1, 1, 12, tzinfo=UTC)
STATES = ("awake", "pre_sleep", "sleep", "waking")


def field(value):
    return FieldValue(status="valid", quality="healthy", value=value, evidence=("synthetic",))


def schema(kind="bool", **extra):
    return dict(type=kind, required=True, physical_state=False, **extra)


def literal(value=True):
    return dict(op="literal", value=value, schema=schema())


def definition():
    return dict(
        type_id="synthetic.sleep_probe",
        type_version=1,
        language_version=5,
        inputs=[dict(name="tv", schema=schema()), dict(name="pc", schema=schema())],
        schema=dict(fields={"value": schema("enum", value_catalog=list(STATES))}),
        outputs={"value": dict(op="machine")},
        machine=dict(
            id="flow",
            states=list(STATES),
            initial="awake",
            commands={"sleep": {}, "pre_sleep": {}},
            unknown_deadline="keep_and_block_transition",
            deadline_by_state={
                s: dict(duration=n)
                for s, n in (("pre_sleep", 2700), ("sleep", 2700), ("waking", 3600))
            },
            reinitialize_when=literal(),
            transitions=[
                dict(
                    source="pre_sleep",
                    target="sleep",
                    event="deadline",
                    guard=literal(),
                    reason="timeout",
                    deadline_action="clear",
                )
            ],
        ),
    )


def model_probe():
    result = {}
    data = definition()
    parsed = DeclarativeDefinition.model_validate(data)
    check_definition(parsed)
    machine = compile_machine(parsed)
    before = machine.initial(NOW, 1, "synthetic").model_copy(update={"state": "pre_sleep"})
    before = before.model_copy(
        update={"deadline": machine._deadline(before.episode_id, NOW, "pre_sleep")}
    )
    after, outcome = machine.step(
        before,
        "deadline",
        {"tv": field(True), "pc": field(False)},
        NOW + timedelta(seconds=2700),
        1,
    )
    assert outcome == "accepted" and after.state == "sleep" and after.deadline is None
    assert after.episode_id == before.episode_id
    result["P4_clear"] = "accepted; sleep has no deadline; episode unchanged"

    duplicate = copy.deepcopy(data)
    duplicate["machine"]["transitions"] += [
        dict(source="pre_sleep", target=target, event="input", guard=literal(), reason="probe")
        for target in ("awake", "pre_sleep")
    ]
    try:
        check_definition(DeclarativeDefinition.model_validate(duplicate))
    except DefinitionValidationErrors as error:
        assert any(str(issue) == "duplicate_transition_event" for issue in error.issues)
        result["P1_validator"] = "duplicate_transition_event"
    else:
        raise AssertionError("Expected b10 duplicate rejection")

    commands = copy.deepcopy(data)
    commands["machine"]["transitions"].append(
        dict(
            source="sleep",
            target="sleep",
            event="sleep",
            guard=literal(),
            reason="confirmed",
            deadline_action="reset",
        )
    )
    try:
        DeclarativeDefinition.model_validate(commands)
    except ValidationError as error:
        assert "reset_duration_required" in str(error)
        result["P3_command_reset"] = "reset_duration_required"
    else:
        raise AssertionError("Expected b10 command-reset rejection")

    # Even the low-level engine does not implement first matching input rules.
    native = Machine(
        Definition(
            states=STATES,
            initial="pre_sleep",
            commands=(),
            transitions=(
                Transition("pre_sleep", "awake", "input", lambda i, n: False, "first"),
                Transition("pre_sleep", "sleep", "input", lambda i, n: True, "second"),
            ),
        )
    )
    state, outcome = native.step(native.initial(NOW, 1, "synthetic"), "input", {}, NOW, 1)
    assert state.state == "pre_sleep" and outcome == "guard_rejected"
    result["P1_runtime"] = "false first guard rejects; second is not evaluated"

    snapshot = MachineState.model_validate_json(after.model_dump_json())
    assert snapshot.deadline is None and snapshot.since_at == after.since_at
    result["restore_roundtrip"] = "state/deadline identity preserved (model only)"
    window = CalendarWindow(past_days=0, future_days=31)
    assert window.bounds(NOW)[1].date().isoformat() == "2032-02-01"
    result["P2_calendar_window"] = "today through day +30 fits existing exclusive end +31"
    result["definition_digest"] = checksum(parsed)
    return result


def row_probe():
    """Isolated native transitions, not a claim that the full template is supported."""
    rows = []
    # Every table row in Issue 61 section 1, plus one row for presence unknown.
    cases = [
        ("A1", "awake", "sleep", "sleep", "confirmed", True, False, "keep"),
        ("A2", "awake", "pre_sleep", "pre_sleep", "intentional", True, False, "keep"),
        ("A3", "awake", "input", "pre_sleep", "suspected", True, False, "keep"),
        ("P1", "pre_sleep", "deadline", "sleep", "timeout", False, False, "clear"),
        ("P2", "pre_sleep", "sleep", "sleep", "confirmed", False, False, "keep"),
        ("P3", "pre_sleep", "pre_sleep", "pre_sleep", "intentional", False, False, "reset"),
        ("P4", "pre_sleep", "input", "pre_sleep", "interaction", False, False, "reset"),
        ("P5", "pre_sleep", "input", "awake", "pc_on", False, True, "keep"),
        ("P6", "pre_sleep", "input", "awake", "away", False, True, "keep"),
        ("P7", "pre_sleep", "absolute", "waking", "wake", False, False, "keep"),
        ("P8", "pre_sleep", "ignored", "pre_sleep", "tv_off", False, False, "keep"),
        ("P9", "pre_sleep", "unknown", "pre_sleep", "unknown", False, False, "keep"),
        ("S1", "sleep", "input", "awake", "wake_signal", False, True, "keep"),
        ("S2", "sleep", "input", "awake", "away", False, True, "keep"),
        ("S3", "sleep", "absolute", "waking", "wake", False, False, "keep"),
        ("S4", "sleep", "sleep", "sleep", "confirmed", False, False, "reset"),
        ("S5", "sleep", "pre_sleep", "pre_sleep", "intentional", True, False, "keep"),
        ("S6", "sleep", "ignored", "sleep", "no_effect", False, False, "keep"),
        ("W1", "waking", "input", "awake", "wake_signal", False, True, "keep"),
        ("W2", "waking", "input", "awake", "away", False, True, "keep"),
        ("W3", "waking", "deadline", "awake", "timeout", False, True, "keep"),
        ("W4", "waking", "sleep", "sleep", "confirmed", False, False, "keep"),
        ("W5", "waking", "pre_sleep", "pre_sleep", "intentional", False, False, "keep"),
        ("U1", "sleep", "ignored", "sleep", "presence_unknown", False, False, "keep"),
    ]
    for name, source, event, target, reason, new, end, action in cases:
        ignored = event in {"ignored", "unknown"}
        transitions = (
            ()
            if ignored or event == "absolute"
            else (
                Transition(
                    source,
                    target,
                    event,
                    lambda i, n: True,
                    reason,
                    new_episode=new,
                    ends_episode=end,
                    deadline_action=action,
                ),
            )
        )
        absolute = (
            dict(states=[source], target="waking", at_input="planned", priority="absolute")
            if event == "absolute"
            else None
        )
        machine = Machine(
            Definition(
                states=STATES,
                initial=source,
                transitions=transitions,
                commands=("sleep", "pre_sleep"),
                deadline_v2=True,
                deadline_by_state={"pre_sleep": 2700, "sleep": 2700, "waking": 3600},
                absolute_transition=absolute,
                required_inputs=("tv", "pc"),
            )
        )
        before = machine.initial(NOW, 1, "synthetic")
        # Establish b10's required rising-edge baseline for reset leaf probes.
        # This is deliberately not the proposed command-reset implementation.
        if action == "reset":
            before = before.model_copy(update={"reset_levels": {"0": False}})
        elapsed = (3600 if source == "waking" else 2700) if event == "deadline" else 60
        at = NOW + timedelta(seconds=elapsed)
        inputs = {"tv": field(True), "pc": field(False), "planned": field(at.isoformat())}
        if event == "unknown":
            inputs["tv"] = unknown(ReasonCode.INPUT_STALE, "tv", at)
        if absolute:
            before = machine.prepare(before, inputs, at)
        after, outcome = machine.step(
            before,
            "input" if ignored else event,
            inputs,
            at,
            1,
            manual=event in {"sleep", "pre_sleep"},
        )
        assert after.state == target, (name, after.state, target)
        if ignored:
            assert after.deadline == before.deadline
        elif action == "clear" or target == "awake":
            assert after.deadline is None
        else:
            assert after.deadline.at == at + timedelta(seconds=3600 if target == "waking" else 2700)
        assert (after.episode_id != before.episode_id) == new
        assert bool(after.episode_ended_at) == end
        rows.append(
            dict(
                id=name,
                target=target,
                outcome=outcome,
                level="isolated_native_transition",
                needs_P3=action == "reset" and event != "input",
            )
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(dict(model_probes=model_probe(), rows=row_probe()), indent=2))
