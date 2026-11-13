import asyncio
import copy
from datetime import timedelta
from uuid import uuid4

import aiohttp
import pytest
from core_contracts_client import CoreContractsClient, InstallationMismatch

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.api import API
from core_contracts.security import Tokens
from core_contracts.validation import validate_document as validate
from dev.fake_ha import FakeHA

from .conftest import activate, activation_body
from .test_runtime import obs


async def test_api_auth_and_registry(runtime, config, aiohttp_client, tmp_path):
    tokens = Tokens(tmp_path)
    api = API(runtime, tokens, str(uuid4()))
    client = await aiohttp_client(api.application())
    assert (await client.get("/api/v1/info")).status == 401
    assert (await client.get("/health/live")).status == 200
    assert (await client.get("/health/ready")).status == 503
    consumer = {"Authorization": "Bearer " + tokens.values["consumer"]}
    admin = {"X-Authoring-Protocol": "2", "Authorization": "Bearer " + tokens.values["admin"]}
    assert (await client.get("/api/v1/sources", headers=consumer)).status == 403
    draft = await (
        await client.post(
            "/api/v1/registry/drafts",
            headers=admin,
            json={"config": config, "expected_active_revision": runtime.state.active_revision},
        )
    ).json()
    path = "/api/v1/registry/drafts/" + draft["draft_id"]
    assert (await client.post(path + "/validate", headers=admin, json={})).status == 200
    assert (
        await client.post(
            path + "/activate",
            headers=admin,
            json=await activation_body(client, path, admin, 1),
        )
    ).status == 200
    assert (
        await client.post(
            path + "/activate",
            headers=admin,
            json={"draft_version": 1, "expected_active_revision": 0},
        )
    ).status == 409
    assert (await client.get("/api/v1/registry/active/export", headers=admin)).status == 200
    assert (await client.get("/api/v1/types", headers=consumer)).status == 200
    token = tokens.values["consumer"]
    tokens.rotate("consumer")
    assert tokens.role("Bearer " + token) is None


async def test_ingress_does_not_trust_forwarding_headers(runtime, aiohttp_client, tmp_path):
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    client = await aiohttp_client(api.application(ingress=True))
    assert (
        await client.get("/api/v1/info", headers={"X-Forwarded-For": "172.30.32.2"})
    ).status == 403


async def test_real_client_snapshot_subscription_command(runtime, config, aiohttp_server, tmp_path):
    await activate(runtime, config)
    tokens = Tokens(tmp_path)
    identity = str(uuid4())
    server = await aiohttp_server(API(runtime, tokens, identity).application())
    async with CoreContractsClient(
        str(server.make_url("")), tokens.values["consumer"], identity
    ) as client:
        snapshot = await client.snapshot()
        assert snapshot.publication_seq == runtime.state.publication_seq
        stream = client.subscribe()
        assert (await anext(stream))["type"] == "service_state"
        assert (await anext(stream))["type"] == "snapshot"
        await runtime.submit("observation", obs(runtime))
        delta = await anext(stream)
        assert delta["type"] == "delta" and delta["prev_seq"] == snapshot.publication_seq
        await runtime.submit("recover")
        recovered = await anext(stream)
        assert recovered["type"] == "service_state"
        assert recovered["reasons"] == runtime.readiness_reasons
        assert recovered["publication_confirmed"] is True
        assert (await anext(stream))["type"] == "snapshot"
        await stream.aclose()
    with pytest.raises(InstallationMismatch):
        async with CoreContractsClient(
            str(server.make_url("")), tokens.values["consumer"], str(uuid4())
        ):
            pytest.fail("wrong identity accepted")


async def test_client_resync_sequence_gap(runtime, config, aiohttp_server, tmp_path):
    await activate(runtime, config)
    tokens = Tokens(tmp_path)
    identity = str(uuid4())
    server = await aiohttp_server(API(runtime, tokens, identity).application())
    async with CoreContractsClient(
        str(server.make_url("")), tokens.values["consumer"], identity
    ) as client:
        stream = client.subscribe()
        await anext(stream)
        await anext(stream)
        runtime._broadcast(
            {
                "type": "delta",
                "prev_seq": 999,
                "publication_seq": 1000,
                "epoch_id": runtime.state.epoch_id,
                "contracts": {},
            }
        )
        assert (await anext(stream))["type"] == "snapshot"
        await stream.aclose()


