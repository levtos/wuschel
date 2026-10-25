"""Language 5 calendar semantics, privacy and shared scheduler regression."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from core_contracts.calendar_cache import normalize_events
from core_contracts.clock import FakeClock
from core_contracts.declarative import Evaluation, check_definition, compile_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.persistence import State
from core_contracts.quality import FieldValue, Reason, ReasonCode, unknown
from core_contracts.registry import Contract

from .test_declarative import literal, schema

NOW = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def value(raw, **kwargs):
    return FieldValue(status="valid", quality="healthy", value=raw, **kwargs)


def definition(rule, kind, inputs=None):
    return DeclarativeDefinition.model_validate(
        dict(
            type_id="test.calendar_rules",
            type_version=1,
            language_version=5,
            inputs=[dict(name=k, schema=schema(v)) for k, v in (inputs or {"a": "text"}).items()],
            schema=dict(fields=dict(value=schema(kind)), available_projection=["value"]),
            outputs=dict(value=rule),
        )
    )


def curve(points=None, **kwargs):
    return dict(
        op="annual_curve",
        at=dict(op="input", name="a"),
        points=points or [dict(day="01-01", value=0), dict(day="07-01", value=182)],
        **kwargs,
    )


def evaluate(rule, raw, kind="number", **kwargs):
    model = definition(rule, kind, {"a": "object" if kind == "bool" else "text"})
    check_definition(model)
    return Evaluation(
        model, {"a": raw if isinstance(raw, FieldValue) else value(raw)}, NOW, **kwargs
    ).evaluate(model.outputs["value"])


@pytest.mark.parametrize(
    "day,expected",
    [
        ("2026-01-01", 0),
        ("2026-04-02", 91.50276243093923),
        ("2026-07-01", 182),
        ("2026-12-31", 182 / 184),
    ],
)
def test_annual_curve_exact_anchors_real_calendar_days_and_year_wrap(day, expected):
    assert evaluate(curve(), day).value == pytest.approx(expected)


def test_leap_year_has_real_extra_day_and_parameter_points():
    model = definition(
        curve(
            [dict(day="02-28", value=0), dict(day="03-01", value=dict(op="parameter", name="high"))]
        ),
        "number",
    )
    data = model.model_dump(mode="json")
    data["parameter_schema"] = dict(high=dict(schema=schema("number")))
    model = DeclarativeDefinition.model_validate(data)
    check_definition(model)
    assert (
        Evaluation(model, {"a": value("2028-02-29")}, NOW, parameters={"high": 10})
        .evaluate(model.outputs["value"])
        .value
        == 5
    )


@pytest.mark.parametrize(
    "strategy,day,expected",
    [("feb28", "2027-02-28", 10), ("mar01", "2027-03-01", 10), ("skip", "2027-03-01", 0)],
)
def test_explicit_february_29_strategy(strategy, day, expected):
    points = [dict(day="01-01", value=0), dict(day="02-29", value=10), dict(day="12-31", value=0)]
    assert evaluate(curve(points, leap_day=strategy), day).value == expected


@pytest.mark.parametrize(
    "points,options",
    [
        ([dict(day="03-21", value=1)], {}),
        ([dict(day="03-21", value=1)] * 2, {}),
        ([dict(day="12-21", value=1), dict(day="03-21", value=2)], {}),
        ([dict(day="02-30", value=1), dict(day="03-21", value=2)], {}),
        ([dict(day="02-29", value=1), dict(day="03-21", value=2)], {}),
        ([dict(day="02-28", value=1), dict(day="02-29", value=2)], {"leap_day": "feb28"}),
        ([dict(day="02-29", value=1), dict(day="03-21", value=2)], {"leap_day": "skip"}),
        ([dict(day="01-01", value=True), dict(day="03-21", value=2)], {}),
        ([dict(day="01-01", value=float("nan")), dict(day="03-21", value=2)], {}),
        ([dict(day="01-01", value=float("inf")), dict(day="03-21", value=2)], {}),
    ],
)
def test_annual_curve_rejects_invalid_declarations(points, options):
    with pytest.raises(ValidationError):
        definition(curve(points, **options), "number")


@pytest.mark.parametrize(
    "raw", ["2026-1-01", "2026-02-29", "20260101", "2026-W01-1", "2026-01-01T00:00:00Z"]
)
def test_date_requires_exact_valid_iso_date(raw):
    assert evaluate(curve(), raw).reasons[0].code == ReasonCode.INVALID_VALUE


def test_annual_curve_preserves_empty_status_quality_and_absolute_grace():
    missing = unknown(ReasonCode.INPUT_ABSENT, "a", NOW).model_copy(update={"status": "unresolved"})
    assert evaluate(curve(), missing) == missing
    end = NOW + timedelta(seconds=1)
    held = FieldValue(
        status="held",
        quality="healthy",
        value="2026-07-01",
        held_until=end,
        grace_declared=True,
        reasons=(Reason(code=ReasonCode.GRACE, input="a", since=NOW),),
    )
    result = evaluate(curve(), held)
    assert (result.value, result.status, result.held_until) == (182, "held", end)
    expired = held.model_copy(update={"held_until": NOW})
    assert evaluate(curve(), expired).status == "unknown"


def calendar(events=(), *, expires=7200):
    start, end = NOW - timedelta(days=1), NOW + timedelta(days=14)
    return dict(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        fetched_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(seconds=expires)).isoformat(),
        events=normalize_events(
            {"calendar.synthetic": {"events": list(events)}},
            "calendar.synthetic",
            start,
            end,
            "Europe/Berlin",
        ),
    )


def event(a=-10, b=10, title=" SYNTHETIC_PRIVATE Urlaub "):
    return dict(
        start=(NOW + timedelta(seconds=a)).isoformat(),
        end=(NOW + timedelta(seconds=b)).isoformat(),
        summary=title,
        location="SYNTHETIC_PRIVATE_LOCATION",
    )


def active(**kwargs):
    return dict(
        op="calendar_active",
        arg=dict(op="input", name="a"),
        pattern=literal(" *uRLaUb* ", "text"),
        **kwargs,
    )


@pytest.mark.parametrize(
    "a,b,expected", [(0, 10, True), (-10, 0, False), (1, 10, False), (-10, 1, True)]
)
def test_calendar_half_open_case_insensitive_glob(a, b, expected):
    result = evaluate(active(), calendar([event(a, b)]), "bool")
    assert result.value is expected
    assert "SYNTHETIC_PRIVATE" not in result.model_dump_json()


def test_valid_empty_is_false_but_stale_invalid_and_unknown_are_not_false():
    assert evaluate(active(), calendar(), "bool").value is False
    stale = evaluate(active(), calendar(expires=0), "bool")
    assert stale.value is None and stale.status == "unknown"
    raw = calendar([event()])
    for change in [
        dict(events=None),
        dict(window_end=raw["window_start"]),
        dict(events=[{}]),
        dict(events=[{**raw["events"][0], "key": "SYNTHETIC_PRIVATE_TITLE"}]),
    ]:
        assert evaluate(active(), {**raw, **change}, "bool").status == "unknown"
    missing = unknown(ReasonCode.INPUT_STALE, "a", NOW)
    assert evaluate(active(), missing, "bool") == missing


def test_all_day_uses_normalized_berlin_dst_bounds():
    cache = calendar([dict(summary="Urlaub", start="2026-10-25", end="2026-10-26")])
    assert evaluate(active(all_day_only=True), cache, "bool").value is True
    assert evaluate(active(all_day_only=True), calendar([event()]), "bool").value is False
    start = datetime.fromisoformat(cache["events"][0]["start"])
    end = datetime.fromisoformat(cache["events"][0]["end"])
    assert end - start == timedelta(hours=25)


class Harness:
    def __init__(self, mixed=True, reverse=False):
        data = definition(active(), "bool", {"a": "object", "at": "timestamp"}).model_dump(
            mode="json"
        )
        reached = dict(op="time_reached", at=dict(op="input", name="at"))
        if mixed:
            data["schema"]["fields"]["reached"] = schema()
            data["outputs"]["reached"] = reached
            if reverse:
                data["outputs"] = dict(reversed(list(data["outputs"].items())))
        else:
            data["outputs"] = dict(value=reached)
        self.definition = DeclarativeDefinition.model_validate(data)
        self.kind = compile_definition(self.definition)
        self.contract = Contract(
            contract_id="test.clock",
            type_id=self.definition.type_id,
            type_version=1,
            enabled=True,
            inputs=[],
            parameters={},
        )
        self.clock, self.node, self.state = (
            FakeClock(NOW),
            {},
            State(active_revision=1, epoch_id="test"),
        )

    def run(self, cache, target, origin="tick"):
        fields, self.node, _ = self.kind.evaluate(
            self.clock,
            {},
            self.contract,
            dict(a=value(cache), at=value(target.isoformat())),
            self.node,
            self.state,
            origin,
            "valid",
            None,
        )
        return fields

    def due(self):
        return self.kind.next_due({}, self.node, self.clock.now_utc())


@pytest.mark.parametrize("mixed,reverse", [(False, False), (True, False), (True, True)])
def test_time_reached_alone_and_mixed_share_due_reset_without_lost_or_old_alarm(mixed, reverse):
    h = Harness(mixed, reverse)
    target = NOW + timedelta(seconds=5)
    cache = calendar([event(10, 20)])
    assert h.run(cache, target)["reached" if mixed else "value"].value is False
    assert h.due() == target
    h.node = json.loads(json.dumps(h.node))  # restart context
    h.clock.advance(5)
    fields = h.run(cache, target, "restore")
    assert fields["reached" if mixed else "value"].value is True
    assert h.due() == (NOW + timedelta(seconds=10) if mixed else None)
    if mixed:
        h.clock.advance(5)
        assert h.run(cache, target)["value"].value is True
        assert h.due() == NOW + timedelta(seconds=20)
        new_target = NOW + timedelta(seconds=7)
        assert h.run(calendar(), new_target)["value"].value is False
        assert NOW + timedelta(seconds=20) not in [
            datetime.fromisoformat(t) for t in h.node["clock_due"]
        ]


def test_calendar_union_schedules_actual_boolean_change_and_expiry_without_title_leaks():
    h = Harness()
    cache = calendar([event(-10, 10), event(5, 20), event(20, 30)])
    h.run(cache, NOW)
    assert h.due() == NOW + timedelta(seconds=30)
    assert "SYNTHETIC_PRIVATE" not in json.dumps(h.node)
    h.run({**cache, "expires_at": (NOW + timedelta(seconds=8)).isoformat()}, NOW)
    assert h.due() == NOW + timedelta(seconds=8)
    h.clock.advance(8)
    assert (
        h.run({**cache, "expires_at": h.clock.now_utc().isoformat()}, NOW)["value"].status
        == "unknown"
    )


def test_clock_jumps_reassess_current_intervals_and_replace_the_due_list():
    h = Harness()
    cache = calendar([event(10, 20), event(30, 40)])
    cache["fetched_at"] = (NOW - timedelta(minutes=1)).isoformat()
    h.run(cache, NOW)
    h.clock.jump(35)
    assert h.run(cache, NOW)["value"].value is True
    assert h.due() == NOW + timedelta(seconds=40)
    h.clock.jump(-20)
    assert h.run(cache, NOW)["value"].value is True
    assert h.due() == NOW + timedelta(seconds=20)
    h.clock.jump(30)
    assert h.run(cache, NOW)["value"].value is False
    assert h.due() == NOW + timedelta(seconds=7200)


def test_calendar_input_validity_and_absolute_held_deadline_are_not_renewed():
    from core_contracts.calendar_rules import calendar_active

    rule = definition(active(), "bool", {"a": "object"}).outputs["value"]
    begin, end = NOW + timedelta(seconds=2), NOW + timedelta(seconds=4)
    cached = value(calendar([event()]), valid_from=begin, valid_until=end)
    due = []
    assert calendar_active(rule, [cached, value("*Urlaub*")], NOW, due).status == "unknown"
    assert due == [begin.isoformat()]
    cached = FieldValue(
        status="held",
        quality="healthy",
        value=cached.value,
        held_until=end,
        grace_declared=True,
        reasons=(Reason(code=ReasonCode.GRACE, input="a", since=NOW),),
    )
    due = []
    result = calendar_active(rule, [cached, value("*Urlaub*")], begin, due)
    assert result.status == "held" and result.held_until == end and end.isoformat() in due
    assert calendar_active(rule, [cached, value("*Urlaub*")], end, []).status == "unknown"


@pytest.mark.parametrize("pattern", ["", "   ", "*" * 257])
def test_pattern_limits_fail_without_source_content_in_output(pattern):
    rule = {**active(), "pattern": literal(pattern, "text")}
    result = evaluate(rule, calendar([event()]), "bool")
    assert result.status == "unknown" and result.reasons[0].code == ReasonCode.INVALID_VALUE
    assert "SYNTHETIC_PRIVATE" not in result.model_dump_json()


def test_calendar_resource_limits_are_enforced():
    raw = calendar([event()])
    raw["events"] *= 1001
    assert evaluate(active(), raw, "bool").status == "unknown"
    raw = calendar([event(title="X" * 4096)])
    raw["events"] *= 256
    assert evaluate(active(), raw, "bool").status == "unknown"


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_all_day_calendar_rule_uses_dst_instants_without_24_hour_assumption(day, hours):
    from core_contracts.clock import berlin_zone

    local = datetime.fromisoformat(day).replace(tzinfo=berlin_zone())
    now = local.astimezone(UTC)
    end = (local + timedelta(days=1)).astimezone(UTC)
    cache = dict(
        window_start=now.isoformat(),
        window_end=end.isoformat(),
        fetched_at=now.isoformat(),
        expires_at=end.isoformat(),
        events=normalize_events(
            {
                "calendar.synthetic": {
                    "events": [
                        dict(
                            summary="Urlaub",
                            start=day,
                            end=(local + timedelta(days=1)).date().isoformat(),
                        )
                    ]
                }
            },
            "calendar.synthetic",
            now,
            end,
            "Europe/Berlin",
        ),
    )
    model = definition(active(all_day_only=True), "bool", {"a": "object"})
    result = Evaluation(model, {"a": value(cache)}, now).evaluate(model.outputs["value"])
    assert result.value is True and result.valid_until == end
    assert end - now == timedelta(hours=hours)


@pytest.mark.parametrize("mixed", [False, True])
async def test_runtime_uses_clock_alarms_for_time_reached_and_calendar_without_new_input(
    runtime, config, mixed
):
    from .conftest import activate

    now = runtime.clock.now_utc()
    cache = calendar()
    cache.update(
        fetched_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        window_start=(now - timedelta(days=1)).isoformat(),
        window_end=(now + timedelta(days=1)).isoformat(),
    )
    cache["events"] = normalize_events(
        {
            "calendar.synthetic": {
                "events": [
                    dict(
                        summary="Urlaub",
                        start=(now + timedelta(seconds=10)).isoformat(),
                        end=(now + timedelta(seconds=20)).isoformat(),
                    )
                ]
            }
        },
        "calendar.synthetic",
        now,
        now + timedelta(days=1),
        "Europe/Berlin",
    )
    reached = dict(
        op="time_reached", at=literal((now + timedelta(seconds=5)).isoformat(), "timestamp")
    )
    data = definition(reached, "bool", {}).model_dump(mode="json")
    data["inputs"] = []
    if mixed:
        data["schema"]["fields"]["active"] = schema()
        data["outputs"]["active"] = {**active(), "arg": literal(cache, "object")}
    config.update(
        config_schema_version=3,
        fingerprint_version=3,
        definitions=[data],
        sources=[],
        bindings=[],
        dependencies=[],
    )
    config["contracts"][0].update(type_id=data["type_id"], inputs=[])
    await activate(runtime, config)

    def field():
        return runtime.snapshot()["contracts"]["fixture.echo"]["fields"]

    assert field()["value"]["value"] is False
    runtime.clock.advance(5)
    await runtime.submit("tick")
    assert field()["value"]["value"] is True
    await runtime._restore("process_restart")
    if mixed:
        runtime.clock.advance(5)
        await runtime.submit("tick")
        assert field()["active"]["value"] is True
        runtime.clock.advance(10)
        await runtime.submit("tick")
        assert field()["active"]["value"] is False


@pytest.mark.parametrize(
    "rule,kind,inputs", [(curve(), "number", {"a": "text"}), (active(), "bool", {"a": "object"})]
)
def test_new_operators_require_language_5_and_roundtrip_without_mutation(rule, kind, inputs):
    model = definition(rule, kind, inputs)
    payload = model.model_dump(mode="json")
    assert (
        DeclarativeDefinition.model_validate(copy.deepcopy(payload)).model_dump(mode="json")
        == payload
    )
    for version in range(1, 5):
        with pytest.raises(ValueError, match="language_version_5_required"):
            check_definition(model.model_copy(update={"language_version": version}))
