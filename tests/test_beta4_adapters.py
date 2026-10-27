"""Rule-only activation must not generate HA snapshots or MQTT replay input."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.freshness_templates import upgrade_document
from core_contracts.registry import storage_document
from dev.fake_ha import FakeHA
from tests.conftest import activate
from tests.test_beta4_freshness import legacy, running, shared
from tests.test_beta_openings import with_connectivity
from tests.test_ha_request_ids import until


async def test_rule_only_ha_activation_keeps_socket_snapshot_sequence_and_accepts_current_revision(
    runtime, config, aiohttp_server
):
    config["fingerprint_version"] = 2
    config["sources"][0]["freshness"] = dict(
        mode="report_heartbeat", interval_s=8400, future_tolerance_s=0
    )
    await activate(runtime, with_connectivity(config))
    stamp = runtime.clock.now_utc().isoformat()
    state = dict(state=True, attributes={}, last_reported=stamp, last_updated=stamp)
    fake = FakeHA({"sensor.example_1": state}, v2=True)
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, str(server.make_url("/websocket")), "synthetic")
        task = asyncio.create_task(adapter.run())
        try:
            await until(lambda: runtime.first_snapshot and adapter.socket is not None)
            before = deepcopy(runtime.state.tables["source_observation_current"])
            socket = adapter.socket
            document = upgrade_document(storage_document(runtime.config))
            document["sources"][0]["freshness_selection"]["rule"]["interval_s"] = 10800
            await activate(runtime, document)
            await until(lambda: not runtime.revision_changed.is_set())
            assert adapter.socket is socket and len(fake.connections) == 1
            assert runtime.state.tables["source_observation_current"] == before
            runtime.clock.advance(1)
            current = runtime.clock.now_utc().isoformat()
            await fake.emit(
                {
                    "kind": "changed",
                    "entity_id": "sensor.example_1",
                    "new_state": {
                        **state,
                        "state": False,
                        "last_reported": current,
                        "last_updated": current,
                    },
                    "time_fired": current,
                    "data": {
                        "entity_id": "sensor.example_1",
                        "old_state": state,
                        "new_state": {
                            **state,
                            "state": False,
                            "last_reported": current,
                            "last_updated": current,
                        },
                    },
                }
            )
            await until(
                lambda: (
                    runtime.state.tables["source_observation_current"]["binding.a"]["value_raw"]
                    is False
                )
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("change", ["freshness_rule", "unrelated_contract"])
async def test_unchanged_mqtt_activation_keeps_subscription_duplicate_memory_and_uses_new_revision(
    monkeypatch,
    change,
):
    from core_contracts.adapters import mqtt

    runtime = await running(legacy())
    clients = []
    messages = asyncio.Queue()

    class Messages:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return await messages.get()

    class Client:
        def __init__(self, **kwargs):
            self.messages = Messages()
            self.topics = []

        async def __aenter__(self):
            clients.append(self)
            return self

        async def __aexit__(self, *args):
            return False

        async def subscribe(self, topic, **kwargs):
            self.topics.append(topic)

    monkeypatch.setattr(mqtt.aiomqtt, "Client", Client)
    adapter = MQTTAdapter(
        runtime, None, "external", {"host": "synthetic.invalid", "port": 1883}, ""
    )
    ready = asyncio.Event()

    class Seen(dict):
        def clear(self):
            super().clear()
            ready.set()

    adapter.seen = Seen()
    task = asyncio.create_task(adapter.run())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        adapter.seen["synthetic"] = "retained duplicate sentinel"
        before = deepcopy(runtime.state.tables["source_observation_current"])
        document = shared(legacy()) if change == "freshness_rule" else legacy()
        if change == "unrelated_contract":
            document["contracts"].append(
                {**deepcopy(document["contracts"][0]), "contract_id": "fixture.independent"}
            )
        await activate(runtime, document)
        runtime.clock.advance(1)
        await until(lambda: not runtime.mqtt_revision_changed.is_set())
        assert len(clients) == 1 and len(clients[0].topics) == 7
        assert adapter.seen["synthetic"] == "retained duplicate sentinel"
        assert {
            key: row["ingest_seq"]
            for key, row in runtime.state.tables["source_observation_current"].items()
        } == {key: row["ingest_seq"] for key, row in before.items()}
        import json

        messages.put_nowait(
            SimpleNamespace(
                topic="test/beta/0/open",
                payload=json.dumps(
                    {"contact": False, "last_seen": runtime.clock.now_utc().isoformat()}
                ).encode(),
                retain=False,
                qos=0,
            )
        )
        await until(
            lambda: (
                runtime.state.tables["source_observation_current"]["source.test.0.open"][
                    "value_raw"
                ]
                is False
            )
        )
        assert (
            runtime.state.tables["source_observation_current"]["source.test.0.open"][
                "registry_revision"
            ]
            == runtime.state.active_revision
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.stop()
