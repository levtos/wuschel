"""Grouped writes retain evaluation order and the existing transaction boundary."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.registry import storage_document, validate
from core_contracts.runtime import Runtime

from .conftest import activate
from .test_runtime import obs


@pytest.mark.parametrize("kind", ["event", "contact"])
async def test_protected_burst_evaluates_every_event_in_order_and_groups_commits(
    runtime, config, monkeypatch, kind
):
    if kind == "event":
        config["sources"][0]["source_type"] = "event"
    else:
        config = storage_document(validate(config, runtime.types))
        config.update(config_schema_version=4, fingerprint_version=4)
        for source in config["sources"]:
            source["freshness_selection"] = {"kind": "own", "rule": source.pop("freshness")}
        config["sources"][0].update(sensor_kind="contact", sensor_kind_origin="declared")
    await activate(runtime, config)
    evaluated, commits = [], []
    evaluate = Runtime._evaluate
    commit = runtime.store.commit

    def record(self, state, configuration, **kwargs):
        if kwargs.get("origin") == "input":
            evaluated.append(state.tables["source_observation_current"]["binding.a"]["ingest_seq"])
        return evaluate(self, state, configuration, **kwargs)

    async def write(state):
        commits.append(len(state.tables["source_observation_history"]))
        assert runtime.state is not state
        await commit(state)

    monkeypatch.setattr(Runtime, "_evaluate", record)
    monkeypatch.setattr(runtime.store, "commit", write)
    publications = runtime.subscribe(max_queue=1000)
    for index in range(500):
        runtime.clock.advance(0.001)
        runtime.ingest(obs(runtime, bool(index % 2)).model_copy(update={"ingest_seq": index + 1}))
    assert not runtime.latest and not runtime.resubscribe.is_set()
    await runtime.queue.join()
    assert evaluated == list(range(1, 501))
    assert sum(commits) == 500 and max(commits) <= 100 and len(commits) < 250
    assert publications.qsize() == len(commits)
    assert not runtime.overflow
    assert runtime.state.tables["source_observation_current"]["binding.a"]["ingest_seq"] == 500
    runtime.subscribers.discard(publications)


async def test_observation_block_cannot_publish_before_commit_and_control_stays_separate(
    runtime, config, monkeypatch
):
    await activate(runtime, config)
    entered, release = asyncio.Event(), asyncio.Event()
    commit = runtime.store.commit
    before = copy.deepcopy(runtime.snapshot())

    async def delayed(state):
        if state.tables["source_observation_history"]:
            entered.set()
            await release.wait()
        await commit(state)

    monkeypatch.setattr(runtime.store, "commit", delayed)
    runtime.ingest(obs(runtime))
    control = asyncio.create_task(runtime.submit("audit", {"operation": "synthetic_control"}))
    await entered.wait()
    assert runtime.snapshot() == before and not control.done()
    release.set()
    await control
    await runtime.queue.join()
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] is True


async def test_configuration_indexes_are_reused_and_invalidated(runtime, config, monkeypatch):
    import core_contracts.revision_plan as revision_plan

    calls = []
    build = revision_plan.build

    def counted(configuration):
        calls.append(configuration)
        return build(configuration)

    monkeypatch.setattr(revision_plan, "build", counted)
    await activate(runtime, config)
    calls.clear()
    runtime._structures.clear()
    for _ in range(100):
        assert runtime.requires_ha_snapshot
        assert runtime._resolve("source.a", runtime.config) == ("source.a", None)
    assert len(calls) == 1
    await activate(runtime, {**config, "contracts": [], "sources": [], "bindings": []})
    assert not runtime.requires_ha_snapshot
    assert len(calls) >= 2


async def test_mqtt_event_queue_never_drops_protected_messages(runtime, config):
    config["sources"][0]["source_type"] = "event"
    config["bindings"][0]["adapter"] = {"kind": "mqtt", "mqtt_topic": "synthetic/input"}
    await activate(runtime, config)
    adapter = MQTTAdapter(runtime, None, "disabled", {}, "")
    queue = adapter.queue_type(1)
    messages = [SimpleNamespace(topic="synthetic/input", index=index) for index in range(500)]
    for message in messages:
        queue.put_nowait(message)
    assert [queue.get_nowait() for _ in messages] == messages
    assert not adapter.queue_overflow and not runtime.overflow
