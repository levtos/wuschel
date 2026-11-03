"""Manual synthetic MemoryStore benchmark: uv run python -m tests.perf.benchmark.

Never connects to a running Wuschel, HA, MQTT or an existing database.
"""

import asyncio
import copy
import json
import logging
import sys
import tempfile
import threading
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))

from aiohttp.test_utils import TestClient, TestServer

from core_contracts.api import API
from core_contracts.clock import SystemClock
from core_contracts.evidence import Observation
from core_contracts.freshness_templates import upgrade_document
from core_contracts.managed_catalog import prepare
from core_contracts.persistence import MemoryStore
from core_contracts.registry import RegistryConfig
from core_contracts.runtime import Runtime, Work
from core_contracts.security import Tokens
from core_contracts.testing.contract_types import type_registry
from tests.conftest import activate, config
from tests.test_b22_p2 import SeedWrite
from tests.test_managed_catalog import catalog
from tests.test_portable import export


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        if record.msg == "operation_timing":
            self.records.append(
                {
                    "operation": record.operation,
                    "run_ms": record.run_ms,
                    "db_ms": record.db_ms,
                    "db_calls": record.db_calls,
                    "phases_ms": record.phases_ms,
                }
            )


def synthetic_config():
    doc = config.__wrapped__()
    doc.update(
        sources=[], bindings=[], contracts=[], config_schema_version=3, fingerprint_version=3
    )
    base = config.__wrapped__()
    for i in range(90):
        source = copy.deepcopy(base["sources"][0])
        source.update(source_id=f"source.synthetic{i}", physical_source_key=f"synthetic.{i}")
        binding = copy.deepcopy(base["bindings"][0])
        binding.update(binding_id=f"binding.synthetic{i}", source_id=source["source_id"])
        binding["adapter"]["ha_entity_id"] = f"binary_sensor.synthetic_{i}"
        doc["sources"].append(source)
        doc["bindings"].append(binding)
    for i in range(100):
        contract = copy.deepcopy(base["contracts"][0])
        contract["contract_id"] = f"example.extra{i}"
        contract["inputs"][0]["ref"] = f"source.synthetic{i % 90}"
        doc["contracts"].append(contract)
    doc["catalogs"] = [{**catalog(), "catalog_id": f"example.catalog{i}"} for i in range(7)]
    doc["catalogs"][0]["catalog_id"] = "example.titles"
    return upgrade_document(doc)


async def seed_catalogs(runtime):
    state = runtime.state.clone()
    for catalog_index in range(7):
        cat_id = "example.titles" if catalog_index == 0 else f"example.catalog{catalog_index}"
        for offset in range(0, 1600, 800):
            changes = []
            for i in range(offset, offset + 800):
                change = {
                    "scope": "example",
                    "media_type": "music",
                    "signal_type": "title",
                    "key": f"Synthetic {i:05}",
                    "classification": "other",
                }
                if i % 2:
                    change.update(classification=None, master_key=f"Synthetic {i - 1:05}")
                changes.append(change)
            request = SeedWrite.model_validate(
                {
                    "catalog_id": cat_id,
                    "definition_version": 1,
                    "expected_active_revision": state.active_revision,
                    "expected_content_revision": offset // 800,
                    "request_id": str(uuid4()),
                    "reason": "Synthetic benchmark",
                    "changes": changes,
                }
            )
            prepare(state, runtime.config, request, {"kind": "synthetic"}, runtime.clock.now_utc())
    await runtime._commit(state)
    assert (
        sum(len(row["entries"]) for row in runtime.state.tables["catalog_head"].values()) == 11200
    )


async def cached_history(runtime, candidate, *, skip_unchanged):
    """Measured prototype, only invoked for identical synthetic sources."""
    active = {s.source_id: s for s in runtime.config.sources}
    revisions = {}
    checks = 0
    for source in candidate.sources:
        if skip_unchanged and active.get(source.source_id) == source:
            continue
        for row in await runtime.store.source_history(source.source_id, include_revision=True):
            revision = row["_registry_revision"]
            if revision not in revisions:
                historical = await runtime.store.get("registry_revision", str(revision))
                revisions[revision] = RegistryConfig.model_validate(historical["config"])
            old = next(s for s in revisions[revision].sources if s.source_id == source.source_id)
            inferred = {"boolean": "bool", "numeric": "number", "text": "text"}
            schema, previous = source.value_schema, old.value_schema
            assert source.source_type == old.source_type
            assert (schema.type if schema else inferred.get(source.source_type)) == (
                previous.type if previous else inferred.get(old.source_type)
            )
            assert (schema.unit if schema else None) == (previous.unit if previous else None)
            assert not previous or set(previous.value_catalog).issubset(
                schema.value_catalog if schema else ()
            )
            checks += 1
    return checks


