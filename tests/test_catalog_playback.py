"""b20: catalog scheduling cannot alter the existing per-observation resolver."""

import copy

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.managed_catalog import head_key
from core_contracts.model import canonical
from tests.conftest import activate
from tests.test_beta_media import configuration, observe


async def advance(runtime, seconds):
    runtime.clock.advance(seconds)
    await runtime.submit("catalog_tick")


def counts(runtime):
    return {r["key"]: r["seen_count"] for r in runtime.state.tables["catalog_sighting"].values()}


async def music(runtime, config):
    await activate(runtime, configuration(config, "media.homepods_class"))
    await observe(runtime, "applicable", True)
    await observe(runtime, "artist", "Synthetic Artist A")
    await observe(runtime, "title", "Synthetic Title A")


@pytest.mark.parametrize("delay", [0.7, 1.1, 2, 3, 8])
@pytest.mark.parametrize("artist_first", [False, True])
async def test_split_pair_and_repeated_reports_count_real_playbacks(
    runtime, config, delay, artist_first
):
    await music(runtime, config)
    assert counts(runtime) == {}
    await advance(runtime, 2)
    original = canonical(["Synthetic Artist A", "Synthetic Title A"])
    target = canonical(["Synthetic Artist B", "Synthetic Title B"])
    assert counts(runtime) == {original: 1}
    first, second = ("artist", "title") if artist_first else ("title", "artist")
    await observe(
        runtime, first, "Synthetic Artist B" if first == "artist" else "Synthetic Title B"
    )
    await advance(runtime, delay)
    assert counts(runtime) == {original: 1}
    await observe(
        runtime, second, "Synthetic Artist B" if second == "artist" else "Synthetic Title B"
    )
    await advance(runtime, 2)
    assert counts(runtime) == {original: 1, target: 1}
    for _ in range(3):
        await observe(runtime, "title", "Synthetic Title B", kind="report")
    await advance(runtime, 20)
    assert counts(runtime) == {original: 1, target: 1}


@pytest.mark.parametrize("empty_seconds", [0.5, 0.7, 1.1])
async def test_briefly_empty_title_preserves_transition_protection(runtime, config, empty_seconds):
    await music(runtime, config)
    await advance(runtime, 2)
    original = canonical(["Synthetic Artist A", "Synthetic Title A"])
    target = canonical(["Synthetic Artist B", "Synthetic Title B"])
    assert counts(runtime) == {original: 1}
    await observe(runtime, "title", "")
    await advance(runtime, empty_seconds)
    await observe(runtime, "title", "Synthetic Title B")
    await advance(runtime, 3)
    assert counts(runtime) == {original: 1}
    await observe(runtime, "artist", "Synthetic Artist B")
    await advance(runtime, 2)
    assert counts(runtime) == {original: 1, target: 1}


async def test_same_artist_next_title_waits_out_split_window(runtime, config):
    await music(runtime, config)
    await advance(runtime, 2)
    await observe(runtime, "title", "Synthetic Title B")
    await advance(runtime, 9.9)
    assert sum(counts(runtime).values()) == 1
    await advance(runtime, 0.1)
    assert counts(runtime)[canonical(["Synthetic Artist A", "Synthetic Title B"])] == 1


@pytest.mark.parametrize("type_id", ["media.ps5_class", "media.homepods_class"])
@pytest.mark.parametrize("clear_title", [False, True])
async def test_same_title_counts_again_after_applicability_ends(
    runtime, config, type_id, clear_title
):
    await activate(runtime, configuration(config, type_id))
    await observe(runtime, "applicable", True)
    if type_id == "media.homepods_class":
        await observe(runtime, "artist", "Synthetic Artist")
    await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    await observe(runtime, "applicable", False)
    if clear_title:
        await observe(runtime, "title", "")
    await advance(runtime, 3600)
    assert sum(counts(runtime).values()) == 1
    await observe(runtime, "applicable", True)
    if clear_title:
        await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 2


@pytest.mark.parametrize("value", ["", None])
async def test_empty_or_unknown_title_ends_playback_without_player_frame(runtime, config, value):
    await activate(runtime, configuration(config, "media.ps5_class"))
    await observe(runtime, "applicable", True)
    await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    await observe(runtime, "title", value)
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 2


