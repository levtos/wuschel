"""Owned, synthetic in-memory API for the b10 Firefox import regression proof."""

import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from workbench_demo import FinishedSetup

from core_contracts.api import API
from core_contracts.clock import SystemClock
from core_contracts.persistence import MemoryStore
from core_contracts.portable import export
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
from core_contracts.testing.authoring import activate_draft
from core_contracts.testing.contract_types import type_registry


async def main():
    root = Path(__file__).resolve().parents[1]
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
    await runtime.start()
    config = {
        "config_schema_version": 4,
        "fingerprint_version": 4,
        "dependencies": [
            {"dependency_id": "synthetic.mqtt", "kind": "mqtt", "monitor": "internal"}
        ],
        "sources": [],
        "bindings": [],
        "contracts": [],
        "catalogs": [],
        "freshness_templates": [
            {
                "template_id": "freshness.contact",
                "display_name": "Kontakte",
                "sensor_kind": "contact",
                "rule": {"mode": "report_heartbeat", "interval_s": 8400, "future_tolerance_s": 0},
            }
        ],
    }
    for index in range(29):
        if index == 28:
            draft = await runtime.submit("draft_create", {"config": config})
            await activate_draft(runtime, draft, expected_active_revision=0)
        identity = f"synthetic.{index}"
        config["sources"].append(
            {
                "source_id": f"source.{identity}",
                "display_name": f"Synthetische Quelle {index}",
                "source_type": "boolean",
                "source_origin": "local",
                "dependency_id": "synthetic.mqtt",
                "physical_source_key": identity,
                "freshness_selection": {
                    "kind": "own",
                    "rule": {
                        "mode": "report_heartbeat",
                        "interval_s": 8400,
                        "future_tolerance_s": 0,
                    },
                },
            }
        )
        config["bindings"].append(
            {
                "binding_id": f"binding.{identity}",
                "source_id": f"source.{identity}",
                "adapter": {"kind": "mqtt", "mqtt_topic": f"wuschel/synthetic/{index}"},
            }
        )
    imported = await runtime.submit("draft_create", {"config": config})
    package = export(
        runtime,
        runtime.state,
        {
            "expected_active_revision": 1,
            "draft_id": imported["draft_id"],
            "draft_version": imported["draft_version"],
        },
    )
    temporary = Path(tempfile.mkdtemp(prefix="wuschel-b10-review-"))
    api = API(
        runtime,
        Tokens(temporary / "secrets"),
        str(uuid4()),
        ingress_address="127.0.0.1",
        frontend=root / "frontend/dist",
        setup=FinishedSetup(temporary, None),
    )
    api.limiter.limit = 1_000_000
    application = api.application(ingress=True)

    async def synthetic_package(request):
        return web.json_response(package)

    # The static catch-all is already registered; expose the fixture via a second local listener.
    fixture = web.Application()
    fixture.router.add_get("/package", synthetic_package)
    runner, fixture_runner = (
        web.AppRunner(application, access_log=None),
        web.AppRunner(fixture, access_log=None),
    )
    await runner.setup()
    await fixture_runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8139).start()
    await web.TCPSite(fixture_runner, "127.0.0.1", 8140).start()
    print(json.dumps({"ready": True, "synthetic": True}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await fixture_runner.cleanup()
        await runner.cleanup()
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
