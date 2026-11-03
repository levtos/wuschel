"""Dirty-key equivalence, nested writes and ownership at the commit boundary."""

import copy
from contextvars import ContextVar

import pytest

from core_contracts.persistence import MemoryStore, State, encoded_changes

from .conftest import activate
from .test_b23_acceptance import scale_runtime
from .test_runtime import obs


@pytest.mark.parametrize(
    "change",
    [
        lambda row: row["nested"].update(count=2),
        lambda row: row["nested"].pop("count"),
        lambda row: row["items"][0].update(count=3),
        lambda row: row["items"].append({"count": 4}),
        lambda row: row["items"].extend([{"count": 5}]),
        lambda row: row["items"].insert(0, {"count": 6}),
        lambda row: row["items"].pop(),
        lambda row: row["items"].reverse(),
        lambda row: row["items"].__setitem__(slice(None), [{"count": 7}]),
        lambda row: row["items"].__delitem__(slice(None)),
    ],
)
async def test_nested_writes_are_tracked_without_crossing_snapshot_ownership(change):
    store = MemoryStore()
    store.verify_changes = True
    state = State()
    state.tables["service_state"]["synthetic"] = {
        "nested": {"count": 1},
        "items": [{"count": 1}, {"count": 2}],
    }
    await store.commit(state)
    before = state.model_dump()
    fork = state.clone()
    change(fork.tables["service_state"]["synthetic"])
    assert state.model_dump() == before
    assert fork._changes.changed == {"service_state": {"synthetic"}}
    await store.commit(fork)
    assert (
        await store.get("service_state", "synthetic") == fork.tables["service_state"]["synthetic"]
    )
    assert state.model_dump() == before


async def test_source_writes_after_fork_and_store_writes_after_load_are_isolated():
    store = MemoryStore()
    state = State()
    state.tables["service_state"]["synthetic"] = {"nested": {"count": 1}}
    await store.commit(state)
    fork = state.clone()
    state.tables["service_state"]["synthetic"]["nested"]["count"] = 2
    assert fork.tables["service_state"]["synthetic"]["nested"]["count"] == 1
    loaded = await store.load()
    store.state.tables["service_state"]["synthetic"]["nested"]["count"] = 3
    assert loaded.tables["service_state"]["synthetic"]["nested"]["count"] == 1
    loaded.tables["service_state"]["synthetic"]["nested"]["count"] = 4
    assert (await store.get("service_state", "synthetic"))["nested"]["count"] == 3


async def test_reads_do_not_dirty_rows_and_successful_commit_detaches_local_aliases():
    store = MemoryStore()
    state = State()
    row = {"items": [{"count": 1}]}
    state.tables["service_state"]["synthetic"] = row
    row["items"][0]["count"] = 2
    await store.commit(state)
    row["items"][0]["count"] = 3
    assert state.tables["service_state"]["synthetic"]["items"][0]["count"] == 2
    fork = state.clone()
    assert list(fork.tables["service_state"].values())[0]["items"][0]["count"] == 2
    assert not any(fork._changes.changed.values())
    assert encoded_changes(store.state, fork, verify=True) == []


async def test_failed_commit_keeps_basis_and_all_dirty_mutations_for_retry():
    store = MemoryStore()
    store.verify_changes = True
    state = State()
    state.tables["service_state"]["synthetic"] = {"count": 1}
    await store.commit(state)
    fork = state.clone()
    fork.tables["service_state"]["synthetic"]["count"] = 2
    fork.tables["service_state"]["new"] = {"count": 3}
    del fork.tables["service_state"]["synthetic"]
    original = store.state.model_dump()
    store.before_commit = lambda: (_ for _ in ()).throw(OSError("synthetic rollback"))
    with pytest.raises(OSError):
        await store.commit(fork)
    assert store.state.model_dump() == original
    assert fork.generation == state.generation
    store.before_commit = None
    await store.commit(fork)
    assert await store.get("service_state", "synthetic") is None
    assert await store.get("service_state", "new") == {"count": 3}


async def test_editing_a_popped_row_cannot_cancel_its_pending_delete():
    store = MemoryStore()
    store.verify_changes = True
    state = State()
    state.tables["service_state"]["synthetic"] = {"nested": {"count": 1}}
    await store.commit(state)
    staged = state.clone()
    removed = staged.tables["service_state"].pop("synthetic")
    removed["nested"]["count"] = 2
    await store.commit(staged)
    assert await store.get("service_state", "synthetic") is None
    assert state.tables["service_state"]["synthetic"]["nested"]["count"] == 1


async def test_live_size_full_comparison_equivalence_for_observations_and_management(monkeypatch):
    from core_contracts import persistence

    store = MemoryStore()
    store.verify_changes = True
    comparisons = []
    original = persistence.full_encoded_changes

    def oracle(previous, staged):
        result = original(previous, staged)
        comparisons.append(len(result))
        return result

    monkeypatch.setattr(persistence, "full_encoded_changes", oracle)
    runtime, config = await scale_runtime(store)
    try:
        for index in range(40):
            runtime.clock.now_utc()
            await runtime.submit(
                "observation",
                obs(runtime, bool(index % 2)).model_copy(
                    update={
                        "source_id": "source.synthetic0",
                        "binding_id": "binding.synthetic0",
                        "ingest_seq": index + 1,
                    }
                ),
            )
        draft = await runtime.submit("draft_create", {"config": copy.deepcopy(config)})
        changed = copy.deepcopy(config)
        changed["contracts"][0]["display_name"] = "Synthetic P7 label"
        await runtime.submit(
            "draft_update",
            {
                "draft_id": draft["draft_id"],
                "draft_version": draft["draft_version"],
                "config": changed,
            },
        )
        await activate(runtime, changed)
        staged = runtime.state.clone()
        staged.tables["service_state"]["synthetic:p7"] = {"nested": {"values": [1, 2]}}
        await runtime._commit(staged)
        staged = runtime.state.clone()
        staged.tables["service_state"]["synthetic:p7"]["nested"]["values"].append(3)
        await runtime._commit(staged)
        staged = runtime.state.clone()
        del staged.tables["service_state"]["synthetic:p7"]
        await runtime._commit(staged)
        assert comparisons and len(comparisons) > 60
        assert store.state.tables["source_observation_history"]
        assert store.state.publications
        assert not runtime.state._changes.changed
    finally:
        await runtime.stop()


async def test_memory_commit_never_clones_the_whole_state(monkeypatch):
    store = MemoryStore()
    state = State()
    state.tables["service_state"]["synthetic"] = {"count": 1}
    in_commit = ContextVar("p7_commit", default=False)
    clone = State.clone

    def guarded_clone(self):
        assert not in_commit.get(), "State.clone in commit"
        return clone(self)

    monkeypatch.setattr(State, "clone", guarded_clone)
    token = in_commit.set(True)
    try:
        await store.commit(state)
    finally:
        in_commit.reset(token)
    assert store.last_commit_timing["clone_calls"] == 0