@pytest.mark.parametrize("availability", ["unknown", "unavailable"])
async def test_unusable_title_ends_playback_without_player_frame(runtime, config, availability):
    from core_contracts.evidence import Observation

    await activate(runtime, configuration(config, "media.ps5_class"))
    await observe(runtime, "applicable", True)
    await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    runtime.clock.advance(0.01)
    now = runtime.clock.now_utc()
    old = Observation.model_validate(
        runtime.state.tables["source_observation_current"]["binding.title"]
    )
    await runtime.submit(
        "observation",
        old.model_copy(
            update={
                "availability": availability,
                "received_at": now,
                "ha_time_fired": now,
                "ingest_seq": runtime.state.generation + 1,
            }
        ),
    )
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 2


@pytest.mark.parametrize("type_id", ["media.ps5_class", "media.homepods_class"])
async def test_short_reconnect_keeps_current_playback_baseline(runtime, config, type_id):
    from core_contracts.evidence import Observation

    await activate(runtime, configuration(config, type_id))
    await observe(runtime, "applicable", True)
    if type_id == "media.homepods_class":
        await observe(runtime, "artist", "Synthetic Artist")
    await observe(runtime, "title", "Synthetic Replay")
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    await runtime.submit("gap", "ha_reconnect")
    runtime.clock.advance(0.1)
    snapshots = [
        Observation.model_validate(row).model_copy(update={"observation_kind": "snapshot"})
        for row in runtime.state.tables["source_observation_current"].values()
    ]
    await runtime.submit("snapshot", snapshots)
    await observe(runtime, "title", "Synthetic Replay", kind="report")
    await advance(runtime, 11)
    assert sum(counts(runtime).values()) == 1


async def test_one_inactive_consumer_does_not_restart_a_shared_playback(runtime, config):
    doc = configuration(config, "media.ps5_class")
    source = copy.deepcopy(next(s for s in doc["sources"] if s["source_id"] == "source.applicable"))
    source.update(source_id="source.other", physical_source_key="synthetic.other")
    doc["sources"].append(source)
    doc["bindings"].append(
        dict(binding_id="binding.other", source_id="source.other", adapter=dict(kind="manual"))
    )
    consumer = copy.deepcopy(doc["contracts"][0])
    consumer["contract_id"] = "fixture.other"
    next(i for i in consumer["inputs"] if i["name"] == "applicable")["ref"] = "source.other"
    doc["contracts"].append(consumer)
    await activate(runtime, doc)
    await observe(runtime, "applicable", True)
    await observe(runtime, "other", True)
    await observe(runtime, "title", "Synthetic Shared Playback")
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    await observe(runtime, "applicable", False)
    await observe(runtime, "applicable", True)
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    await observe(runtime, "applicable", False)
    await observe(runtime, "other", False)
    await observe(runtime, "applicable", True)
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 2


@pytest.mark.parametrize("operation", ["gap", "restore"])
async def test_unfinished_playback_is_not_counted_after_a_gap_or_restore(
    runtime, config, operation
):
    await music(runtime, config)
    assert counts(runtime) == {}
    if operation == "gap":
        await runtime.submit("gap", "synthetic gap")
    else:
        await runtime._restore("synthetic restore")
    await advance(runtime, 3)
    assert counts(runtime) == {}
    await observe(runtime, "artist", "Synthetic Artist A", kind="report")
    await observe(runtime, "title", "Synthetic Title A", kind="report")
    await advance(runtime, 11)
    assert counts(runtime) == {}
    await observe(runtime, "artist", "Synthetic Artist B")
    await observe(runtime, "title", "Synthetic Title B")
    await advance(runtime, 2)
    assert counts(runtime) == {canonical(["Synthetic Artist B", "Synthetic Title B"]): 1}


async def test_catalog_deadline_never_evaluates_contracts(runtime, config, monkeypatch):
    await music(runtime, config)
    before = copy.deepcopy(runtime.state.tables["contract_state_current"])
    nodes = copy.deepcopy(runtime.state.tables["node_state"])

    def forbidden(*args, **kwargs):
        pytest.fail("a catalog deadline evaluated a contract")

    monkeypatch.setattr(runtime, "_evaluate", forbidden)
    await advance(runtime, 2)
    assert sum(counts(runtime).values()) == 1
    assert runtime.state.tables["contract_state_current"] == before
    assert runtime.state.tables["node_state"] == nodes


