"""UX-3/4: synthetic registry ownership and original-time F3 evidence matrix."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from core_contracts.clock import FakeClock
from core_contracts.contract_types import type_registry
from core_contracts.freshness_templates import effective_freshness, upgrade_document
from core_contracts.persistence import MemoryStore
from core_contracts.registry import (
    assessment_fingerprints,
    context_compatibility,
    diff,
    observation_fingerprints,
    storage_document,
    validate,
)
from core_contracts.runtime import Runtime
from tests.conftest import activate
from tests.test_beta_openings import closed, configuration, field, report
from tests.test_core import NOW


def legacy():
    document = configuration()
    document.update(config_schema_version=3, fingerprint_version=3)
    for row in document["sources"][-2:]:
        row["freshness"]["interval_s"] = 10800
    return document


def shared(document, interval=10800):
    result = upgrade_document(document)
    result["freshness_templates"] = [
        dict(
            template_id="freshness.test.contacts",
            display_name="Synthetic contacts",
            sensor_kind="contact",
            rule=dict(mode="report_heartbeat", interval_s=interval, future_tolerance_s=0),
        )
    ]
    for row in result["sources"]:
        row.update(
            sensor_kind="contact",
            sensor_kind_origin="confirmed",
            freshness_selection=dict(kind="shared", template_id="freshness.test.contacts"),
        )
    return result


async def running(document=None):
    runtime = Runtime(MemoryStore(), FakeClock(NOW), type_registry())
    await runtime.start()
    await activate(runtime, document or legacy())
    for index in range(4):
        await closed(runtime, index)
    return runtime


def test_schema_4_own_shared_resolution_and_legacy_readability():
    types = type_registry()
    for version in (1, 2, 3):
        document = dict(
            config_schema_version=version,
            fingerprint_version=version,
            dependencies=[],
            sources=[],
            bindings=[],
            contracts=[],
            catalogs=[],
        )
        old = validate(document, types)
        upgraded = validate(upgrade_document(storage_document(old)), types)
        assert upgraded.config_schema_version == upgraded.fingerprint_version == 4
        assert old.config_schema_version == version
    original = legacy()
    upgraded = upgrade_document(original)
    assert original["config_schema_version"] == 3
    own = validate(upgraded, types)
    assert [row.freshness for row in own.sources] == [
        row.freshness for row in validate(original, types).sources
    ]
    assert all(row.freshness_selection.kind == "own" for row in own.sources)
    assert not own.freshness_templates
    config = validate(shared(original), types)
    document = storage_document(config)
    assert all("freshness" not in row for row in document["sources"])
    assert storage_document(validate(document, types)) == document
    resolved = effective_freshness(config)
    usage = resolved["templates"]["freshness.test.contacts"]
    assert len(usage["source_ids"]) == 7 and len(usage["contract_ids"]) == 4
    assert len(set(usage["source_ids"])) == 7
    assert all(row["rule"]["interval_s"] == 10800 for row in resolved["sources"].values())


@pytest.mark.parametrize(
    "change",
    [
        "double",
        "missing",
        "unconfirmed",
        "wrong_kind",
        "duplicate",
        "liveness_missing",
        "liveness_cycle",
        "legacy_selection",
        "old_fingerprint",
    ],
)
def test_ambiguous_missing_and_cyclic_selections_are_rejected(change):
    document = shared(legacy())
    source = document["sources"][0]
    if change == "double":
        source["freshness"] = dict(mode="event_stateful", future_tolerance_s=0)
    elif change == "missing":
        document["freshness_templates"] = []
    elif change == "unconfirmed":
        source.pop("sensor_kind_origin")
    elif change == "wrong_kind":
        source["sensor_kind"] = "temperature"
    elif change == "duplicate":
        document["freshness_templates"] *= 2
    elif change.startswith("liveness"):
        document["freshness_templates"][0]["rule"]["mode"] = "liveness_source"
        if change == "liveness_cycle":
            for row in document["sources"]:
                row["freshness_selection"]["liveness_source"] = row["source_id"]
    elif change == "legacy_selection":
        document["config_schema_version"] = 3
    else:
        document["fingerprint_version"] = 3
    with pytest.raises(ValueError):
        validate(document, type_registry())


def test_own_equal_is_still_independent_and_differences_are_explicit():
    document = shared(legacy())
    first = document["sources"][0]
    first["freshness_selection"] = dict(
        kind="own",
        comparison_template_id="freshness.test.contacts",
        rule=dict(mode="report_heartbeat", interval_s=10800, future_tolerance_s=0),
    )
    config = validate(document, type_registry())
    assert effective_freshness(config)["sources"][first["source_id"]]["differs"] is False
    document["freshness_templates"][0]["rule"]["interval_s"] = 14400
    config = validate(document, type_registry())
    resolved = effective_freshness(config)
    assert resolved["sources"][first["source_id"]]["differs"] is True
    assert config.sources[0].freshness.interval_s == 10800
    assert config.sources[1].freshness.interval_s == 14400


async def test_real_case_seven_contacts_four_openings_three_zones_preserve_original_evidence():
    runtime = await running()
    try:
        before = deepcopy(runtime.state.tables["source_observation_current"])
        nodes = deepcopy(runtime.state.tables["node_state"])
        runtime.clock.advance(9001)
        await runtime.submit("tick")
        assert field(runtime)["status"] == "unknown"
        await activate(runtime, shared(legacy()))
        observations = runtime.state.tables["source_observation_current"]
        for key, row in before.items():
            assert {name: value for name, value in row.items() if name != "mqtt_device_proof"} == {
                name: value
                for name, value in observations[key].items()
                if name != "mqtt_device_proof"
            }
        assert field(runtime)["status"] == "valid"
        assert datetime.fromisoformat(field(runtime)["measured_at"]) == NOW
        assert datetime.fromisoformat(field(runtime)["since_at"]) == runtime.clock.now_utc()
        assert datetime.fromisoformat(
            field(runtime, "test.opening3")["since_at"]
        ) == datetime.fromisoformat(nodes["test.opening3"]["since_at"])
        assert all(
            field(runtime, f"test.zone.{name}", "all_closed")["value"] is True
            for name in ("windows", "area", "all")
        )
        usage = effective_freshness(runtime.config)["templates"]["freshness.test.contacts"]
        assert len(usage["source_ids"]) == 7 and len(usage["contract_ids"]) == 4
    finally:
        await runtime.stop()


async def test_identical_effective_rule_preserves_since_grace_and_observation_without_revision_edge():
    document = shared(legacy())
    runtime = await running(document)
    try:
        original = deepcopy(runtime.state.tables["source_observation_current"])
        nodes = deepcopy(runtime.state.tables["node_state"])
        runtime.clock.advance(100)
        renamed = deepcopy(document)
        renamed["freshness_templates"][0]["template_id"] = "freshness.test.other"
        for row in renamed["sources"]:
            row["freshness_selection"]["template_id"] = "freshness.test.other"
        change = diff(runtime.config, validate(renamed, runtime.types), runtime.types)
        assert change["effective_freshness_changes"] == [] and change["affected_contracts"] == []
        await activate(runtime, renamed)
        assert runtime.state.tables["source_observation_current"] == original
        assert all(
            runtime.state.tables["node_state"][key].get("since_at") == node.get("since_at")
            for key, node in nodes.items()
        )
        assert runtime.next_evaluation == NOW + timedelta(seconds=10800, microseconds=1)
    finally:
        await runtime.stop()


@pytest.mark.parametrize(
    "age,new_interval,expected",
    [(60, 100, "valid"), (60, 30, "unknown"), (9001, 10800, "valid"), (9001, 8000, "unknown")],
)
async def test_original_time_interval_matrix(age, new_interval, expected):
    runtime = await running(shared(legacy(), 8400))
    try:
        original = deepcopy(runtime.state.tables["source_observation_current"])
        runtime.clock.advance(age)
        await runtime.submit("tick")
        await activate(runtime, shared(legacy(), new_interval))
        assert field(runtime)["status"] == expected
        assert runtime.state.tables["source_observation_current"] == original
        if expected == "valid":
            assert datetime.fromisoformat(field(runtime)["measured_at"]) == NOW
        else:
            await runtime.submit("tick")
            assert field(runtime)["status"] == "unknown"
    finally:
        await runtime.stop()


@pytest.mark.parametrize("version", [1, 2, 3])
async def test_proven_legacy_fingerprint_to_v4_preserves_usable_nodes(version):
    document = legacy()
    document.update(config_schema_version=version, fingerprint_version=version)
    runtime = await running(document)
    try:
        original = deepcopy(runtime.state.tables["source_observation_current"])
        since = field(runtime)["since_at"]
        runtime.clock.advance(30)
        await activate(runtime, upgrade_document(document))
        assert field(runtime)["status"] == "valid" and field(runtime)["since_at"] == since
        for key, row in original.items():
            assert (
                runtime.state.tables["source_observation_current"][key]["ingest_seq"]
                == row["ingest_seq"]
            )
    finally:
        await runtime.stop()


async def test_commit_failure_keeps_old_assessment_observation_and_publication():
    runtime = await running(shared(legacy(), 8400))
    try:
        observations = deepcopy(runtime.state.tables["source_observation_current"])
        nodes = deepcopy(runtime.state.tables["node_state"])
        revision, sequence = runtime.state.active_revision, runtime.state.publication_seq
        draft = await runtime.submit("draft_create", {"config": shared(legacy(), 10800)})
        from tests.test_beta3_authoring import envelope

        request = await envelope(runtime, draft)
        runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("synthetic failure"))
        with pytest.raises(OSError):
            await runtime.submit("activate", request)
        assert (
            runtime.state.active_revision == revision and runtime.state.publication_seq == sequence
        )
        assert runtime.state.tables["source_observation_current"] == observations
        assert runtime.state.tables["node_state"] == nodes
    finally:
        runtime.store.before_commit = None
        await runtime.stop()


@pytest.mark.parametrize("change", ["same", "longer", "shorter"])
async def test_hysteresis_latch_and_absolute_grace_are_compatible_only_with_same_interpretation(
    runtime, config, change
):
    from tests.test_alpha4_review import exercise_latch_restore
    from tests.test_phase1a import field as fixture_field

    deadline = await exercise_latch_restore(runtime, config)
    before = deepcopy(runtime.latches)
    document = upgrade_document(storage_document(runtime.config))
    for source in document["sources"]:
        rule = source["freshness_selection"]["rule"]
        if rule.get("interval_s"):
            rule["interval_s"] *= 2 if change == "longer" else 0.5 if change == "shorter" else 1
    await activate(runtime, document)
    assert runtime.latches == before
    assert fixture_field(runtime)["value"] is True
    assert fixture_field(runtime, key="held")["held_until"] == deadline


async def test_no_refresh_episode_quota_or_budget_renewal(runtime, config):
    from tests.test_alpha9_refresh import edge, setup

    await setup(runtime, config)
    await edge(runtime, True)
    refresh = deepcopy(
        {
            key: row
            for key, row in runtime.state.tables["service_state"].items()
            if key.startswith("refresh:")
        }
    )
    jobs = deepcopy(runtime.state.tables["service_job"])
    document = upgrade_document(storage_document(runtime.config))
    for source in document["sources"]:
        rule = source["freshness_selection"]["rule"]
        if rule.get("interval_s"):
            rule["interval_s"] *= 2
    await activate(runtime, document)
    assert {
        key: row
        for key, row in runtime.state.tables["service_state"].items()
        if key.startswith("refresh:")
    } == refresh
    assert runtime.state.tables["service_job"] == jobs


@pytest.mark.parametrize("operation", ["stable_for", "dwell", "edge"])
async def test_continuity_and_edge_semantics_on_neutral_shorter_and_reviving_rules(
    runtime, config, operation
):
    from tests.test_remediation import field as fixture_field
    from tests.test_remediation import temporal
    from tests.test_runtime import obs

    temporal(config, operation, duration=1000)
    config["sources"][0]["freshness"] = dict(
        mode="report_heartbeat", interval_s=100, future_tolerance_s=0
    )
    await activate(runtime, config)
    await runtime.submit("observation", obs(runtime, True))
    original_since = runtime.state.tables["node_state"]["fixture.echo"]["temporal"]["since_at"]
    runtime.clock.advance(50)
    document = upgrade_document(storage_document(runtime.config))
    document["sources"][0]["freshness_selection"]["rule"]["interval_s"] = 200
    await activate(runtime, document)
    assert fixture_field(runtime)["value"] is False
    assert (
        runtime.state.tables["node_state"]["fixture.echo"]["temporal"]["since_at"] == original_since
    )
    runtime.clock.advance(151)
    await runtime.submit("tick")
    assert fixture_field(runtime)["status"] == "unknown"
    document["sources"][0]["freshness_selection"]["rule"]["interval_s"] = 400
    await activate(runtime, document)
    assert fixture_field(runtime)["value"] is False
    if operation != "edge":
        assert (
            datetime.fromisoformat(
                runtime.state.tables["node_state"]["fixture.echo"]["temporal"]["since_at"]
            )
            == runtime.clock.now_utc()
        )


async def test_machine_episode_and_deadlines_are_not_renewed_by_v4_upgrade(runtime, config):
    from tests.test_alpha7_manual_restart import machine, setup_machine
    from tests.test_declarative import observation

    await setup_machine(runtime, config)
    await runtime.submit("observation", observation(runtime, True))
    before = deepcopy(machine(runtime))
    runtime.clock.advance(1)
    await activate(runtime, upgrade_document(storage_document(runtime.config)))
    assert machine(runtime) == before


@pytest.mark.parametrize(
    "flags",
    [
        {"ha_restored": True},
        {"assumed_state": True},
        {"observation_kind": "restore", "mqtt_device_proof": None},
        {"device_time": None},
        {"availability": "unavailable"},
        {"device_time": NOW + timedelta(seconds=10)},
    ],
)
async def test_hard_evidence_gates_are_unchanged_under_longer_freshness(flags):
    from core_contracts.evidence import Observation

    runtime = await running(shared(legacy(), 8400))
    try:
        original = runtime.state.tables["source_observation_current"]["source.test.0.open"]
        observation = Observation.model_validate(original).model_copy(update=flags)
        store, clock, types = runtime.store, runtime.clock, runtime.types
        await runtime.stop()
        store.state.tables["source_observation_current"]["source.test.0.open"] = (
            observation.storage_document()
        )
        runtime = Runtime(store, clock, types)
        await runtime.start()
        await activate(runtime, shared(legacy(), 10800))
        assert field(runtime)["status"] != "valid"
    finally:
        await runtime.stop()


async def test_running_and_expired_grace_never_extend_from_revision():
    runtime = await running(shared(legacy(), 8400))
    try:
        runtime.clock.advance(8401)
        await runtime.submit("tick")
        deadline = field(runtime)["held_until"]
        await activate(runtime, shared(legacy(), 8300))
        assert field(runtime)["held_until"] == deadline
        runtime.clock.advance(600)
        await runtime.submit("tick")
        assert field(runtime)["status"] == "unknown"
        await activate(runtime, shared(legacy(), 8200))
        assert field(runtime)["status"] == "unknown"
        assert (
            runtime.state.tables["node_state"]["test.opening0"]["temporal"]["grace_until"]
            == deadline
        )
    finally:
        await runtime.stop()


@pytest.mark.parametrize("change", ["transform", "time_path", "catalog", "physical", "binding"])
async def test_interpretation_changes_keep_strict_observation_boundary(change):
    document = shared(legacy())
    runtime = await running(document)
    try:
        changed = deepcopy(document)
        if change == "transform":
            changed["bindings"][0]["transform"] = dict(kind="type_cast", target="bool")
        elif change == "time_path":
            changed["bindings"][0]["adapter"]["mqtt_time_path"] = "other_time"
        elif change == "catalog":
            changed["catalogs"][0].update(version=2, entries={"True": True, "False": False})
        elif change == "physical":
            changed["sources"][0]["physical_source_key"] = "test.other"
        else:
            changed["bindings"][0]["binding_id"] = "test.other.binding"
        await activate(runtime, changed)
        assert "source.test.0.open" not in runtime.state.tables["source_observation_current"]
    finally:
        await runtime.stop()


def test_three_comparisons_do_not_conflate_interval_and_identity():
    types = type_registry()
    a, b = validate(shared(legacy(), 8400), types), validate(shared(legacy(), 10800), types)
    assert observation_fingerprints(a) == observation_fingerprints(b)
    assert assessment_fingerprints(a) != assessment_fingerprints(b)
    assert context_compatibility(a, types) == context_compatibility(b, types)


async def test_legacy_b3_own_interval_edits_get_f3_without_forcing_a_shared_assignment():
    runtime = await running(legacy())
    try:
        observations = deepcopy(runtime.state.tables["source_observation_current"])
        since = field(runtime)["since_at"]
        runtime.clock.advance(60)
        document = legacy()
        for source in document["sources"]:
            source["freshness"]["interval_s"] = 10800
        await activate(runtime, document)
        assert runtime.config.config_schema_version == 3
        assert field(runtime)["status"] == "valid" and field(runtime)["since_at"] == since
        for key, before in observations.items():
            assert (
                runtime.state.tables["source_observation_current"][key]["ingest_seq"]
                == before["ingest_seq"]
            )
    finally:
        await runtime.stop()


async def test_unproven_node_context_does_not_receive_compatibility_from_a_template_change():
    runtime = await running(legacy())
    try:
        runtime.state.tables["node_state"]["test.opening0"]["fingerprint"] = "unproven"
        runtime.clock.advance(30)
        await activate(runtime, shared(legacy()))
        assert datetime.fromisoformat(field(runtime)["since_at"]) == runtime.clock.now_utc()
        assert (
            runtime.state.tables["node_state"]["test.opening0"]["temporal"]["grace_armed"] is False
        )
    finally:
        await runtime.stop()


@pytest.mark.parametrize(
    "retained,raw", [(True, None), (False, {"contact": True, "last_seen": "invalid"})]
)
async def test_longer_interval_does_not_promote_invalid_or_retained_evidence(retained, raw):
    runtime = await running(shared(legacy(), 8400))
    try:
        if raw:
            from core_contracts.evidence import Observation

            observation = Observation.model_validate(
                runtime.state.tables["source_observation_current"]["source.test.0.open"]
            )
            await runtime.submit(
                "observation", observation.model_copy(update={"invalid_timestamp": True})
            )
        else:
            # b5 intentionally ignores an identical retained replay. This test
            # needs genuinely invalid evidence before changing the interval.
            await report(runtime, retained=retained, value=True)
        assert field(runtime)["status"] != "valid"
        await activate(runtime, shared(legacy(), 10800))
        assert field(runtime)["status"] != "valid"
    finally:
        await runtime.stop()
