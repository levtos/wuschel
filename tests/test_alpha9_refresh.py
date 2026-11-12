import copy

import pytest

from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_declarative import definition, observation, registry, schema
from .test_phase1a import add_source


def refresh_config(config):
    config = copy.deepcopy(config)
    config = registry(config, definition())
    config["config_schema_version"] = 2
    add_source(config, "b")
    config["bindings"][1]["refresh"] = {"profile": "entity_poll"}
    config["sources"][1]["value_schema"] = schema()
    config["contracts"][0]["refresh_plan"] = {
        "trigger_ref": "fixture.echo.value",
        "targets": ["source.b"],
        "attempt_offsets_s": [0, 10, 30],
        "window_s": 60,
        "cooldown_s": 120,
    }
    return config


def jobs(runtime):
    return [
        job
        for job in runtime.state.tables["service_job"].values()
        if job["kind"] == "refresh" and job["status"] == "planned"
    ]


async def setup(runtime, config):
    await activate(runtime, refresh_config(config))
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    await runtime.submit(
        "snapshot",
        [
            observation(runtime, False).model_copy(
                update={"observation_kind": "snapshot", "ingest_seq": runtime.state.generation + 1}
            )
        ],
    )


async def edge(runtime, value):
    runtime.clock.advance(1)
    await runtime.submit(
        "observation",
        observation(runtime, value).model_copy(update={"ingest_seq": runtime.state.generation + 1}),
    )


async def complete(runtime, outcome="completed"):
    job = await runtime.submit("service_claim")
    assert job
    await runtime.submit(
        "service_complete", {"job_id": job["job_id"], "response": {"outcome": outcome}}
    )
    return job


async def test_fixed_window_bounded_attempts_and_ack_never_renews_measurement(runtime, config):
    await setup(runtime, config)
    await edge(runtime, True)
    assert len(jobs(runtime)) == 1
    start = runtime.clock.now_utc()
    first = await complete(runtime)
    assert first["arguments"] == {"profile": "entity_poll"}
    assert "binding.b" not in runtime.state.tables["source_observation_current"]
    await edge(runtime, False)
    await edge(runtime, True)
    row = runtime.state.tables["service_state"]["refresh:fixture.echo"]
    assert row["run"]["started_at"] == start.isoformat()
    runtime.clock.advance(8)
    await runtime.submit("tick")
    assert len(jobs(runtime)) == 1
    await complete(runtime, "indeterminate")
    runtime.clock.advance(20)
    await runtime.submit("tick")
    assert len(jobs(runtime)) == 1
    await complete(runtime)
    runtime.clock.advance(31)
    await runtime.submit("tick")
    assert jobs(runtime) == []
    assert runtime.state.tables["service_state"]["refresh:fixture.echo"]["run"]["consumed"] == [
        0,
        1,
        2,
    ]


async def test_snapshot_high_is_not_an_edge_and_outage_does_not_replay_missed_attempts(
    runtime, config
):
    await setup(runtime, config)
    await runtime.submit(
        "snapshot",
        [
            observation(runtime, True).model_copy(
                update={"observation_kind": "snapshot", "ingest_seq": runtime.state.generation + 1}
            )
        ],
    )
    assert not jobs(runtime)
    await edge(runtime, False)
    await runtime.submit("bridge_state", ("unavailable", "disconnected"))
    await edge(runtime, True)
    assert not jobs(runtime)
    runtime.clock.advance(11)
    await runtime.submit("tick")
    await runtime.submit("bridge_state", ("connected", "connected"))
    assert not jobs(runtime)
    runtime.clock.advance(19)
    await runtime.submit("tick")
    assert len(jobs(runtime)) == 1


async def test_restart_never_replays_a_consumed_or_uncertain_attempt(runtime, config):
    await setup(runtime, config)
    await edge(runtime, True)
    job = await runtime.submit("service_claim")
    await runtime._restore("process_restart")
    assert runtime.state.tables["service_job"][job["job_id"]]["status"] == "indeterminate"
    assert not jobs(runtime)
    runtime.clock.advance(10)
    await runtime.submit("tick")
    assert len(jobs(runtime)) == 1


