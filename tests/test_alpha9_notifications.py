from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core_contracts.notifications import complete_notice, plan_notices
from core_contracts.persistence import State
from core_contracts.registry import RegistryConfig

from .conftest import activate

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def document(config):
    return RegistryConfig.model_validate(
        {
            **config,
            "config_schema_version": 2,
            "notifications": {
                "signal": {
                    "enabled": True,
                    "debounce_s": 30,
                    "clear_after_s": 10,
                    "min_update_s": 300,
                },
                "configuration": {
                    "enabled": True,
                    "debounce_s": 60,
                    "clear_after_s": 10,
                    "min_update_s": 300,
                },
                "max_items": 10,
            },
        }
    )


def problem(status="unknown", incomplete=False):
    return {
        "status": status,
        "reasons": [{"code": "input_stale", "detail": "private secret"}],
        "configuration": {
            "complete": not incomplete,
            "hints": [{"code": "activity_mapping_missing", "message": "private title"}]
            if incomplete
            else [],
        },
    }


def jobs(state):
    return [job for job in state.tables["service_job"].values() if job["status"] == "planned"]


def acknowledge(state, job, at):
    job["status"] = "finished"
    complete_notice(state, job, {"outcome": "completed"}, at)


def test_axes_debounce_individual_onsets_unchanged_digest_and_own_link(config):
    state = State(epoch_id="epoch")
    state.tables["contract_state_current"]["fixture.echo"] = {
        "fields": {"bad": problem(), "unassigned": problem("valid", True), "grace": problem("held")}
    }
    conf, installation = document(config), str(uuid4())
    plan_notices(state, conf, NOW, installation)
    assert jobs(state) == []
    state.tables["contract_state_current"]["fixture.echo"]["fields"]["another"] = problem(
        "unresolved"
    )
    plan_notices(state, conf, NOW + timedelta(seconds=29), installation)
    plan_notices(state, conf, NOW + timedelta(seconds=30), installation, "abcd1234_wuschel")
    signal = jobs(state)[0]
    assert signal["target_ids"] == [f"wuschel_{installation}_signal"]
    assert "1 Werte" in signal["arguments"]["message"]
    assert "abcd1234_wuschel" in signal["arguments"]["message"]
    assert "private" not in signal["arguments"]["message"]
    acknowledge(state, signal, NOW + timedelta(seconds=30))
    plan_notices(state, conf, NOW + timedelta(seconds=60), installation)
    assert len(jobs(state)) == 1
    configuration = jobs(state)[0]
    assert configuration["target_ids"] == [f"wuschel_{installation}_configuration"]
    acknowledge(state, configuration, NOW + timedelta(seconds=60))
    # The new signal is rate-limited against the confirmed first summary.
    assert len(jobs(state)) == 0
    plan_notices(state, conf, NOW + timedelta(seconds=330), installation)
    second = jobs(state)[0]
    acknowledge(state, second, NOW + timedelta(seconds=330))
    plan_notices(state, conf, NOW + timedelta(days=1), installation)
    assert jobs(state) == []  # Also stays quiet after a user dismisses the HA message.


def test_partial_resolution_clear_delay_and_disable_only_own_notice(config):
    state, conf, installation = State(epoch_id="epoch"), document(config), str(uuid4())
    fields = {"a": problem(), "b": problem()}
    state.tables["contract_state_current"]["fixture.echo"] = {"fields": fields}
    plan_notices(state, conf, NOW, installation)
    plan_notices(state, conf, NOW + timedelta(seconds=30), installation)
    acknowledge(state, jobs(state)[0], NOW + timedelta(seconds=30))
    fields.pop("a")
    plan_notices(state, conf, NOW + timedelta(seconds=40), installation)
    assert jobs(state) == []
    plan_notices(state, conf, NOW + timedelta(seconds=330), installation)
    partial = jobs(state)[0]
    assert partial["operation"] == "problem_notice_put"
    acknowledge(state, partial, NOW + timedelta(seconds=330))
    fields.clear()
    plan_notices(state, conf, NOW + timedelta(seconds=340), installation)
    assert jobs(state) == []
    plan_notices(state, conf, NOW + timedelta(seconds=350), installation)
    assert jobs(state)[0]["operation"] == "problem_notice_remove"
    assert jobs(state)[0]["arguments"] == {}


def test_outage_coalesces_latest_and_keeps_only_one_pending_summary(config):
    state, conf, installation = State(epoch_id="epoch"), document(config), str(uuid4())
    fields = {"a": problem()}
    state.tables["contract_state_current"]["fixture.echo"] = {"fields": fields}
    plan_notices(state, conf, NOW, installation)
    plan_notices(state, conf, NOW + timedelta(seconds=30), installation)
    fields["b"] = problem()
    plan_notices(state, conf, NOW + timedelta(seconds=31), installation)
    plan_notices(state, conf, NOW + timedelta(seconds=61), installation)
    assert len(jobs(state)) == 1
    assert "2 Werte" in jobs(state)[0]["arguments"]["message"]
    fields.clear()
    plan_notices(state, conf, NOW + timedelta(seconds=62), installation)
    assert jobs(state) == []


