"""Short, synthetic profiles. Run on the selected revision with --scale 1 or 2.

`uv run python -m dev.truth_lane_profile --scale 1 --samples 60`
Add `--administration` for overlapping catalog migration/staging and `--profile`
for inclusive CPU times. This local probe uses MemoryStore and an in-process
HTTP application. It complements the PostgreSQL/Traber acceptance run.
"""

import argparse
import asyncio
import cProfile
import json
import logging
import math
import pstats
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer

from core_contracts import __version__, wiring  # noqa: F401
from core_contracts.api import API
from core_contracts.clock import SystemClock
from core_contracts.extensions.catalog.managed_catalog import prepare
from core_contracts.persistence import MemoryStore
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange
from core_contracts.runtime import Runtime
from core_contracts.security import Tokens
from core_contracts.testing.contract_types import type_registry
from tests.conftest import activate
from tests.conftest import config as config_fixture
from tests.test_alpha9_calendar import calendar_config, fetch
from tests.test_declarative import observation
from tests.test_managed_catalog import change, configured


def distribution(values):
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "count": len(values),
        "p95_ms": round(ordered[math.ceil(len(values) * 0.95) - 1], 3),
        "max_ms": round(ordered[-1], 3),
    }


class Timings(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.measurements = defaultdict(list)

    def emit(self, record):
        if record.msg == "operation_timing" and record.operation in {
            "observation_block",
            "tick",
        }:
            key = record.operation
            self.measurements[key].append(record.run_ms)
            self.measurements[key + ".database"].append(record.db_ms)
            for phase, milliseconds in getattr(record, "phases_ms", {}).items():
                self.measurements[f"{key}.{phase}"].append(milliseconds)


def registry(scale):
    template = config_fixture.__wrapped__()
    document = configured(template)
    old_catalog = document["catalogs"][0]
    old_catalog.update(catalog_id="media.homepods", unassigned_value="normal")
    old_catalog["classification_schema"]["values"] = ["normal", "boost", "stumm", "leiser"]
    for index in range(1, 90 * scale):
        source = deepcopy(template["sources"][0])
        binding = deepcopy(template["bindings"][0])
        source.update(
            source_id=f"source.synthetic_{index}", physical_source_key=f"physical.synthetic_{index}"
        )
        binding.update(binding_id=f"binding.synthetic_{index}", source_id=source["source_id"])
        binding["adapter"]["ha_entity_id"] = f"sensor.synthetic_{index}"
        document["sources"].append(source)
        document["bindings"].append(binding)
    calendar = calendar_config(deepcopy(template))
    source, binding = calendar["sources"][0], calendar["bindings"][0]
    source.update(
        source_id="source.synthetic_calendar", physical_source_key="physical.synthetic_calendar"
    )
    binding.update(binding_id="binding.synthetic_calendar", source_id=source["source_id"])
    document["sources"][0] = source
    document["bindings"][0] = binding
    document["contracts"] = [
        {
            **template["contracts"][0],
            "contract_id": f"fixture.synthetic_{index}",
            "inputs": [
                {"name": "a", "ref": document["sources"][index % (90 * scale)]["source_id"]}
            ],
        }
        for index in range(100 * scale)
    ]
    return document


async def seed(runtime, scale, *, document=None):
    await activate(runtime, registry(scale) if document is None else document)
    state = runtime.state.catalog_clone()
    now = runtime.clock.now_utc()
    batch = CatalogWrite.model_construct(
        catalog_id="media.homepods",
        definition_version=1,
        expected_active_revision=state.active_revision,
        expected_content_revision=0,
        request_id=str(uuid4()),
        reason="Synthetic benchmark fixture",
        changes=tuple(
            EntryChange.model_validate(
                change(f"Synthetic title {i:05}", classification="boost" if i % 2 else "normal")
            )
            for i in range(3500 * scale)
        ),
    )
    prepare(state, runtime.config, batch, None, now, expanded_limit=100_000)
    await runtime._commit(state)
    events = [
        {
            "summary": f"Synthetic appointment {i:03}",
            "start": now.date().isoformat(),
            "end": (now.date() + timedelta(days=1)).isoformat(),
        }
        for i in range(50 * scale)
    ]
    await fetch(runtime, {"calendar.example": {"events": events}})


async def timed_request(client, headers, timings, name, path, body):
    start = perf_counter()
    async with client.post(f"/api/v1/{path}", headers=headers, json=body) as response:
        result = await response.json()
        if response.status != 200:
            raise RuntimeError((path, response.status, result.get("error")))
    timings.measurements[name].append((perf_counter() - start) * 1000)
    return result


async def administer(client, headers, timings, runtime):
    migration = await timed_request(
        client,
        headers,
        timings,
        "class_migration.preview",
        "catalogs/classes/migration",
        {"expected_active_revision": runtime.state.active_revision},
    )
    draft = await timed_request(
        client,
        headers,
        timings,
        "portable.stage",
        "portable/stage",
        {
            "package": migration["package"],
            "resolutions": migration["resolutions"],
            "plan_digest": migration["preview"]["plan_digest"],
            "request_id": str(uuid4()),
        },
    )
    return draft["draft_id"]


async def exercise(client, headers, timings, runtime, args):
    loop = asyncio.get_running_loop()
    start = loop.time()

    async def observe_loop():
        while True:
            due = loop.time() + 0.02
            await runtime.clock.sleep(0.02)
            timings.measurements["event_loop"].append(max(0, loop.time() - due) * 1000)

    clock_probe = asyncio.create_task(observe_loop())
    management = (
        asyncio.create_task(administer(client, headers, timings, runtime))
        if args.administration
        else None
    )
    observations = []
    try:
        for index in range(args.samples):
            target = start + index / (args.rate * args.scale)
            await runtime.clock.sleep(max(0, target - loop.time()))
            point = 1 + index % (90 * args.scale - 1)
            item = observation(runtime, bool(index % 2), name=f"synthetic_{point}")
            item = item.model_copy(
                update={
                    "ingest_seq": index + 1,
                    "adapter": "ha_state",
                    "registry_revision": runtime.state.active_revision,
                }
            )
            observations.append(asyncio.create_task(runtime.submit("observation", item)))
            if index % args.rate == 0:
                await runtime.submit("tick")
        await asyncio.gather(*observations)
        if management:
            await management
    finally:
        clock_probe.cancel()
        if management and not management.done():
            management.cancel()
        await asyncio.gather(
            clock_probe, *([management] if management else []), return_exceptions=True
        )


async def run(args):
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
    await runtime.start()
    try:
        await seed(runtime, args.scale)
        timings = Timings()
        logger = logging.getLogger("core_contracts")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(timings)
        profile = cProfile.Profile()
        with TemporaryDirectory() as directory:
            tokens = Tokens(Path(directory) / "secrets")
            headers = {
                "Authorization": "Bearer " + tokens.values["admin"],
                "X-Authoring-Protocol": "2",
            }
            async with TestClient(
                TestServer(API(runtime, tokens, "synthetic-profile").application())
            ) as client:
                start, cpu = perf_counter(), process_time()
                if args.profile:
                    profile.enable()
                await exercise(client, headers, timings, runtime, args)
                profile.disable()
                result = {
                    "version": __version__,
                    "storage": "MemoryStore",
                    "scale": args.scale,
                    "rate_per_second": args.rate * args.scale,
                    "administration": args.administration,
                    "contracts": 100 * args.scale,
                    "sources": 90 * args.scale,
                    "catalog_entries": 3500 * args.scale,
                    "main_process_cpu_cores": round(
                        (process_time() - cpu) / (perf_counter() - start), 3
                    ),
                    "profiled": args.profile,
                    "samples": {
                        key: distribution(values)
                        for key, values in sorted(timings.measurements.items())
                    },
                }
        if args.profile:
            stream = StringIO()
            pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(
                22
            )
            result["profile"] = stream.getvalue()
        print(json.dumps(result, indent=2))
        logger.removeHandler(timings)
    finally:
        await runtime.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=int, choices=(1, 2), required=True)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--rate", type=int, default=8)
    parser.add_argument("--administration", action="store_true")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.samples < 1 or args.rate < 1:
        parser.error("--samples and --rate must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