async def test_bridge_snapshot_absent_changed_report_order(runtime, config, aiohttp_server):
    await activate(runtime, config)
    fake = FakeHA()
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(
            runtime,
            session,
            str(server.make_url("/websocket")).replace("http:", "ws:"),
            "test-token",
        )
        task = asyncio.create_task(adapter.connect(runtime.config))
        async with asyncio.timeout(3):
            while not runtime.first_snapshot:
                await asyncio.sleep(0)  # noqa: TID251
        assert (
            runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["reasons"][0]["code"]
            == "input_absent"
        )
        now = runtime.clock.now_utc().isoformat()
        state = {
            "state": True,
            "attributes": {},
            "last_changed": now,
            "last_updated": now,
            "last_reported": now,
        }
        await fake.emit(
            {
                "kind": "changed",
                "entity_id": "sensor.example_1",
                "old_state": None,
                "new_state": state,
                "time_fired": now,
                "context": {"id": "context"},
            }
        )
        async with asyncio.timeout(3):
            while not runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"]:
                await asyncio.sleep(0)  # noqa: TID251
        later = (runtime.clock.now_utc() + timedelta(seconds=1)).isoformat()
        await adapter.event(
            {
                "kind": "reported",
                "entity_id": "sensor.example_1",
                "last_reported": later,
                "old_last_reported": now,
                "time_fired": later,
                "context_id": "report",
            },
            runtime.config,
        )
        await runtime.queue.join()
        evidence = runtime.state.tables["source_observation_current"]["binding.a"]
        assert (
            evidence["ha_last_reported"] == later.replace("+00:00", "Z")
            and evidence["observation_kind"] == "report"
        )
        assert [r["type"] for r in fake.requests] == [
            "traber/info",
            "traber/negotiate",
            "get_config",
            "subscribe_events",
            "subscribe_events",
            "traber/subscribe",
        ]
        runtime.resubscribe.set()
        await task


@pytest.mark.parametrize("missing,protocol", [(True, 1), (False, 2)])
async def test_bridge_missing_or_incompatible_no_fallback(
    runtime, config, aiohttp_server, missing, protocol
):
    await activate(runtime, config)
    fake = FakeHA(missing=missing, protocol=protocol)
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(
            runtime,
            session,
            str(server.make_url("/websocket")).replace("http:", "ws:"),
            "test-token",
        )
        with pytest.raises(ConnectionError):
            await adapter.connect(runtime.config)
    assert len(fake.requests) == 1


async def test_v2_client_multiplexes_service_reply_and_legacy_client_stays_readonly(
    runtime, config, aiohttp_server
):
    await activate(runtime, config)
    fake = FakeHA(v2=True)
    server = await aiohttp_server(fake.application())
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(
            runtime,
            session,
            str(server.make_url("/websocket")).replace("http:", "ws:"),
            "test-token",
        )
        task = asyncio.create_task(adapter.connect(runtime.config))
        async with asyncio.timeout(3):
            while adapter.socket is None:
                await asyncio.sleep(0)  # noqa: TID251
        result = await adapter.service_call(
            "calendar_events",
            ["calendar.example"],
            {"start": "2026-10-17T00:00:00Z", "end": "2026-10-18T00:00:00Z"},
        )
        assert result["outcome"] == "completed"
        assert result["data"] == {"calendar.example": {"events": []}}
        assert runtime.bridge_protocol_selected == 2
        runtime.resubscribe.set()
        await task
        assert adapter.socket is None
        assert (await adapter.service_call("calendar_events", ["calendar.example"], {}))[
            "outcome"
        ] == "rejected"


async def test_mqtt_retain_dedup_old_source_time(runtime, config):
    config["bindings"][0]["adapter"] = {
        "kind": "mqtt",
        "mqtt_topic": "test/input",
        "mqtt_value_path": "value",
        "mqtt_time_path": "time",
    }
    configuration = validate(config, runtime.types)
    await activate(runtime, config)
    async with aiohttp.ClientSession() as session:
        adapter = MQTTAdapter(runtime, session, "disabled", {}, "")
        payload = b'{"value":true,"time":"2026-01-01T12:00:00Z"}'
        retained = adapter.observation(configuration.bindings[0], configuration, payload, True, 1)
        await runtime.submit("observation", retained)
        assert (
            runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"]
            == "unknown"
        )
        assert (
            adapter.observation(configuration.bindings[0], configuration, payload, True, 1) is None
        )
        live = adapter.observation(configuration.bindings[0], configuration, payload, False, 1)
        await runtime.submit("observation", live)
        assert (
            runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"]
            == "unknown"
        )
        runtime.clock.advance(1)
        fresh = adapter.observation(
            configuration.bindings[0],
            configuration,
            payload.replace(b"12:00:00", b"12:00:01"),
            False,
            1,
        )
        await runtime.submit("observation", fresh)
        assert (
            runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"] == "valid"
        )


def test_duplicate_physical_path_rejected(config):
    second = copy.deepcopy(config["sources"][0])
    second["source_id"] = "source.b"
    config["sources"].append(second)
    config["bindings"].append(
        {
            "binding_id": "binding.b",
            "source_id": "source.b",
            "adapter": {"kind": "mqtt", "mqtt_topic": "test/b"},
        }
    )
    from core_contracts.testing.contract_types import type_registry

    with pytest.raises(ValueError, match="physical"):
        validate(config, type_registry())
