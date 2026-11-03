"""Real, disposable Mosquitto transport integration on the isolated CI runner."""

import asyncio
import json
import os
import socket
import subprocess
from uuid import uuid4

import aiohttp
import aiomqtt
import pytest

from core_contracts.adapters.mqtt import MQTTAdapter
from core_contracts.clock import SystemClock
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry

from .conftest import activate


@pytest.mark.skipif(
    os.environ.get("TEST_MQTT_DOCKER") != "1", reason="isolated Docker MQTT integration only"
)
@pytest.mark.parametrize("opening", [False, True])
async def test_broker_disconnect_reconnect_and_invalid_message(tmp_path, config, opening):
    name = "cc-mqtt-test-" + uuid4().hex[:12]
    configuration = tmp_path / "mosquitto.conf"
    configuration.write_text("listener 1883\nallow_anonymous true\npersistence false\n")
    configuration.chmod(0o644)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True).strip()

    from core_contracts.contract_types import type_registry as application_types

    runtime = Runtime(
        MemoryStore(),
        SystemClock(),
        application_types(include_test_types=True) if opening else type_registry(),
    )
    await runtime.start()
    task = None

    async def eventually(predicate):
        async with asyncio.timeout(20):
            while not predicate():
                await runtime.clock.sleep(0.05)

    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:1883",
            "--mount",
            f"type=bind,source={configuration},target=/mosquitto/config/mosquitto.conf,readonly",
            "eclipse-mosquitto:2",
        )
        assert int(docker("port", name, "1883").rsplit(":", 1)[1]) == port
        config["dependencies"][0]["kind"] = "mqtt"
        if opening:
            config["sources"][0]["freshness"] = {
                "mode": "report_heartbeat",
                "interval_s": 8400,
                "future_tolerance_s": 0,
            }
            config["contracts"][0].update(
                type_id="opening.single_contact",
                parameters={"grace_s": 600},
                inputs=[{"name": "contact", "ref": "source.a"}],
            )
        config["bindings"][0]["adapter"] = {
            "kind": "mqtt",
            "mqtt_topic": "synthetic/input",
            "mqtt_value_path": "value",
            "mqtt_time_path": "at",
        }
        await activate(runtime, config)
        async with aiohttp.ClientSession() as session:
            adapter = MQTTAdapter(
                runtime,
                session,
                "external",
                {"host": "127.0.0.1", "port": port, "username": "", "password": ""},
                "",
            )
            task = asyncio.create_task(adapter.run())
            await eventually(lambda: runtime.mqtt == "connected")

            async def publish(payload):
                async with aiomqtt.Client("127.0.0.1", port) as publisher:
                    await publisher.publish("synthetic/input", payload, qos=1)

            await publish(json.dumps({"value": True, "at": runtime.clock.now_utc().isoformat()}))
            await eventually(
                lambda: (
                    runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"]
                    == "valid"
                )
            )
            await publish("{invalid-json")
            await eventually(
                lambda: (
                    runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"]
                    == ("held" if opening else "unknown")
                )
            )
            assert runtime.mqtt == "connected" and not await runtime.store.rows("history_gap")
            docker("stop", "--time", "2", name)
            await eventually(lambda: runtime.mqtt == "disconnected")
            await runtime.clock.sleep(3)
            assert len(await runtime.store.rows("history_gap")) == 1
            docker("start", name)
            assert int(docker("port", name, "1883").rsplit(":", 1)[1]) == port
            await eventually(lambda: runtime.mqtt == "connected")
            await publish(json.dumps({"value": False, "at": runtime.clock.now_utc().isoformat()}))
            await eventually(
                lambda: (
                    runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["status"]
                    == "valid"
                )
            )
            assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] == (
                "closed" if opening else False
            )
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.stop()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
