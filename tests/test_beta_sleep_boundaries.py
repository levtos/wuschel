"""Ordered choice, transaction boundaries, bounded plans and calendar-night semantics."""

import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core_contracts.declarative import Evaluation, check_definition, compile_machine
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.persistence import PersistenceUnavailable
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.statemachine import Definition, Machine, MachineState, Transition

from .test_beta_sleep import FLOW, TEMPLATES, command, fields, machine, observe, ready

NOW = datetime(2032, 4, 5, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "first,target,outcome",
    [(False, "c", "accepted"), (True, "b", "accepted"), (None, "a", "guard_unknown")],
)
def test_ordered_alternatives_stop_at_first_true_or_unknown(first, target, outcome):
    m = Machine(
        Definition(
            states=("a", "b", "c"),
            initial="a",
            commands=(),
            ordered=True,
            transitions=(
                Transition("a", "b", "input", lambda i, n: first, "first"),
                Transition("a", "c", "input", lambda i, n: True, "second"),
            ),
        )
    )
    result, reason = m.step(m.initial(NOW, 1, "synthetic"), "input", {}, NOW, 1)
    assert (result.state, reason) == (target, outcome)


def test_invalid_capture_restore_and_backward_machine_dependency_are_rejected():
    model = DeclarativeDefinition.model_validate(FLOW)
    m = compile_machine(model)
    state = m.initial(NOW, 1, "synthetic").model_copy(update={"state": "sleep", "captures": {}})
    assert m.validate_restore(state, "synthetic", NOW).status == "unknown"
    data = copy.deepcopy(FLOW)
    data["machine"]["absolute_transition"]["at_rule"] = {"op": "machine_field", "field": "since_at"}
    with pytest.raises(ValueError, match="stateful_guard_requires_contract_input"):
        check_definition(DeclarativeDefinition.model_validate(data))


async def test_failed_command_commit_keeps_state_capture_and_receipt_atomic(runtime, config):
    await ready(runtime, config)
    before = copy.deepcopy(machine(runtime))
    identity = str(uuid4())

    def fail():
        raise PersistenceUnavailable("synthetic_failure")

    runtime.store.before_commit = fail
    try:
        with pytest.raises(PersistenceUnavailable):
            await command(runtime, "confirm_sleep", identity)
    finally:
        runtime.store.before_commit = None
    assert machine(runtime) == before
    assert await runtime.store.get("command_log", identity) is None


async def test_invalid_calendar_does_not_block_an_explicit_sleep_command(runtime, config):
    await ready(runtime, config)
    await observe(runtime, "appointments", {"events": []})
    assert (await command(runtime, "confirm_sleep"))["status"] == "accepted"
    assert machine(runtime)["state"] == "sleep"
    assert fields(runtime)["wake_next_wake_start"]["status"] == "unknown"


async def test_date_bound_manual_exception_expires_at_next_local_midnight(runtime, config):
    await ready(runtime, config)
    response = await runtime.submit(
        "manual_source_enter",
        dict(
            source_id="source.manual_exception",
            request=dict(
                request_id=str(uuid4()),
                expected_active_revision=runtime.state.active_revision,
                value={"date": "2026-01-02", "wake_time": "08:00"},
            ),
            _actor={"kind": "internal", "role": "system"},
        ),
    )
    assert response["status"] == "accepted"
    row = runtime.state.tables["source_observation_current"]["binding.manual_exception"]
    assert datetime.fromisoformat(row["manual"]["valid_until"]) == datetime(
        2026, 1, 2, 23, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2032-04-05T21:00:00+00:00", True),
        ("2032-04-06T01:00:00+00:00", True),
        ("2032-04-06T10:00:00+00:00", False),
    ],
)
def test_night_permission_keeps_the_same_morning_group_across_midnight(stamp, expected):
    model = DeclarativeDefinition.model_validate(
        next(d for d in TEMPLATES if d["type_id"] == "daily.window")
    )
    now = datetime.fromisoformat(stamp)

    def value(v):
        return FieldValue(status="valid", quality="healthy", value=v)

    # before and after midnight both use the workday branch; the other branch differs.
    from zoneinfo import ZoneInfo

    result = Evaluation(
        model,
        dict(
            date=value(now.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()),
            today_workday=value(True),
            tomorrow_workday=value(True),
        ),
        now,
        parameters=dict(
            work_kind="time",
            work_phase="early_night",
            work_time="22:00",
            other_kind="time",
            other_phase="early_night",
            other_time="23:59",
        ),
    ).evaluate(model.outputs["value"])
    assert result.value is expected


def test_missing_sleep_memory_never_silently_disables_minimum_sleep():
    from core_contracts.wake.operator import build_plan
    from core_contracts.wake.settings import WakeSettings

    from .test_beta_sleep import settings

    absent = unknown(ReasonCode.RESTORE_MISSING, "capture", NOW)
    result = build_plan(
        WakeSettings.model_validate(settings()), NOW, absent, absent, absent, absent, None
    )
    assert result["all"].status == "unknown"


@pytest.mark.parametrize("reverse", [False, True])
async def test_output_order_does_not_change_schedule_or_first_sleep_capture(
    runtime, config, reverse
):
    await ready(runtime, config, wake_time="13:02", reverse_outputs=reverse)
    await command(runtime, "confirm_sleep")
    state = MachineState.model_validate(machine(runtime))
    assert state.captures["first_sleep_at"] == runtime.clock.now_utc()
    assert (
        datetime.fromisoformat(fields(runtime)["wake_next_wake_start"]["value"])
        == state.absolute_at
    )