async def test_catalog_notification_is_only_published_after_a_successful_commit(runtime, config):
    import asyncio

    await music(runtime, config)
    subscriber = asyncio.Queue()
    runtime.subscribers.add(subscriber)

    def fail():
        raise RuntimeError("synthetic commit failure")

    runtime.catalog_alarm.cancel()
    runtime.clock.advance(2)
    runtime.store.before_commit = fail
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await runtime.submit("catalog_tick")
    assert counts(runtime) == {}
    failed_messages = []
    while not subscriber.empty():
        failed_messages.append(subscriber.get_nowait())
    assert not any(item.get("type") == "catalog_changed" for item in failed_messages)
    runtime.store.before_commit = None
    await runtime.submit("recover")
    await observe(runtime, "artist", "Synthetic Artist B")
    await observe(runtime, "title", "Synthetic Title B")
    await advance(runtime, 2)
    messages = []
    while not subscriber.empty():
        messages.append(subscriber.get_nowait())
    assert {"type": "catalog_changed"} in messages
    assert sum(counts(runtime).values()) == 1
    runtime.subscribers.remove(subscriber)


def player_document(config):
    doc = configuration(config, "media.homepods_class")
    doc["dependencies"].append(dict(dependency_id="dep.ha", kind="ha", monitor="internal"))
    for name, attribute in [("artist", "media_artist"), ("title", "media_title")]:
        source = next(s for s in doc["sources"] if s["source_id"] == "source." + name)
        source.pop("origin")
        source["dependency_id"] = "dep.ha"
        binding = next(b for b in doc["bindings"] if b["source_id"] == "source." + name)
        binding["adapter"] = dict(
            kind="ha_attribute", ha_entity_id="media_player.synthetic", ha_attribute=attribute
        )
    return doc


async def player_event(runtime, adapter, artist, title, state="playing", **attributes):
    runtime.clock.advance(0.01)
    now = runtime.clock.now_utc().isoformat()
    await adapter.event(
        dict(
            kind="changed",
            time_fired=now,
            entity_id="media_player.synthetic",
            new_state=dict(
                state=state,
                attributes=dict(media_artist=artist, media_title=title, **attributes),
                last_changed=now,
                last_updated=now,
            ),
        ),
        runtime.config,
    )
    await runtime.queue.join()


@pytest.mark.parametrize("ended_state", ["idle", "off"])
async def test_catalog_frame_pause_repeat_seek_and_new_start(runtime, config, ended_state):
    await activate(runtime, player_document(config))
    await observe(runtime, "applicable", True)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
        artist, title = "Synthetic Artist A", "Synthetic Title A"
        await player_event(
            runtime,
            adapter,
            artist,
            title,
            media_position=0,
            media_duration=100,
            media_position_updated_at="1",
        )
        await advance(runtime, 2)
        assert sum(counts(runtime).values()) == 1
        await player_event(
            runtime, adapter, artist, title, state="paused", media_position=20, media_duration=100
        )
        await player_event(runtime, adapter, artist, title, media_position=20, media_duration=100)
        await advance(runtime, 2)
        assert sum(counts(runtime).values()) == 1
        # Seek near the beginning is not proof of a repeat.
        await player_event(
            runtime,
            adapter,
            artist,
            title,
            media_position=1,
            media_duration=100,
            media_position_updated_at="2",
        )
        await advance(runtime, 2)
        assert sum(counts(runtime).values()) == 1
        await player_event(
            runtime,
            adapter,
            artist,
            title,
            media_position=99,
            media_duration=100,
            media_position_updated_at="3",
        )
        await player_event(
            runtime,
            adapter,
            artist,
            title,
            media_position=0,
            media_duration=100,
            media_position_updated_at="4",
        )
        await advance(runtime, 2)
        assert sum(counts(runtime).values()) == 2
        await player_event(runtime, adapter, artist, title, state=ended_state)
        await player_event(runtime, adapter, artist, title)
        await advance(runtime, 2)
        assert sum(counts(runtime).values()) == 3