async def with_stream(runtime, operation, target):
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    started = threading.Event()
    samples = []
    futures = []
    dropped = 0
    generated = 0
    original_process = runtime.process

    async def monitored_process(name, payload):
        if name != target:
            return await original_process(name, payload)
        started.set()
        try:
            return await original_process(name, payload)
        finally:
            stop.set()

    runtime.process = monitored_process

    def enqueue(obs, issued):
        nonlocal dropped
        if runtime.queue.full():
            dropped += 1
            return
        future = loop.create_future()
        future.add_done_callback(lambda f: samples.append((perf_counter() - issued) * 1000))
        futures.append(future)
        runtime.queue.put_nowait(Work("observation", obs, future, queued_at=issued))

    def produce():
        nonlocal generated
        started.wait()
        sequence = 0
        while not stop.is_set():
            sequence += 1
            generated += 1
            issued = perf_counter()
            now = runtime.clock.now_utc()
            obs = Observation(
                source_id="source.synthetic0",
                binding_id="binding.synthetic0",
                adapter="ha_state",
                value_raw=bool(sequence % 2),
                received_at=now,
                ha_time_fired=now,
                observation_kind="live_change",
                epoch_id="synthetic",
                ingest_seq=sequence,
            )
            loop.call_soon_threadsafe(enqueue, obs, issued)
            stop.wait(0.05)

    thread = threading.Thread(target=produce)
    thread.start()
    try:
        start = perf_counter()
        result = await operation()
        duration = (perf_counter() - start) * 1000
    finally:
        stop.set()
        started.set()
        await asyncio.to_thread(thread.join)
        await asyncio.gather(*futures)
        runtime.process = original_process
    samples.sort()

    def percentile(q):
        return samples[int((len(samples) - 1) * q)] if samples else None

    return result, {
        "duration_ms": duration,
        "observation_count": len(samples),
        "observation_generated": generated,
        "observation_dropped": dropped,
        "observation_p50_ms": percentile(0.5),
        "observation_p95_ms": percentile(0.95),
        "observation_max_ms": max(samples, default=None),
    }


async def main():
    capture = Capture()
    logger = logging.getLogger("core_contracts.runtime")
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
    await runtime.start()
    temporary = tempfile.TemporaryDirectory(prefix="wuschel-perf-")
    api = API(runtime, Tokens(Path(temporary.name)), str(uuid4()))
    client = TestClient(TestServer(api.application()))
    await client.start_server()
    headers = {"X-Authoring-Protocol": "2", "Authorization": "Bearer " + api.tokens.values["admin"]}

    async def post(path, body):
        response = await client.post("/api/v1/" + path, headers=headers, json=body)
        if response.status != 200:
            raise RuntimeError(f"synthetic endpoint failed: HTTP {response.status}")
        return await response.json()

    doc = synthetic_config()
    try:
        for revision in range(11):
            await activate(runtime, doc)
            print(json.dumps({"setup_revision": revision + 1}), flush=True)
            if revision in (0, 2, 4, 6):
                await runtime.submit("draft_create", {"config": doc})
        for _ in range(5):
            await runtime.submit("draft_create", {"config": doc})
        await seed_catalogs(runtime)
        print(json.dumps({"setup_catalog_entries": 11200}), flush=True)
        draft = await runtime.submit("draft_create", {"config": doc})
        package = await export(runtime)
        results = {}
        for name, operation in (
            ("validate", lambda: post(f"registry/drafts/{draft['draft_id']}/validate", {})),
            (
                "activation_preview",
                lambda: post(f"registry/drafts/{draft['draft_id']}/activation-preview", {}),
            ),
            ("import_preview", lambda: post("portable/preview", {"package": package})),
        ):
            samples = []
            for _ in range(3):
                capture.records.clear()
                target = {
                    "validate": "validate",
                    "activation_preview": "draft_preview",
                    "import_preview": "portable_preview",
                }[name]
                _, sample = await with_stream(runtime, operation, target)
                sample["timing"] = next(
                    (
                        r
                        for r in capture.records
                        if r["operation"] in {"validate", "draft_preview", "portable_preview"}
                    ),
                    None,
                )
                samples.append(sample)
                print(json.dumps({"operation": name, "sample": sample}), flush=True)
            results[name] = {
                "samples": samples,
                "median_ms": median(s["duration_ms"] for s in samples),
            }
        samples = []
        for _ in range(3):
            current = await runtime.submit("draft_create", {"config": doc})
            preview = await runtime.submit("draft_preview", {"draft_id": current["draft_id"]})
            payload = {
                "draft_id": current["draft_id"],
                "draft_version": current["draft_version"],
                "request_id": str(uuid4()),
                "expected_active_revision": runtime.state.active_revision,
                "comparison_digest": preview["comparison_digest"],
                "confirmations": preview["required_confirmations"],
            }
            capture.records.clear()
            _, sample = await with_stream(
                runtime,
                lambda: post(f"registry/drafts/{current['draft_id']}/activate", payload),
                "activate",
            )
            sample["timing"] = next(
                (r for r in capture.records if r["operation"] == "activate"), None
            )
            samples.append(sample)
            print(json.dumps({"operation": "activation", "sample": sample}), flush=True)
        results["activation"] = {
            "samples": samples,
            "median_ms": median(s["duration_ms"] for s in samples),
        }
        candidate = RegistryConfig.model_validate(doc)
        prototypes = {}
        for name, callback in (
            ("baseline", lambda: runtime._source_types(candidate)),
            ("cache", lambda: cached_history(runtime, candidate, skip_unchanged=False)),
            ("skip_identical", lambda: cached_history(runtime, candidate, skip_unchanged=True)),
        ):
            times = []
            for _ in range(3):
                start = perf_counter()
                await callback()
                times.append((perf_counter() - start) * 1000)
                print(json.dumps({"prototype": name, "ms": times[-1]}), flush=True)
            prototypes[name] = {"samples_ms": times, "median_ms": median(times)}
        print(
            json.dumps(
                {
                    "store": "MemoryStore",
                    "sources": 90,
                    "contracts": 100,
                    "initial_revisions": 11,
                    "catalog_entries": 11200,
                    "open_drafts_initial": 9,
                    "observation_rate_hz": 20,
                    "results": results,
                    "prototypes": prototypes,
                },
                indent=2,
            )
        )
    finally:
        await client.close()
        temporary.cleanup()
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
