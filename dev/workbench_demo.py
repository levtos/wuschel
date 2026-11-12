"""Local review harness for the Wuschel Workbench (no Home Assistant, no PostgreSQL).

Runs the *real* HTTP/WebSocket API and the real engine on the in-memory store and
serves the built frontend (``frontend/dist``) on the Ingress listener, so the UI can
be exercised against genuine API responses. It is a development aid only: it
activates nothing in a real installation, talks to no external system and keeps
nothing after it stops.

    cd frontend && npm ci && npm run build
    uv run python dev/workbench_demo.py --seed --port 8099
    # open http://127.0.0.1:8099/

``--seed`` activates docs/alpha4/example-registry.json through the public
draft/activate path. ``--feed`` alternately sets the boolean sources ``source.a`` and
``source.b`` (every ``--interval`` seconds) so an activated cascade changes live; it
works on its own, for registries built in the UI. The Workbench end-to-end test
(``npm run test:e2e``) runs against a fresh, empty instance started with ``--feed``.
"""

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import ClientSession, web
from workbench_devices import attach as attach_devices
from workbench_devices import feed as feed_devices

from core_contracts import wiring  # noqa: F401
from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.api import API
from core_contracts.clock import FakeClock, SystemClock
from core_contracts.evidence import Observation
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
from core_contracts.setup import Setup
from core_contracts.testing.authoring import activate_draft
from core_contracts.testing.contract_types import type_registry

ROOT = Path(__file__).resolve().parents[1]


class FinishedSetup(Setup):
    """Pretends the first-run assistant is complete; it exposes no setup mutation."""

    def status(self) -> dict[str, Any]:
        return {
            "phase": "completed",
            "database_configured": True,
            "onboarding_complete": True,
            "authority": "supervisor",
            "installation_id": "demo",
            "error": "",
            "restart_required": False,
            "csrf_token": self.csrf,
            "database": None,
        }

    async def dispatch(self, path: str, method: str, body: Any) -> Any:
        if path == "status" and method == "GET":
            return self.status()
        if path == "bridge" and method == "GET":
            return {"state": "active"}
        raise ValueError("invalid_setup_request")


def observation(runtime: Runtime, name: str, value: bool, seq: int) -> Observation:
    now = runtime.clock.now_utc()
    return Observation(
        source_id=f"source.{name}",
        binding_id=f"binding.{name}",
        adapter="ha_state",
        value_raw=value,
        observation_kind="live_change",
        ha_time_fired=now,
        received_at=now,
        epoch_id=runtime.state.epoch_id,
        ingest_seq=seq,
        registry_revision=runtime.state.active_revision,
        reference_version=2,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--seed", action="store_true", help="activate the Alpha-4 example registry")
    parser.add_argument("--feed", action="store_true", help="feed source.a / source.b periodically")
    parser.add_argument("--alpha8", action="store_true", help="synthetic Alpha 8 review surfaces")
    parser.add_argument("--alpha9", action="store_true", help="synthetic Alpha 9 review surfaces")
    parser.add_argument("--alpha10", action="store_true", help="synthetic Alpha 10 review surfaces")
    parser.add_argument(
        "--post1", action="store_true", help="deterministic synthetic correction screenshots"
    )
    parser.add_argument("--interval", type=float, default=8.0, help="feed period in seconds")
    parser.add_argument("--frontend", type=Path, default=ROOT / "frontend" / "dist")
    args = parser.parse_args()

    clock = (
        FakeClock(datetime(2026, 10, 17, 10, tzinfo=UTC))
        if args.post1 or args.alpha8 or args.alpha9 or args.alpha10
        else SystemClock()
    )
    from core_contracts.openings import register as register_openings

    types = type_registry()
    register_openings(types)
    runtime = Runtime(MemoryStore(), clock, types)
    await runtime.start()
    session = ClientSession()
    # This adapter only normalizes synthetic local records; its network methods are never called.
    adapter = BridgeAdapter(runtime, session, "ws://127.0.0.1:1/unused", "synthetic-only")
    entities = attach_devices(adapter, ROOT)
    data = Path(tempfile.mkdtemp(prefix="terra-demo-"))
    api = API(
        runtime,
        Tokens(data / "secrets"),
        str(uuid4()),
        installation_label="Wuschel Workbench Demo (lokal, nicht produktiv)",
        ingress_address="127.0.0.1",
        frontend=args.frontend,
        setup=FinishedSetup(data, None),
    )
    # The production limiter allows 120 requests/minute for the whole Ingress; scripted end-to-end
    # runs are far faster than a person, so the review harness lifts it (it is not under test here).
    api.limiter.limit = 1_000_000
    runner = web.AppRunner(api.application(ingress=True), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", args.port).start()

    async def scheduler() -> None:
        while True:
            runtime.tick()
            await clock.sleep(0.5)

    tasks = [asyncio.create_task(scheduler())]
    if args.post1:
        from workbench_post1 import seed

        await seed(runtime)
    if args.alpha8:
        from workbench_alpha8 import seed as seed_alpha8

        await seed_alpha8(runtime)
    if args.alpha9:
        from workbench_alpha9 import seed as seed_alpha9

        await seed_alpha9(runtime)
    if args.alpha10:
        from workbench_alpha10 import seed as seed_alpha10

        await seed_alpha10(runtime)
    if args.seed:
        document = json.loads(
            (ROOT / "docs" / "alpha4" / "example-registry.json").read_text(encoding="utf-8")
        )
        draft = await runtime.submit("draft_create", {"config": document})
        await activate_draft(runtime, draft, expected_active_revision=0)
        runtime.first_snapshot = True

    if args.feed:
        runtime.first_snapshot = True
        await runtime.submit("bridge_state", ("connected", "connected"))

        async def feed() -> None:
            seq = 0
            flip = False
            while True:
                seq += 1
                flip = not flip
                await runtime.submit("observation", observation(runtime, "a", flip, seq))
                await runtime.submit("observation", observation(runtime, "b", not flip, seq + 1))
                await feed_devices(adapter, entities)
                await clock.sleep(args.interval)

        tasks.append(asyncio.create_task(feed()))
    print(f"Workbench demo: http://127.0.0.1:{args.port}/  (in-memory, not a real installation)")
    try:
        await asyncio.Event().wait()
    finally:
        for task in tasks:
            task.cancel()
        await runner.cleanup()
        await runtime.stop()
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