async def test_real_new_target_report_stops_requests_for_that_target(runtime, config):
    await setup(runtime, config)
    await edge(runtime, True)
    await complete(runtime)
    runtime.clock.advance(2)
    await runtime.submit("observation", observation(runtime, True, "b"))
    runtime.clock.advance(30)
    await runtime.submit("tick")
    assert not jobs(runtime)
    assert runtime.state.tables["service_state"]["refresh:fixture.echo"]["run"]["received"] == [
        "source.b"
    ]


@pytest.mark.parametrize(
    "patch",
    [
        {"attempt_offsets_s": [0, 0]},
        {"attempt_offsets_s": [1, 10]},
        {"attempt_offsets_s": [0, 9]},
        {"attempt_offsets_s": [0, 60]},
        {"window_s": 301},
        {"cooldown_s": 10},
        {"trigger_ref": "source.a"},
        {"targets": ["source.missing"]},
    ],
)
def test_refresh_plan_validation_is_typed_and_bounded(runtime, config, patch):
    data = refresh_config(copy.deepcopy(config))
    data["contracts"][0]["refresh_plan"].update(patch)
    with pytest.raises(ValueError):
        validate(data, runtime.types)


async def test_permanent_error_and_disabling_cancel_remaining_requests(runtime, config):
    await setup(runtime, config)
    await edge(runtime, True)
    await complete(runtime, "rejected")
    runtime.clock.advance(30)
    await runtime.submit("tick")
    assert not jobs(runtime)
    data = refresh_config(config)
    data["contracts"][0]["enabled"] = False
    await activate(runtime, data)
    assert not jobs(runtime)
    assert (
        runtime.state.tables["service_state"]["refresh:fixture.echo"]["run"]["status"]
        == "cancelled"
    )


async def test_true_stop_contract_prevents_request_and_unknown_is_not_success(runtime, config):
    data = refresh_config(config)
    data["contracts"][0]["refresh_plan"]["stop_when"] = {"ref": "fixture.echo.value"}
    await activate(runtime, data)
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    await edge(runtime, False)
    await edge(runtime, True)
    assert not jobs(runtime)
    assert (
        runtime.state.tables["service_state"]["refresh:fixture.echo"]["run"]["status"] == "stopped"
    )


async def test_legacy_and_plan_requests_share_persisted_quota(runtime, config):
    await setup(runtime, config)
    payload = {"source_id": "source.b", "revision": runtime.state.active_revision}
    first = await runtime.submit("legacy_refresh_prepare", payload)
    assert first["status"] == "sent"
    await edge(runtime, True)
    assert not jobs(runtime)
    assert (
        runtime.state.tables["service_state"]["refresh:fixture.echo"]["run"]["attempts"][0][
            "outcome"
        ]
        == "quota"
    )
    await runtime._restore("process_restart")
    assert await runtime.submit("legacy_refresh_prepare", payload) is None
    runtime.clock.advance(10)
    await runtime.submit("tick")
    assert len(jobs(runtime)) == 1
    assert await runtime.submit("legacy_refresh_prepare", payload) is None


async def test_commit_failure_cannot_grant_a_refresh_request(runtime, config):
    await setup(runtime, config)
    before = copy.deepcopy(runtime.state.tables["service_job"])
    runtime.store.before_commit = lambda: (_ for _ in ()).throw(OSError("failure"))
    try:
        with pytest.raises(OSError):
            await runtime.submit(
                "legacy_refresh_prepare",
                {"source_id": "source.b", "revision": runtime.state.active_revision},
            )
        assert runtime.state.tables["service_job"] == before
    finally:
        runtime.store.before_commit = None


def test_companion_permission_and_refresh_target_cannot_be_bypassed(runtime, config):
    data = refresh_config(config)
    data["bindings"][1]["refresh"] = {"profile": "companion_location"}
    with pytest.raises(ValueError):
        validate(data, runtime.types)
    data["bindings"][1]["refresh"]["permission_confirmed"] = True
    with pytest.raises(ValueError):
        validate(data, runtime.types)
