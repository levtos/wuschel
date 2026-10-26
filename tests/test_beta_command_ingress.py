"""Live transport, request identity, installation binding, and durable runtime dedup."""

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.clock import SystemClock

spec = importlib.util.spec_from_file_location(
    "traber_command_ingress",
    Path(__file__).parents[1] / "custom_components/traber/command_ingress.py",
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CommandIngress = module.CommandIngress


async def test_live_commands_deduplicate_and_distinct_presses_survive():
    now = datetime(2032, 1, 1, tzinfo=UTC)
    ingress = CommandIngress("epoch", lambda: now)
    owner, sent = object(), []
    installation = str(uuid4())
    ingress.attach(owner, installation, sent.append)
    data = dict(contract_id="fixture.sleep", command="confirm_sleep", request_id=str(uuid4()))
    first = asyncio.create_task(ingress.call(data))
    second = asyncio.create_task(ingress.call(data))
    await SystemClock().sleep(0)
    assert len(sent) == 1
    assert sent[0]["installation_id"] == installation
    assert datetime.fromisoformat(sent[0]["valid_until"]) - now == timedelta(seconds=30)
    assert not ingress.complete(object(), data["request_id"], {"status": "accepted"})
    assert ingress.complete(owner, data["request_id"], {"status": "accepted", "secret": "ignored"})
    expected = {"request_id": data["request_id"], "status": "accepted"}
    assert await first == await second == expected
    assert await ingress.call(data) == expected
    assert len(sent) == 1
    assert (await ingress.call({**data, "command": "start_pre_sleep"}))[
        "status"
    ] == "request_id_conflict"
    third = asyncio.create_task(ingress.call({**data, "request_id": str(uuid4())}))
    await SystemClock().sleep(0)
    assert len(sent) == 2
    ingress.detach(owner)
    assert (await third)["status"] == "disconnected"


async def test_no_offline_queue_and_reconnect_has_no_replay():
    ingress = CommandIngress("epoch", lambda: datetime(2032, 1, 1, tzinfo=UTC))
    data = dict(contract_id="fixture.sleep", command="confirm_sleep")
    assert (await ingress.call(data))["status"] == "disconnected"
    owner, sent = object(), []
    ingress.attach(owner, str(uuid4()), sent.append)
    assert sent == []
    with pytest.raises(ValueError, match="command_owner_busy"):
        ingress.attach(object(), str(uuid4()), sent.append)
    assert (await ingress.call({**data, "request_id": "invalid"}))["status"] == "invalid_arguments"


async def test_adapter_checks_session_and_returns_durable_receipt(runtime, config):
    from core_contracts.adapters.ha import BridgeAdapter

    from .test_beta_sleep import machine, ready

    await ready(runtime, config)
    # Exercise the real receiver independently of a WebSocket/network session.
    adapter = object.__new__(BridgeAdapter)
    adapter.runtime = runtime
    adapter.contexts = type("Contexts", (), {"epoch": "epoch"})()
    now = runtime.clock.now_utc()
    event = dict(
        kind="command_request",
        request_id=str(uuid4()),
        installation_id=runtime.installation_id,
        ha_epoch_id="epoch",
        issued_at=now.isoformat(),
        valid_until=(now + timedelta(seconds=30)).isoformat(),
        contract_id="fixture.sleep",
        command="confirm_sleep",
        arguments={},
    )
    result = await adapter.incoming_command(event)
    assert result["status"] == "accepted"
    deadline = machine(runtime)["deadline"]
    runtime.clock.advance(1)
    assert await adapter.incoming_command(event) == result
    assert machine(runtime)["deadline"] == deadline
    assert (await adapter.incoming_command({**event, "arguments": {"unexpected": 1}}))[
        "status"
    ] == "command_id_conflict"
    assert (await adapter.incoming_command({**event, "ha_epoch_id": "old"}))["status"] == "rejected"
    assert (await adapter.incoming_command({**event, "installation_id": str(uuid4())}))[
        "status"
    ] == "rejected"
    runtime.clock.advance(30)
    assert (await adapter.incoming_command(event))["status"] == "rejected"
