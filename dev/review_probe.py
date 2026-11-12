"""Synthetic review measurements; PostgreSQL runs only in an isolated *_test DB.

No timing assertions: retain correctness assertions and report actual latency,
query plans, row growth and node writes for independent review.
"""

import asyncio
import copy
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from core_contracts.clock import FakeClock
from core_contracts.evidence import Observation
from core_contracts.history import TIME_SQL, HistoryQuery
from core_contracts.persistence import MemoryStore, PostgresStore
from core_contracts.registry import _compile_definition, diff
from core_contracts.runtime import Runtime
from core_contracts.schema import TypeRegistry
from core_contracts.testing.authoring import activate_draft
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import validate_document as validate

ROOT = Path(__file__).resolve().parents[1]


def document(count=1, definitions=1):
    schema = dict(type="bool", required=True, physical_state=False)
    return dict(
        config_schema_version=1,
        fingerprint_version=2,
        dependencies=[dict(dependency_id="dep.test", kind="ha", monitor="internal")],
        sources=[
            dict(
                source_id=f"source.{name}",
                source_type="boolean",
                source_origin="local",
                physical_source_key=f"physical.{name}",
                dependency_id="dep.test",
                value_schema=schema,
                freshness=dict(mode="report_heartbeat", interval_s=3600, future_tolerance_s=1),
            )
            for name in ("a", "b")
        ],
        bindings=[
            dict(
                binding_id=f"binding.{name}",
                source_id=f"source.{name}",
                adapter=dict(kind="ha_state", ha_entity_id=f"sensor.{name}"),
                transform=dict(kind="type_cast", target="bool"),
            )
            for name in ("a", "b")
        ],
        definitions=[
            dict(
                type_id=f"test.probe{number}",
                type_version=1,
                inputs=[dict(name="a", schema=schema)],
                schema=dict(fields=dict(value=schema)),
                outputs=dict(value=dict(op="input", name="a")),
            )
            for number in range(definitions)
        ],
        contracts=[
            dict(
                contract_id=f"fixture.{number}",
                type_id=f"test.probe{number % definitions}",
                type_version=1,
                enabled=True,
                inputs=[dict(name="a", ref="source.a")],
                parameters={},
            )
            for number in range(count)
        ],
        catalogs=[],
    )


def sample(runtime, seq, name="a"):
    return Observation(
        source_id=f"source.{name}",
        binding_id=f"binding.{name}",
        adapter="ha_state",
        value_raw=True,
        observation_kind="report",
        ha_last_reported=runtime.clock.now_utc(),
        received_at=runtime.clock.now_utc(),
        epoch_id=runtime.state.epoch_id,
        ingest_seq=seq,
        registry_revision=runtime.state.active_revision,
        reference_version=2,
    )


async def configured(store, config):
    runtime = Runtime(store, FakeClock(datetime(2026, 1, 1, 12, tzinfo=UTC)), type_registry())
    await runtime.start()
    draft = await runtime.submit("draft_create", dict(config=config))
    await activate_draft(runtime, draft, expected_active_revision=0)
    return runtime


async def compilation_probe():
    results = []
    for count, definitions in ((1, 1), (256, 1), (1024, 1), (128, 128)):
        config, base = document(count, definitions), type_registry()
        _compile_definition.cache_clear()
        start = time.perf_counter()
        parsed = validate(config, base)
        cold = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        validate(config, base)
        warm = (time.perf_counter() - start) * 1000
        edited = copy.deepcopy(config)
        edited["contracts"][0]["description"] = "editorial update"
        changed = validate(edited, base)
        start = time.perf_counter()
        diff(parsed, changed, base)
        diff_ms = (time.perf_counter() - start) * 1000
        runtime = await configured(MemoryStore(), config)
        calls = 0
        original = TypeRegistry.register

        def register(self, declaration):
            nonlocal calls
            calls += 1
            return original(self, declaration)

        start = time.perf_counter()
        with patch.object(TypeRegistry, "register", register):
            await runtime.submit("observation", sample(runtime, 1))
        evaluation_ms = (time.perf_counter() - start) * 1000
        assert calls <= 4 * definitions, (count, definitions, calls)
        await runtime.stop()
        results.append(
            dict(
                contracts=count,
                definitions=definitions,
                cold_validation_ms=round(cold, 3),
                warm_validation_ms=round(warm, 3),
                diff_ms=round(diff_ms, 3),
                input_transaction_ms=round(evaluation_ms, 3),
                type_registrations_per_input=calls,
            )
        )
    return results


