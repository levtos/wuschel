"""The importable presence definitions execute through the production engines."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core_contracts.clock import FakeClock
from core_contracts.contract_types import type_registry
from core_contracts.declarative import check_definition, compile_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.evidence import Observation
from core_contracts.persistence import State
from core_contracts.portable import objects, read_package
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.registry import Contract, fingerprints, validate

from .conftest import activate
from .test_core import NOW

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = json.loads((ROOT / "tests/fixtures/presence-package-v1.json").read_text(encoding="utf-8"))
BASELINE = json.loads(
    (ROOT / "tests/fixtures/presence-baseline-v1.json").read_text(encoding="utf-8")
)
DEFINITIONS = json.loads((ROOT / "examples/presence-templates-v1.json").read_text(encoding="utf-8"))


class Harness:
    def __init__(self, name):
        self.definition = DeclarativeDefinition.model_validate(
            next(d for d in DEFINITIONS if d["type_id"] == "presence." + name)
        )
        check_definition(self.definition)
        self.kind = compile_definition(self.definition)
        self.contract = Contract.model_validate(
            next(
                c for c in PACKAGE["config"]["contracts"] if c["type_id"] == self.definition.type_id
            )
        )
        self.clock = FakeClock(NOW)
        self.node = {"fingerprint": "synthetic"}
        self.state = State(active_revision=1, epoch_id="synthetic")
        self.latches = {}
        self.case = "first"

    def evaluate(self, *, origin="input", **values):
        inputs = {
            key: (
                value
                if isinstance(value, FieldValue)
                else unknown(ReasonCode.INPUT_UNKNOWN, key, self.clock.now_utc())
                if value is None
                else FieldValue(
                    status="valid",
                    quality="healthy",
                    value=value,
                    measured_at=self.clock.now_utc(),
                    evidence=(f"synthetic:{key}",),
                )
            )
            for key, value in values.items()
        }
        fields, self.node, _ = self.kind.evaluate(
            self.clock,
            self.latches,
            self.contract,
            inputs,
            self.node,
            self.state,
            origin,
            self.case,
            None,
        )
        self.case = "valid"
        return fields["value"]


@pytest.mark.parametrize(
    "icloud,app,router,wifi,expected",
    [
        (None, "not_home", None, "SYNTHETIC_HOME_A", "home"),
        (None, None, False, "SYNTHETIC_HOME_B", "home"),
        ("not_home", None, False, "SYNTHETIC_HOME_A", "away"),
        ("home", None, False, None, "home"),
        ("not_home", None, False, None, "away"),
        (None, None, True, None, "parents"),
        ("home", None, False, "SYNTHETIC_PARENTS", "home"),
        ("home", None, None, "SYNTHETIC_PARENTS", "parents"),
        (None, None, None, None, None),
        ("Synthetic Zone", None, False, None, "away"),
    ],
)
def test_location_priority_and_named_zones(icloud, app, router, wifi, expected):
    result = Harness("location").evaluate(icloud=icloud, app=app, router=router, wifi=wifi)
    assert result.value == expected
    assert result.status == ("unknown" if expected is None else "valid")
    assert ReasonCode.EVALUATION_ERROR not in {reason.code for reason in result.reasons}


def test_no_match_does_not_degrade_a_fully_evidenced_location():
    result = Harness("location").evaluate(
        icloud="not_home", app="not_home", router=False, wifi="SYNTHETIC_OTHER"
    )
    assert result.value == "away" and result.quality == "healthy"


@pytest.mark.parametrize(
    "name,good,bad",
    [
        (
            "location",
            dict(icloud="home", app="home", router=False, wifi="SYNTHETIC_HOME_A"),
            dict(icloud=None, app=None, router=None, wifi=None),
        ),
        ("place", dict(icloud="home", app="not_home"), dict(icloud=None, app=None)),
    ],
)
def test_grace_is_finite_and_restart_does_not_extend_it(name, good, bad):
    h = Harness(name)
    assert h.evaluate(**good).status == "valid"
    h.clock.advance(1)
    held = h.evaluate(**bad)
    assert held.status == "held" and held.held_until == h.clock.now_utc() + timedelta(seconds=360)
    h.clock.advance(100)
    h.node = json.loads(json.dumps(h.node))
    assert h.evaluate(origin="restore", **bad).held_until == held.held_until
    h.clock.advance(259)
    assert h.evaluate(origin="tick", **bad).status == "held"
    h.clock.advance(1)
    assert h.evaluate(origin="tick", **bad).status == "unknown"
    h.clock.advance(1)
    assert h.evaluate(**bad).status == "unknown"


@pytest.mark.parametrize(
    "icloud,app,expected",
    [
        ("home", "not_home", "Zuhause"),
        ("not_home", "home", "unterwegs"),
        ("Synthetic Zone", "home", "Synthetic Zone"),
        (None, "App Zone", "App Zone"),
        (None, None, None),
        ("Priority Zone", "Other Zone", "Priority Zone"),
    ],
)
def test_place_preserves_zone_names_without_a_catalog(icloud, app, expected):
    result = Harness("place").evaluate(icloud=icloud, app=app)
    assert result.value == expected and result.status == (
        "unknown" if expected is None else "valid"
    )


def test_band_sequence_initial_ambiguity_and_parameter_order():
    h = Harness("distance_band")
    assert h.evaluate(distance=120).status == "unknown"
    values = [h.evaluate(distance=value).value for value in [0, 120, 160, 820, 860, 820, 790, 9000]]
    assert values == ["home", "home", "preheat", "preheat", "near", "near", "preheat", "far"]
    assert h.evaluate(distance=None).status == "unknown"
    assert h.evaluate(distance=820).status == "unknown"
    parsed = h.kind.parameters.model_validate(h.contract.parameters)
    assert parsed.model_dump()["lower_1"] == 100
    with pytest.raises(ValueError):
        h.kind.parameters.model_validate({**h.contract.parameters, "upper_1": 900})


@pytest.mark.parametrize(
    "location,visitors,expected",
    [
        ("home", False, True),
        ("parents", False, True),
        ("away", True, True),
        ("away", False, False),
        (None, False, None),
        (None, True, True),
    ],
)
def test_household_truth_is_three_valued_and_parents_count_as_here(location, visitors, expected):
    result = Harness("household_occupied").evaluate(location=location, visitors=visitors)
    assert result.value is expected


@pytest.mark.parametrize(
    "location,direction,band,expected",
    [
        ("away", "towards", "preheat", True),
        ("away", "towards", "near", True),
        ("parents", "towards", "near", False),
        ("away", "away_from", "near", False),
        ("away", "towards", "far", False),
        (None, "towards", "near", None),
    ],
)
def test_approaching(location, direction, band, expected):
    assert (
        Harness("approaching").evaluate(location=location, direction=direction, band=band).value
        is expected
    )


@pytest.mark.parametrize("here", ["home", "parents"])
def test_arrival_departure_here_equivalence_and_original_hold(here):
    arrival = Harness("arrival")
    assert arrival.evaluate(location=here).value is False
    assert arrival.evaluate(location="away").value is False
    arrival.clock.advance(1)
    assert arrival.evaluate(location=here).value is True
    deadline = arrival.node["machine"]["deadline"]
    arrival.clock.advance(30)
    assert arrival.evaluate(location="parents" if here == "home" else "home").value is True
    assert arrival.node["machine"]["deadline"] == deadline
    arrival.clock.advance(90)
    assert arrival.evaluate(origin="tick", location=here).value is False
    departure = Harness("departure")
    assert departure.evaluate(location=here).value is False
    assert departure.evaluate(location="parents" if here == "home" else "home").value is False
    assert departure.evaluate(location="away").value is True
    departure.clock.advance(120)
    assert departure.evaluate(origin="tick", location="away").value is False


def test_first_start_away_is_not_departure_and_unknown_does_not_create_arrival():
    assert Harness("departure").evaluate(location="away").value is False
    arrival = Harness("arrival")
    assert arrival.evaluate(location=None).value is False
    assert arrival.evaluate(location="home").value is False


@pytest.mark.parametrize("destination", ["home", "parents", "away"])
def test_passing_and_arrival_at_either_here_location(destination):
    h = Harness("passing")
    assert h.evaluate(location="away", band="far").value is False
    assert h.evaluate(location="away", band="near").value is False
    result = h.evaluate(location=destination, band="far" if destination == "away" else "home")
    assert result.value is (destination == "away")
    assert h.node["machine"]["state"] == "done"
    h.clock.advance(120)
    assert h.evaluate(origin="tick", location=destination, band="far").value is False


def test_unknown_keeps_due_machine_deadline_until_valid_evidence_returns():
    h = Harness("arrival")
    h.evaluate(location="away")
    h.evaluate(location="parents")
    original = copy.deepcopy(h.node["machine"]["deadline"])
    h.clock.advance(120)
    assert h.evaluate(origin="tick", location=None).status == "unknown"
    assert h.node["machine"]["deadline"] == original
    assert h.evaluate(location="parents").value is False


@pytest.mark.parametrize(
    "name,first,second",
    [
        ("arrival", {"location": "away"}, {"location": "home"}),
        ("arrival", {"location": "away"}, {"location": "parents"}),
        ("departure", {"location": "home"}, {"location": "away"}),
        ("departure", {"location": "parents"}, {"location": "away"}),
        ("passing", {"location": "away", "band": "near"}, {"location": "away", "band": "far"}),
        ("passing", {"location": "away", "band": "preheat"}, {"location": "away", "band": "far"}),
    ],
)
def test_reentry_during_hold_cancels_old_deadline_and_rearms_following_cycles(name, first, second):
    h = Harness(name)
    assert h.evaluate(**first).value is False
    h.clock.advance(1)
    assert h.evaluate(**second).value is True
    old_deadline = copy.deepcopy(h.node["machine"]["deadline"])
    h.clock.advance(30)
    assert h.evaluate(**first).value is False
    waiting = copy.deepcopy(h.node["machine"])
    assert waiting["deadline"] is None
    h.clock.advance(90)
    assert h.evaluate(origin="tick", **first).value is False
    assert h.node["machine"]["state"] == waiting["state"]
    assert h.node["machine"]["deadline"] is None
    h.clock.advance(3480)
    assert h.evaluate(**second).value is True
    new_deadline = h.node["machine"]["deadline"]
    assert new_deadline["deadline_id"] != old_deadline["deadline_id"]
    assert datetime.fromisoformat(new_deadline["at"]) == h.clock.now_utc() + timedelta(seconds=120)
    h.clock.advance(120)
    assert h.evaluate(origin="tick", **second).value is False
    assert h.node["machine"]["state"] == "idle"
    h.clock.advance(1)
    assert h.evaluate(**first).value is False
    h.clock.advance(1)
    assert h.evaluate(**second).value is True


@pytest.mark.parametrize(
    "name,first,second",
    [
        ("arrival", {"location": "away"}, {"location": "parents"}),
        ("departure", {"location": "parents"}, {"location": "away"}),
        ("passing", {"location": "away", "band": "near"}, {"location": "away", "band": "far"}),
    ],
)
async def test_real_runtime_restart_preserves_machine_state_and_deadline(
    runtime, name, first, second
):
    h = Harness(name)
    doc = dict(
        config_schema_version=2,
        fingerprint_version=2,
        definitions=[h.definition.model_dump(mode="json")],
        dependencies=[dict(dependency_id="synthetic.ha", kind="ha", monitor="internal")],
        sources=[],
        bindings=[],
        catalogs=[],
        contracts=[],
    )
    contract = h.contract.model_dump(mode="json")
    contract["inputs"] = []
    for declaration in h.definition.inputs:
        key = declaration.name
        doc["sources"].append(
            dict(
                source_id="source." + key,
                source_type="state",
                source_origin="local",
                dependency_id="synthetic.ha",
                physical_source_key="synthetic." + key,
                freshness=dict(mode="event_stateful", future_tolerance_s=0),
                value_schema=declaration.field_schema.model_dump(mode="json"),
            )
        )
        doc["bindings"].append(
            dict(
                binding_id="binding." + key,
                source_id="source." + key,
                adapter=dict(kind="ha_state", ha_entity_id="sensor.synthetic_" + key),
            )
        )
        contract["inputs"].append(dict(name=key, ref="source." + key))
    doc["contracts"].append(contract)
    await activate(runtime, doc)

    async def send(values):
        for key, value in values.items():
            await runtime.submit(
                "observation",
                Observation(
                    source_id="source." + key,
                    binding_id="binding." + key,
                    adapter="ha_state",
                    value_normalized=value,
                    normalized=True,
                    received_at=runtime.clock.now_utc(),
                    device_time=runtime.clock.now_utc(),
                    observation_kind="live_change",
                    epoch_id=runtime.state.epoch_id,
                    ingest_seq=runtime.state.generation + 1,
                    reference_version=2,
                    registry_revision=runtime.state.active_revision,
                ),
            )

    await send(first)
    runtime.clock.advance(1)
    await send(second)
    before = copy.deepcopy(runtime.state.tables["node_state"][contract["contract_id"]]["machine"])
    assert before["deadline"] is not None
    runtime.clock.advance(30)
    await runtime._restore("process_restart")
    after = runtime.state.tables["node_state"][contract["contract_id"]]["machine"]
    assert (after["state"], after["deadline"]) == (before["state"], before["deadline"])
    await send(second)
    runtime.clock.advance(90)
    await runtime.submit("tick")
    assert runtime.state.tables["node_state"][contract["contract_id"]]["machine"]["state"] == "idle"


async def test_synthetic_package_adds_only_presence_preserves_fingerprints_and_preview_is_read_only(
    runtime,
):
    runtime.types = type_registry()
    baseline = read_package(BASELINE)
    package = read_package(PACKAGE)
    assert package["config"]["definitions"][-9:] == [
        d.model_dump(mode="json") for d in map(DeclarativeDefinition.model_validate, DEFINITIONS)
    ]
    before = validate(baseline["config"], type_registry())
    after = validate(package["config"], type_registry())
    old_objects = objects(baseline["config"])
    new_objects = objects(package["config"])
    assert all(new_objects[key] == value for key, value in old_objects.items())
    old_fingerprints = fingerprints(before, type_registry())
    new_fingerprints = fingerprints(after, type_registry())
    assert all(new_fingerprints[key] == value for key, value in old_fingerprints.items())
    assert all(not c.ha_projection for c in after.contracts if c.type_id.startswith("presence."))
    await activate(runtime, baseline["config"])
    state = copy.deepcopy(runtime.state.model_dump())
    preview = await runtime.submit("portable_preview", {"package": PACKAGE})
    assert preview["unresolved"] == [] and preview["conflicts"] == []
    assert preview["added"] == 34 and preview["reused"] == len(old_objects)
    assert runtime.state.model_dump() == state
    assert (await runtime.submit("portable_preview", {"package": PACKAGE}))[
        "plan_digest"
    ] == preview["plan_digest"]