async def test_initialization_waits_for_confirmed_snapshot_and_timer_commits_outbox(
    runtime, config
):
    runtime.installation_id = str(uuid4())
    await activate(runtime, document(config).model_dump(mode="json"))
    assert not jobs(runtime.state)
    await runtime.submit("snapshot", [])
    assert not jobs(runtime.state)
    runtime.clock.advance(30)
    await runtime.submit("tick")
    assert len(jobs(runtime.state)) == 1
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    job = await runtime.submit("service_claim")
    assert job["status"] == "sent"
    published = runtime.state.publication_seq
    await runtime.submit(
        "service_complete", {"job_id": job["job_id"], "response": {"outcome": "completed"}}
    )
    assert runtime.state.tables["service_state"]["notice:signal"]["acknowledged"]
    assert runtime.state.tables["service_job"][job["job_id"]]["status"] == "finished"
    assert runtime.state.publication_seq == published


async def test_notice_completion_schedules_retry_without_republishing(runtime, config):
    runtime.installation_id = str(uuid4())
    await activate(runtime, document(config).model_dump(mode="json"))
    await runtime.submit("snapshot", [])
    runtime.clock.advance(30)
    await runtime.submit("tick")
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    job = await runtime.submit("service_claim")
    published = runtime.state.publication_seq
    await runtime.submit(
        "service_complete", {"job_id": job["job_id"], "response": {"outcome": "failed"}}
    )
    retry_at = runtime.clock.now_utc() + timedelta(seconds=30)
    assert (
        runtime.state.tables["service_state"]["notice:signal"]["retry_at"] == retry_at.isoformat()
    )
    assert runtime.state.publication_seq == published
    runtime.clock.advance(30)
    await runtime.submit("tick")
    assert jobs(runtime.state)[0]["job_id"] != job["job_id"]


async def test_notice_completion_preserves_deadline_crossed_during_commit(
    runtime, config, monkeypatch
):
    runtime.installation_id = str(uuid4())
    await activate(runtime, document(config).model_dump(mode="json"))
    await runtime.submit("snapshot", [])
    runtime.clock.advance(30)
    await runtime.submit("tick")
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    job = await runtime.submit("service_claim")
    runtime.next_evaluation = runtime.clock.now_utc() + timedelta(seconds=1)
    commit = runtime.store.commit
    evaluate = runtime._evaluate
    evaluations = []

    def record_evaluation(*args, **kwargs):
        evaluations.append(kwargs["origin"])
        return evaluate(*args, **kwargs)

    async def slow_commit(state):
        monkeypatch.setattr(runtime.store, "commit", commit)
        runtime.clock.advance(1)
        await commit(state)

    monkeypatch.setattr(runtime.store, "commit", slow_commit)
    monkeypatch.setattr(runtime, "_evaluate", record_evaluation)
    await runtime.submit(
        "service_complete", {"job_id": job["job_id"], "response": {"outcome": "completed"}}
    )
    await runtime.submit("tick")
    assert runtime.state.tables["service_state"]["notice:signal"]["acknowledged"]
    assert evaluations == ["timer"]


async def test_failed_notice_commit_cannot_be_claimed_and_disable_keeps_channel_for_cleanup(
    runtime, config
):
    runtime.installation_id = str(uuid4())
    await activate(runtime, document(config).model_dump(mode="json"))
    await runtime.submit("snapshot", [])
    runtime.clock.advance(30)
    runtime.store.available = False
    # The Clock alarm enqueues the first tick before our explicit queue barrier.
    await runtime.submit("tick")
    assert not runtime.persistence
    assert not jobs(runtime.state)
    runtime.store.available = True
    await runtime.submit("recover")
    await runtime.submit("snapshot", [])
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    job = await runtime.submit("service_claim")
    assert job
    await runtime.submit(
        "service_complete", {"job_id": job["job_id"], "response": {"outcome": "completed"}}
    )
    disabled = document(config).model_dump(mode="json")
    disabled["notifications"] = None
    disabled["contracts"] = []
    await activate(runtime, disabled)
    assert runtime.requires_ha_snapshot
    runtime.clock.advance(10)
    await runtime.submit("tick")
    cleanup = await runtime.submit("service_claim")
    assert cleanup and cleanup["operation"] == "problem_notice_remove"
    await runtime.submit(
        "service_complete", {"job_id": cleanup["job_id"], "response": {"outcome": "completed"}}
    )
    assert not runtime.requires_ha_snapshot


def test_summary_limit_distinct_installations_and_uncertain_reply(config):
    conf, state, first, second = (
        document(config),
        State(epoch_id="epoch"),
        str(uuid4()),
        str(uuid4()),
    )
    for index in range(12):
        state.tables["contract_state_current"][f"fixture.{index}"] = {
            "fields": {"value": problem()}
        }
    plan_notices(state, conf, NOW, first)
    plan_notices(state, conf, NOW + timedelta(seconds=30), first)
    job = jobs(state)[0]
    assert job["arguments"]["message"].count("- fixture.") == 10
    assert "12 Werte" in job["arguments"]["message"]
    job["status"] = "finished"
    complete_notice(state, job, {"outcome": "indeterminate"}, NOW + timedelta(seconds=30))
    plan_notices(state, conf, NOW + timedelta(seconds=59), first)
    assert jobs(state) == []
    plan_notices(state, conf, NOW + timedelta(seconds=60), first)
    assert len(jobs(state)) == 1
    another = State(epoch_id="epoch")
    another.tables["contract_state_current"] = state.tables["contract_state_current"]
    plan_notices(another, conf, NOW, second)
    plan_notices(another, conf, NOW + timedelta(seconds=30), second)
    assert jobs(another)[0]["target_ids"] != job["target_ids"]
