"""b25 published-truth baseline. Record deliberately with WUSCHEL_REPLAY_RECORD=1.

Only random UUIDs are renamed, including occurrences inside evidence references
and table keys. Values, ordering, timestamps and every other byte stay visible.
All configurations derive from conftest's synthetic registry or examples/.
"""

import asyncio
import copy
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from core_contracts.clock import FakeClock
from core_contracts.managed_catalog import head
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry

from .conftest import activate
from .test_alpha9_manual_source import enter, manual_config
from .test_beta_sleep import command as sleep_command
from .test_beta_sleep import fields as sleep_fields
from .test_beta_sleep import observe as sleep_observe
from .test_beta_sleep import ready as sleep_ready
from .test_catalog_care import care
from .test_declarative import definition, observation, registry, schema
from .test_managed_catalog import configured, identity, lookup_rule, request
from .test_phase1a import add_source, contract, field

FIXTURES = Path(__file__).parent / "fixtures/replay"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TABLES = ("contract_state_current", "sm_instance", "deadline", "history_gap")


def canonical(document):
    identities = {}

    def rename(match):
        value = match.group()
        return identities.setdefault(value, f"uuid-{len(identities) + 1}")

    def normalize(value):
        if isinstance(value, str):
            return UUID.sub(rename, value)
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            # Traverse in publication/insertion order before canonical key sorting.
            return {normalize(key): normalize(item) for key, item in value.items()}
        return value

    return json.dumps(normalize(document), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


class Replay:
    def __init__(self):
        self.store = MemoryStore()
        self.clock = FakeClock(datetime(2026, 1, 1, 12, tzinfo=UTC))
        self.messages = []
        self.results = []
        self.runtime = None

    async def start(self):
        self.runtime = Runtime(self.store, self.clock, type_registry())
        # Subscribe before startup so epoch resync and initial service state count.
        self.queue = self.runtime.subscribe(max_queue=10000)
        await self.runtime.start()
        self.collect()

    def collect(self):
        while not self.queue.empty():
            self.messages.append(copy.deepcopy(self.queue.get_nowait()))

    async def step(self, operation, payload=None):
        result = await self.runtime.submit(operation, payload)
        self.collect()
        return result

    async def activate(self, config):
        result = await activate(self.runtime, config)
        self.collect()
        return result

    async def advance(self, seconds):
        self.clock.advance(seconds)
        await self.step("tick")

    async def restart(self):
        await self.runtime.stop()
        self.collect()
        await self.start()

    async def output(self):
        await self.runtime.queue.join()
        self.collect()
        return {
            "messages": self.messages,
            "results": self.results,
            "snapshot": self.runtime.snapshot(),
            "tables": {name: copy.deepcopy(self.store.state.tables[name]) for name in TABLES},
        }


def command(runtime):
    return {
        "command_id": str(uuid4()),
        "contract_id": "fixture.machine",
        "command": "request_b",
        "args": {},
        "origin": {"kind": "test", "actor": "synthetic", "client_name": "replay"},
        "issued_at": runtime.clock.now_utc(),
        "valid_until": runtime.clock.now_utc() + timedelta(seconds=30),
    }


async def duration_deadline(replay, config):
    config["contracts"] = [
        contract(
            "stable",
            "temporal",
            ["source.a"],
            {"operation": "stable_for", "duration_s": 2, "accepts_held": False},
        ),
        contract(
            "machine", "state_machine", ["fixture.stable"], {"deadline_s": 5, "sessions": True}
        ),
    ]
    await replay.activate(config)
    await replay.step("observation", observation(replay.runtime, True))
    await replay.advance(2)
    assert field(replay.runtime, "stable")["value"] is True
    result = await replay.step("command", command(replay.runtime))
    assert result["status"] == "accepted"
    await replay.advance(5)
    assert (
        replay.runtime.snapshot()["contracts"]["fixture.machine"]["state_machine"]["state"] == "c"
    )


async def fusion_freshness(replay, config):
    add_source(config, "b")
    for source in config["sources"]:
        source.update(
            source_type="numeric",
            freshness={"mode": "periodic_ttl", "interval_s": 10, "future_tolerance_s": 1},
        )
    for binding in config["bindings"]:
        binding["transform"] = {"kind": "type_cast", "target": "number"}
    config["contracts"] = [
        contract("fusion", "fusion", ["source.a", "source.b"], {"strategy": "first_healthy"}),
        contract(
            "bucket",
            "boolean",
            ["fixture.fusion"],
            {"operator": "bucket", "initial": False, "lower": 10, "upper": 20},
        ),
    ]
    await replay.activate(config)
    for name, value in (("b", 25), ("a", 5), ("a", 25), ("a", 15), ("a", 5)):
        await replay.step("observation", observation(replay.runtime, value, name))
    await replay.advance(11)
    assert field(replay.runtime, "bucket")["status"] == "unknown"


async def restart(replay, config):
    config["contracts"] = [
        contract("machine", "state_machine", ["source.a"], {"deadline_s": 10, "sessions": True})
    ]
    await replay.activate(config)
    await replay.step("observation", observation(replay.runtime, True))
    await replay.step("command", command(replay.runtime))
    await replay.restart()
    await replay.step(
        "snapshot",
        [observation(replay.runtime, True).model_copy(update={"observation_kind": "snapshot"})],
    )
    await replay.step("snapshot_complete")
    await replay.advance(11)
    assert (
        replay.runtime.snapshot()["contracts"]["fixture.machine"]["state_machine"]["state"] == "c"
    )


async def commit_outage(replay, config):
    await replay.activate(config)
    await replay.step("observation", observation(replay.runtime, True))
    before = replay.runtime.snapshot()

    def fail():
        raise OSError("synthetic commit outage")

    replay.store.before_commit = fail
    with pytest.raises(OSError, match="synthetic commit outage"):
        await replay.step("observation", observation(replay.runtime, False))
    replay.collect()
    assert replay.runtime.snapshot()["contracts"] == before["contracts"]
    replay.runtime.ingest(observation(replay.runtime, False))
    replay.store.before_commit = None
    await replay.step("recover")
    assert any(row["reason"] == "db_outage" for row in await replay.store.rows("history_gap"))


async def ha_reconnect(replay, config):
    await replay.activate(config)
    await replay.step("bridge_state", ("connected", "connected"))
    await replay.step(
        "snapshot",
        [observation(replay.runtime, True).model_copy(update={"observation_kind": "snapshot"})],
    )
    await replay.step("snapshot_complete")
    await replay.advance(1)
    await replay.step("bridge_state", ("unavailable", "disconnected"))
    await replay.advance(5)
    await replay.step("bridge_state", ("connected", "connected"))
    await replay.step(
        "snapshot",
        [observation(replay.runtime, False).model_copy(update={"observation_kind": "snapshot"})],
    )
    await replay.step("snapshot_complete")
    assert field(replay.runtime)["value"] is False


async def management(replay, config):
    # Runtime.submit routes these operations through management.execute/Planner,
    # the management worker in this baseline; never call Runtime.process here.
    validated = await replay.step("validate", config)
    replay.results.append(validated)
    await replay.activate(config)
    await replay.step("observation", observation(replay.runtime, True))
    config["contracts"][0]["enabled"] = False
    await replay.activate(config)
    await replay.step("rollback", {"revision": 1, "expected_active_revision": 2})
    assert replay.runtime.state.active_revision == 3


async def catalog(replay, config):
    document = configured(config)
    rule = lookup_rule("unused")
    rule["key"] = {"op": "input", "name": "a"}
    data = definition(rule)
    data.update(language_version=4)
    data["inputs"][0]["schema"] = schema("text")
    data["schema"]["fields"]["value"] = schema("enum", value_catalog=["other", "example"])
    document = registry(document, data)
    document["fingerprint_version"] = 3
    await replay.activate(document)
    await replay.step("bridge_state", ("connected", "connected"))
    await replay.step("snapshot_complete")
    await replay.step("observation", observation(replay.runtime, "Synthetic First"))
    assert field(replay.runtime)["value"] == "other"
    await replay.advance(2)
    await replay.step("catalog_tick")
    assert replay.runtime.state.tables["catalog_sighting"]
    await replay.step("catalog_write", {"request": request(replay.runtime)})
    assert field(replay.runtime)["value"] == "example"
    selected = [identity("example", "music", "title", "Synthetic First")]
    payload = care(replay.runtime, selected, "review")
    replay.results.append(await replay.step("catalog_care_preview", {"request": payload}))
    replay.results.append(await replay.step("catalog_care_write", {"request": payload}))
    replay.results.append(
        copy.deepcopy(head(replay.runtime.state, replay.runtime.config.catalogs[0]))
    )


async def wake(replay, config):
    await sleep_ready(replay.runtime, config, wake_time="15:00", minimum=480)
    replay.collect()
    now = replay.clock.now_utc()
    cache = {
        "window_start": (now - timedelta(days=1)).isoformat(),
        "window_end": (now + timedelta(days=32)).isoformat(),
        "fetched_at": now.isoformat(),
        "expires_at": (now + timedelta(days=2)).isoformat(),
        "events": [
            {
                "summary": "Synthetic appointment",
                "start": (now + timedelta(hours=3)).isoformat(),
                "end": (now + timedelta(hours=4)).isoformat(),
                "all_day": False,
                "key": "synthetic",
            }
        ],
    }
    await sleep_observe(replay.runtime, "appointments", cache)
    replay.collect()
    await sleep_observe(
        replay.runtime, "manual_exception", {"date": "2026-01-01", "wake_time": "16:00"}
    )
    replay.collect()
    assert (await sleep_command(replay.runtime, "start_pre_sleep"))["status"] == "accepted"
    replay.collect()
    assert sleep_fields(replay.runtime)["wake_plan_date"]["value"] == "2026-01-01"


async def manual_command(replay, config):
    await replay.activate(manual_config(config, ttl=10))
    result = await enter(replay.runtime)
    replay.collect()
    replay.results.append(result)
    assert field(replay.runtime)["value"] is True
    await replay.advance(11)
    assert field(replay.runtime)["status"] == "unknown"
    # Exercise an actual command on the same Runtime after the manual source.
    config["contracts"] = [
        contract("machine", "state_machine", ["source.a"], {"deadline_s": 10, "sessions": True})
    ]
    await replay.activate(config)
    await replay.step("observation", observation(replay.runtime, True))
    assert (await replay.step("command", command(replay.runtime)))["status"] == "accepted"


async def event_storm(replay, config):
    await replay.activate(config)
    original_queue = replay.runtime.queue
    replay.runtime.queue = asyncio.Queue(1)
    try:
        # No await: the live worker cannot drain between these arrivals.
        for index in range(100):
            replay.clock.advance(0.01)
            replay.runtime.ingest(observation(replay.runtime, bool(index % 2)))
        assert replay.runtime.latest
        queued = replay.runtime.queue.get_nowait()
        await replay.runtime.process(queued.operation, queued.payload)
        replay.runtime.queue.task_done()
        replay.collect()
        await replay.runtime._drain_overflow()
        replay.collect()
    finally:
        replay.runtime.queue = original_queue
    assert field(replay.runtime)["value"] is True
    assert any(row["reason"] == "ingest_overflow" for row in await replay.store.rows("history_gap"))


SCENARIOS = (
    duration_deadline,
    fusion_freshness,
    restart,
    commit_outage,
    ha_reconnect,
    management,
    catalog,
    wake,
    manual_command,
    event_storm,
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.__name__)
async def test_replay(scenario, config):
    replay = Replay()
    await replay.start()
    try:
        await scenario(replay, config)
        actual = canonical(await replay.output())
        path = FIXTURES / f"{scenario.__name__}.json"
        if os.environ.get("WUSCHEL_REPLAY_RECORD") == "1":
            FIXTURES.mkdir(parents=True, exist_ok=True)
            path.write_text(actual, encoding="utf-8", newline="\n")
        else:
            assert path.is_file(), "Record explicitly with WUSCHEL_REPLAY_RECORD=1"
            assert actual == path.read_text(encoding="utf-8")
    finally:
        await replay.runtime.stop()