async def test_multiattribute_events_keep_b19_intermediate_contract_values(
    runtime, config, monkeypatch
):
    """A per-event resolver batch would remove the asserted intermediate value."""
    await activate(runtime, player_document(config))
    await observe(runtime, "applicable", True)
    from core_contracts.managed_catalog import identity
    from core_contracts.model import checksum

    catalog = runtime.config.catalogs[0]
    entries = {}
    for artist, title, value in [
        ("Synthetic Artist A", "Synthetic Title A", "normal"),
        ("Synthetic Artist A", "Synthetic Title B", "boost"),
        ("Synthetic Artist B", "Synthetic Title B", "stumm"),
    ]:
        key = canonical([artist, title])
        entries[identity("homepods", "music", "title", key)] = dict(
            scope="homepods",
            media_type="music",
            signal_type="title",
            key=key,
            classification=value,
            tombstone=False,
        )
    # Isolated in-memory fixture for resolver output, not a live content write.
    from core_contracts.managed_catalog import definition_digest

    runtime.state.tables["catalog_head"][head_key(catalog)] = dict(
        catalog_id=catalog.catalog_id,
        definition_version=1,
        definition_digest=definition_digest(catalog),
        revision=1,
        entries=entries,
    )
    trace = []
    evaluate = runtime._evaluate

    def recording(*args, **kwargs):
        answer = evaluate(*args, **kwargs)
        state = args[0]
        field = state.tables["contract_state_current"]["fixture.media"]["fields"]["value"]
        trace.append(
            (
                field["value"],
                state.tables["contract_state_current"]["fixture.media"]["computed_at"],
                copy.deepcopy(state.tables["node_state"]["fixture.media"]),
            )
        )
        return answer

    monkeypatch.setattr(runtime, "_evaluate", recording)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")
        await player_event(runtime, adapter, "Synthetic Artist A", "Synthetic Title A")
        runtime.clock.advance(1)
        trace.clear()
        await player_event(runtime, adapter, "Synthetic Artist B", "Synthetic Title B")
        at = runtime.clock.now_utc().isoformat().replace("+00:00", "Z")
    assert [value for value, _, _ in trace] == ["boost", "stumm"]
    assert [since for _, since, _ in trace] == [at, at]
    assert all("machine" not in node for _, _, node in trace)
    assert checksum(runtime.state.tables["contract_state_current"])


async def test_multiattribute_input_retains_transient_machine_start_and_absolute_deadline(
    runtime, config, monkeypatch
):
    from datetime import datetime, timedelta

    from tests.test_declarative import literal, machine_definition, registry, schema

    data = machine_definition()
    data["inputs"].append(dict(name="b", schema=schema()))
    data["machine"]["reinitialize_when"] = literal(True)
    data["machine"]["transitions"][0].update(
        event="input",
        argument_equals={},
        guard=dict(op="and", args=[dict(op="input", name="a"), dict(op="input", name="b")]),
    )
    doc = registry(config, data)
    doc["sources"].append(
        {**doc["sources"][0], "source_id": "source.b", "physical_source_key": "physical.b"}
    )
    doc["bindings"][0]["adapter"] = dict(
        kind="ha_attribute", ha_entity_id="sensor.synthetic", ha_attribute="first"
    )
    doc["bindings"].append(
        {
            **copy.deepcopy(doc["bindings"][0]),
            "binding_id": "binding.b",
            "source_id": "source.b",
            "adapter": dict(
                kind="ha_attribute", ha_entity_id="sensor.synthetic", ha_attribute="second"
            ),
        }
    )
    doc["contracts"][0]["inputs"].append(dict(name="b", ref="source.b"))
    await activate(runtime, doc)
    trace = []
    evaluate = runtime._evaluate

    def recording(*args, **kwargs):
        answer = evaluate(*args, **kwargs)
        trace.append(copy.deepcopy(args[0].tables["contract_state_current"]["fixture.echo"]))
        return answer

    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused/websocket", "")

        async def change(a, b):
            now = runtime.clock.now_utc().isoformat()
            await adapter.event(
                dict(
                    kind="changed",
                    time_fired=now,
                    entity_id="sensor.synthetic",
                    new_state=dict(
                        state="ready",
                        attributes=dict(first=a, second=b),
                        last_updated=now,
                        last_changed=now,
                    ),
                ),
                runtime.config,
            )
            await runtime.queue.join()

        await change(False, True)
        runtime.clock.advance(1)
        monkeypatch.setattr(runtime, "_evaluate", recording)
        at = runtime.clock.now_utc()
        await change(True, False)
    assert [row["fields"]["value"]["value"] for row in trace] == ["active", "active"]
    assert [datetime.fromisoformat(row["computed_at"]) for row in trace] == [at, at]
    instance = copy.deepcopy(runtime.state.tables["node_state"]["fixture.echo"]["machine"])
    assert instance
    # b19 carries the absolute deadline from initialization; this input
    # transition does not reset it to the later start time.
    runtime.clock.advance(8)
    await runtime.submit("tick")
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] == "active"
    runtime.clock.advance(1)
    await runtime.submit("tick")
    assert runtime.snapshot()["contracts"]["fixture.echo"]["fields"]["value"]["value"] == "done"
    assert runtime.clock.now_utc() == at + timedelta(seconds=9)
