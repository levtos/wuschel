import copy
from collections import Counter
from dataclasses import replace

from core_contracts.registry import validate
from core_contracts.runtime import Runtime

from .conftest import activate, activate_draft


async def test_history_revision_loaded_once_for_all_changed_sources(runtime, config, monkeypatch):
    for index in range(3):
        source = copy.deepcopy(config["sources"][0])
        source.update(
            source_id=f"source.extra{index}", physical_source_key=f"physical.extra{index}"
        )
        config["sources"].append(source)
    for _ in range(3):
        await activate(runtime, config)
    candidate = copy.deepcopy(config)
    for source in candidate["sources"]:
        source["display_name"] = "Synthetic changed label"
    counts = Counter()
    get = runtime.store.get

    async def counted(table, key):
        if table == "registry_revision":
            counts[key] += 1
        return await get(table, key)

    monkeypatch.setattr(runtime.store, "get", counted)
    await runtime._source_types(validate(candidate, runtime.types))
    assert len(counts) == 3
    assert set(counts.values()) == {1}


async def test_unchanged_sources_do_not_read_history(runtime, config, monkeypatch):
    await activate(runtime, config)

    async def forbidden(*args, **kwargs):
        raise AssertionError("unchanged source queried against history")

    monkeypatch.setattr(runtime.store, "source_history", forbidden)
    await runtime._source_types(runtime.config)


async def test_check_reused_by_validate_preview_and_activation_and_types_invalidate(
    runtime, config, monkeypatch
):
    await activate(runtime, config)
    calls = []
    check = Runtime._source_types

    async def counted(self, candidate):
        calls.append(candidate)
        await check(self, candidate)

    monkeypatch.setattr(Runtime, "_source_types", counted)
    draft = await runtime.submit("draft_create", {"config": config})
    await runtime.submit("validate", draft["config"])
    await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    await activate_draft(runtime, draft)
    assert len(calls) == 1
    await runtime.submit("validate", config)
    assert len(calls) == 2  # New active head invalidates the old check.
    runtime.types.register(replace(runtime.types.get("test.echo", 1), type_id="test.cache_new"))
    await runtime.submit("validate", config)
    assert len(calls) == 3
