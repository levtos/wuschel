"""b26 load hysteresis and diagnostics, independent of published truth."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core_contracts.api import API
from core_contracts.load_guard import LoadGuard
from core_contracts.operations import OperationsSettings
from core_contracts.security import Tokens

from .conftest import activate


def sample(guard, now, **overrides):
    return guard.sample(
        now,
        **{
            "lag_ms": 0,
            "queue_depth": 0,
            "queue_capacity": 256,
            "overflow_total": 0,
            "commit_p95_ms": 0,
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "values",
    [
        {"lag_ms": 500},
        {"queue_depth": 256},
        {"overflow_total": 10},
        {"commit_p95_ms": 500},
    ],
)
def test_each_metric_enters_protection_after_hold_and_recovers(values):
    guard = LoadGuard(OperationsSettings())
    assert not sample(guard, 0, **values)
    assert not sample(guard, 4.9, **values)
    assert sample(guard, 5, **values)
    assert guard.state == "geschützt"
    # A full minute clears the overflow window; recovery also has a hold.
    cleared = {"overflow_total": values.get("overflow_total", 0)}
    assert not sample(guard, 61, **cleared)
    assert not sample(guard, 90.9, **cleared)
    assert sample(guard, 91, **cleared)
    assert guard.state == "normal"


def test_hysteresis_resets_on_spikes_and_uses_strained_state():
    guard = LoadGuard(OperationsSettings())
    sample(guard, 0, lag_ms=100)
    sample(guard, 4, lag_ms=0)
    sample(guard, 5, lag_ms=100)
    assert not sample(guard, 9.9, lag_ms=100)
    assert sample(guard, 10, lag_ms=100)
    assert guard.state == "angespannt"
    sample(guard, 11, lag_ms=500)
    sample(guard, 16, lag_ms=500)
    assert guard.state == "geschützt"
    sample(guard, 17, lag_ms=100)
    sample(guard, 46, lag_ms=100)
    assert guard.state == "geschützt"
    sample(guard, 47, lag_ms=100)
    assert guard.state == "angespannt"


def test_overflow_window_is_bounded_and_missing_measurements_are_explicit():
    guard = LoadGuard(OperationsSettings())
    for index in range(1000):
        sample(guard, index / 1000, overflow_total=index + 1, lag_ms=None, commit_p95_ms=None)
    assert len(guard._overflows) == 1
    assert guard.metrics["overflows_per_minute"] == 1000
    sample(guard, 61, overflow_total=1000, lag_ms=None, commit_p95_ms=None)
    assert not guard._overflows
    assert guard.metrics["lag_ms"] is None
    assert guard.metrics["commit_p95_ms"] is None


@pytest.mark.parametrize(
    "metric", ["lag_ms", "queue_percent", "overflows_per_minute", "commit_p95_ms"]
)
def test_threshold_order_is_validated(metric):
    with pytest.raises(ValidationError, match="load_threshold_order"):
        OperationsSettings.model_validate(
            {f"load_{metric}_strained": 100, f"load_{metric}_protected": 1}
        )


@pytest.mark.parametrize("value", [9, 601, 10.5, True])
def test_commit_window_setting_is_bounded(value):
    with pytest.raises(ValidationError):
        OperationsSettings(load_commit_window_s=value)


def slow_commits(runtime, monkeypatch, durations):
    """Measure real runtime commits with deterministic storage wall times."""
    from core_contracts import runtime as module

    elapsed = [0.0]
    original = runtime.store.commit
    durations = iter(durations)

    async def commit(state):
        await original(state)
        duration = next(durations)
        elapsed[0] += duration
        runtime.clock.advance(duration)

    monkeypatch.setattr(module, "perf_counter", lambda: elapsed[0])
    monkeypatch.setattr(runtime.store, "commit", commit)


async def test_single_slow_commit_never_enters_protection(runtime, monkeypatch):
    with monkeypatch.context() as patch:
        slow_commits(runtime, patch, [0.6])
        await runtime._commit(runtime.state.clone(), measure_load=True)
    for delay in (0, 6, 600):
        runtime.clock.advance(delay)
        runtime.sample_load()
        assert runtime.load_guard.state == "normal"
        assert runtime.load_guard.metrics["commit_p95_ms"] is None
    assert runtime.system_state()["commits"]["count"] == 0


@pytest.mark.parametrize("operation", ["audit", "continuation_probe"])
async def test_management_commit_does_not_count(runtime, monkeypatch, operation):
    from core_contracts import slots

    async def handler(rt, state, *args):
        await rt._commit(state)

    with monkeypatch.context() as patch:
        if operation.startswith("continuation_"):
            patch.setitem(slots.OPERATION_PREFIXES, "continuation_", slots.Operation(handler))
        else:
            patch.setitem(slots.OPERATIONS, operation, slots.Operation(handler))
        slow_commits(runtime, patch, [2] * 5)
        for _ in range(5):
            await runtime.process(operation, None)
            runtime.sample_load()
        runtime.clock.advance(runtime.operations.load_enter_hold_s)
        runtime.sample_load()
        assert runtime.commit_duration == 2
        assert runtime.load_guard.state == "normal"
        assert not runtime.commit_samples


async def test_storage_remains_diagnostic_without_late_publication(runtime, config, monkeypatch):
    from .test_runtime import obs

    await activate(runtime, config)
    with monkeypatch.context() as patch:
        slow_commits(runtime, patch, [0.6] * 5)
        for _ in range(5):
            await runtime.process("observation", obs(runtime, True))
    runtime.sample_load()
    assert runtime.system_state()["commits"]["p95_ms"] == pytest.approx(600)
    assert runtime.load_guard.metrics["commit_p95_ms"] is None
    runtime.clock.advance(runtime.operations.load_enter_hold_s)
    runtime.sample_load()
    assert runtime.load_guard.state == "normal"
    runtime.clock.advance(runtime.operations.load_commit_window_s)
    runtime.sample_load()
    assert runtime.system_state()["commits"]["count"] == 0


async def test_end_to_end_publication_samples_recover_after_quiet(runtime):
    now = runtime.clock.monotonic()
    for sequence in range(1, 21):
        runtime.truth_latency.publish(sequence, now - 0.6)
        runtime.truth_latency.acknowledge(sequence, now)
    runtime.sample_load()
    assert runtime.load_guard.metrics["commit_p95_ms"] == pytest.approx(600)
    runtime.clock.advance(runtime.operations.load_enter_hold_s)
    runtime.sample_load()
    assert runtime.load_guard.state == "geschützt"
    assert not runtime.system_state()["truth_delayed"]
    runtime.clock.advance(61)
    runtime.sample_load()
    assert runtime.load_guard.view(runtime.clock.monotonic())["remaining_hold_ms"] == 30_000
    runtime.clock.advance(runtime.operations.load_recover_hold_s)
    runtime.sample_load()
    assert runtime.load_guard.state == "normal"


async def test_truth_warning_requires_an_unacknowledged_publication(runtime):
    now = runtime.clock.monotonic()
    runtime.sample_load()
    assert not runtime.system_state()["truth_delayed"]
    runtime.truth_latency.publish(1, now)
    runtime.clock.advance(0.999)
    runtime.sample_load()
    assert not runtime.system_state()["truth_delayed"]
    runtime.clock.advance(0.002)
    runtime.sample_load()
    assert runtime.system_state()["truth_delayed"]
    runtime.truth_latency.acknowledge(1, runtime.clock.monotonic())
    runtime.sample_load()
    assert not runtime.system_state()["truth_delayed"]


async def test_runtime_state_retry_warning_and_pause_are_process_local(runtime):
    runtime.event_loop_lag_ms = 600
    runtime.sample_load()
    runtime.clock.advance(runtime.operations.load_enter_hold_s)
    runtime.sample_load()
    assert runtime.service_state()["load"]["state"] == "geschützt"
    assert runtime.system_state()["load"]["metrics"]["lag_ms"] == 600
    for _ in range(runtime.operations.retry_warning_after):
        runtime.retry_failed("management", ConnectionError("private detail"))
    state = runtime.system_state()
    assert state["retry_notices"] == ["management"]
    assert state["retries"]["management"]["started_at"]
    assert "private detail" not in json.dumps(state)
    runtime.retry_succeeded("management")
    assert not runtime.service_state()["retry_notices"]
    assert runtime.retries["management"]["started_at"] is None
    runtime.set_projection_paused(True)
    assert runtime.service_state()["projection_paused"] is True
    assert "projection_paused" not in runtime.state.tables["service_state"]


@pytest.mark.parametrize("state", ["normal", "angespannt", "geschützt"])
async def test_light_operations_are_always_allowed(runtime, state):
    runtime.load_guard.state = state
    assert await runtime.submit("audit", {"operation": "synthetic"}) is None


async def test_valid_work_is_not_rejected_and_legacy_wait_returns_immediately(
    runtime, config, tmp_path, aiohttp_client
):
    tokens = Tokens(tmp_path / "secrets")
    api = API(runtime, tokens, "synthetic")
    client = await aiohttp_client(api.application())
    headers = {
        "Authorization": "Bearer " + tokens.values["admin"],
        "X-Wuschel-Interactive": "1",
    }
    runtime.load_guard.state = "geschützt"
    before = runtime.state.publication_seq
    response = await client.post("/api/v1/registry/validate", json=config, headers=headers)
    assert response.status == 200
    assert (await response.json())["valid"]
    response = await client.get("/api/v1/operations/wait", headers=headers)
    assert response.status == 204
    assert runtime.state.publication_seq == before


async def test_pause_api_roles_validation_and_reset(runtime, aiohttp_client, tmp_path):
    api = API(runtime, Tokens(tmp_path / "secrets"), "synthetic")
    client = await aiohttp_client(api.application())
    admin = {"Authorization": "Bearer " + api.tokens.values["admin"]}
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    response = await client.put(
        "/api/v1/operations/projection", headers=consumer, json={"paused": True}
    )
    assert response.status == 403 and runtime.projection_paused is False
    for body in ({"paused": "true"}, {"paused": 1}, {"paused": True, "password": "private"}, {}):
        response = await client.put("/api/v1/operations/projection", headers=admin, json=body)
        assert response.status == 400 and "private" not in await response.text()
        assert runtime.projection_paused is False
    for paused in (True, False):
        response = await client.put(
            "/api/v1/operations/projection", headers=admin, json={"paused": paused}
        )
        assert response.status == 200 and (await response.json())["projection_paused"] is paused
        assert runtime.projection_paused is paused
    response = await client.put(
        "/api/v1/operations/settings",
        headers=admin,
        json={"load_lag_ms_strained": 501, "load_lag_ms_protected": 500},
    )
    assert response.status == 400
    assert "Schutzschwelle" in await response.text()


async def test_management_conflict_retries_are_finite_and_visible(runtime, config, monkeypatch):
    from core_contracts import management

    await activate(runtime, config)
    original = management.commit
    calls = []

    async def conflict(rt, plan):
        calls.append(plan)
        plan.live_conflict = True
        raise ValueError("management_live_conflict")

    monkeypatch.setattr(management, "commit", conflict)
    with pytest.raises(ValueError, match="management_live_conflict"):
        await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 1})
    assert len(calls) == 3
    assert runtime.retries["management"]["consecutive"] == 3
    assert "management" in runtime.service_state()["retry_notices"]
    monkeypatch.setattr(management, "commit", original)
    await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 1})
    assert runtime.retries["management"]["consecutive"] == 0


async def test_mqtt_overflow_never_requests_a_reconnect(runtime, config):
    from core_contracts.adapters.mqtt import MQTTAdapter

    config["bindings"][0]["adapter"] = {"kind": "mqtt", "mqtt_topic": "synthetic/input"}
    await activate(runtime, config)
    runtime.mqtt_revision_changed.clear()
    adapter = MQTTAdapter(runtime, None, "disabled", {}, "")
    queue = adapter.queue_type(1)
    queue.put_nowait(SimpleNamespace(topic="synthetic/input"))
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait(SimpleNamespace(topic="synthetic/input"))
    assert runtime.mqtt_overflow == 1 and runtime.overflow
    assert not runtime.mqtt_revision_changed.is_set()
    assert not runtime.resubscribe.is_set()


async def test_short_mqtt_connections_do_not_reset_failure_series_or_backoff(
    runtime, config, monkeypatch
):
    from core_contracts.adapters import mqtt

    config["bindings"][0]["adapter"] = {"kind": "mqtt", "mqtt_topic": "synthetic/input"}
    await activate(runtime, config)
    runtime.operations = runtime.operations.model_copy(update={"mqtt_retry_max_s": 2})
    delays = []
    runner = asyncio.current_task()

    async def sleep(delay):
        if asyncio.current_task() is not runner:
            await asyncio.Future()
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    class Client:
        def __init__(self, **kwargs):
            self.messages = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def subscribe(self, *args, **kwargs):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionError("private broker detail")

    monkeypatch.setattr(mqtt.aiomqtt, "Client", Client)
    monkeypatch.setattr(runtime.clock, "sleep", sleep)
    adapter = mqtt.MQTTAdapter(runtime, None, "external", {"host": "synthetic", "port": 1883}, "")
    with pytest.raises(asyncio.CancelledError):
        await adapter.run()
    assert delays == [1, 2, 2]
    assert runtime.retries["mqtt"]["consecutive"] == 3
    assert "mqtt" in runtime.service_state()["retry_notices"]
    assert "private broker detail" not in json.dumps(runtime.system_state())


async def test_protected_storm_keeps_every_event_and_ordered_durable_publications(
    runtime, config, monkeypatch
):
    from core_contracts.runtime import Runtime

    from .test_runtime import obs

    config["sources"][0]["source_type"] = "event"
    await activate(runtime, config)
    runtime.mqtt_revision_changed.clear()
    evaluated, persisted = [], []
    evaluate, commit = Runtime._evaluate, runtime.store.commit

    def record(self, state, configuration, **kwargs):
        if kwargs.get("origin") == "input":
            evaluated.append(state.tables["source_observation_current"]["binding.a"]["ingest_seq"])
        return evaluate(self, state, configuration, **kwargs)

    async def write(state):
        persisted.extend(
            row["ingest_seq"] for row in state.tables["source_observation_history"].values()
        )
        await commit(state)

    monkeypatch.setattr(Runtime, "_evaluate", record)
    monkeypatch.setattr(runtime.store, "commit", write)
    publications = runtime.subscribe(max_queue=1000)
    for index in range(500):
        runtime.ingest(obs(runtime, bool(index % 2)).model_copy(update={"ingest_seq": index + 1}))
    runtime.sample_load()
    runtime.clock.advance(runtime.operations.load_enter_hold_s)
    runtime.sample_load()
    assert runtime.load_guard.state == "geschützt"
    assert await runtime.submit("audit", {"operation": "synthetic"}) is None
    await runtime.queue.join()
    assert evaluated == persisted == list(range(1, 501))
    deltas = []
    while not publications.empty():
        message = publications.get_nowait()
        if message["type"] == "delta":
            deltas.append(message)
    assert deltas and all(
        next["prev_seq"] == previous["publication_seq"]
        for previous, next in zip(deltas, deltas[1:])
    )
    assert deltas[-1]["publication_seq"] == runtime.state.publication_seq
    assert not runtime.latest and not runtime.resubscribe.is_set()
    assert not runtime.mqtt_revision_changed.is_set()
    runtime.clock.advance(runtime.operations.load_commit_window_s)
    runtime.sample_load()
    runtime.clock.advance(runtime.operations.load_recover_hold_s)
    runtime.sample_load()
    assert runtime.load_guard.state == "normal"
    runtime.subscribers.discard(publications)