async def database_probe():
    dsn = os.environ.get("TEST_DATABASE_DSN")
    if not dsn:
        return {"status": "not_run_no_test_database"}
    parts = urlsplit(dsn)
    if not parts.path.endswith("_test"):
        raise ValueError("isolated test database required")
    admin = await asyncpg.connect(dsn, ssl=False)
    name = "review_" + uuid4().hex + "_test"
    await admin.execute(f'CREATE DATABASE "{name}"')
    store = PostgresStore(
        urlunsplit(parts._replace(path="/" + name)), ROOT / "migrations", tls=False
    )
    runtime = None
    try:
        await store.open()
        runtime = await configured(store, document())
        await runtime.submit("observation", sample(runtime, 1))
        writer = store.writer
        assert writer is not None
        await writer.execute("CREATE TEMP TABLE node_writes (n integer)")
        await writer.execute(
            "CREATE FUNCTION pg_temp.count_node_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO pg_temp.node_writes VALUES (1); RETURN NEW; END $$"
        )
        await writer.execute(
            "CREATE TRIGGER probe_node_writes AFTER INSERT OR UPDATE ON node_state FOR EACH ROW EXECUTE FUNCTION pg_temp.count_node_write()"
        )
        baseline = await writer.fetchval("SELECT count(*) FROM source_observation_history")
        samples = []
        for seq in range(2, 1002):
            runtime.clock.advance(0.01)  # synthetic 100 reports/s, unchanged value
            start = time.perf_counter()
            await runtime.submit("observation", sample(runtime, seq, "b"))
            samples.append((time.perf_counter() - start) * 1000)
        writes = await writer.fetchval("SELECT count(*) FROM pg_temp.node_writes")
        growth = await writer.fetchval("SELECT count(*) FROM source_observation_history") - baseline
        assert writes == 0 and growth == 1000
        template = sample(runtime, 1002, "b").storage_document()
        await runtime.stop()
        runtime = None
        rows = [
            (f"v2:probe:{i}", json.dumps({**template, "ingest_seq": i})) for i in range(100_000)
        ]
        start = time.perf_counter()
        await writer.copy_records_to_table(
            "source_observation_history", records=rows, columns=("key", "payload")
        )
        seed_ms = (time.perf_counter() - start) * 1000
        await writer.execute("ANALYZE source_observation_history")
        plans = {}
        for label, sql, params in (
            (
                "evidence_primary_key",
                "SELECT payload FROM source_observation_history WHERE key=$1",
                ("v2:probe:50000",),
            ),
            (
                "history_latest",
                f"SELECT key,payload FROM source_observation_history ORDER BY {TIME_SQL} DESC,key DESC LIMIT 201",
                (),
            ),
            (
                "history_source_time",
                f"SELECT key,payload FROM source_observation_history WHERE payload->>'source_id'=$1 AND {TIME_SQL}>=$2::timestamptz ORDER BY {TIME_SQL} DESC,key DESC LIMIT 201",
                ("source.b", datetime(2026, 1, 1, tzinfo=UTC)),
            ),
        ):
            plans[label] = json.loads(
                await writer.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, *params)
            )[0]
        assert "Index" in str(plans["evidence_primary_key"]["Plan"])
        assert (await store.evidence("v2:probe:50000"))["ingest_seq"] == 50000
        assert await store.evidence("v2:missing") is None
        history = await store.history("source_observation_history", HistoryQuery(limit=200))
        assert len(history["items"]) == 200 and history["next_cursor"]
        total = await writer.fetchval("SELECT pg_total_relation_size('source_observation_history')")
        count = await writer.fetchval("SELECT count(*) FROM source_observation_history")
        return dict(
            status="passed",
            postgres=await writer.fetchval("SHOW server_version"),
            unchanged_foreign_reports=1000,
            simulated_reports_per_second=100,
            node_writes=writes,
            observation_rows_added=growth,
            median_transaction_ms=round(statistics.median(samples), 3),
            p95_transaction_ms=round(sorted(samples)[950], 3),
            seed_100k_ms=round(seed_ms, 3),
            rows=count,
            relation_bytes=total,
            bytes_per_row=round(total / count, 2),
            query_plans=plans,
        )
    finally:
        if runtime is not None:
            await runtime.stop()
        await store.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def main():
    result = {"compilation": await compilation_probe(), "postgres": await database_probe()}
    path = ROOT / "dist/alpha4-review-performance.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
