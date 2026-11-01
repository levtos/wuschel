import copy
from uuid import uuid4

import pytest

from core_contracts.catalog_models import ManagedCatalog
from core_contracts.managed_catalog import head, identity
from core_contracts.registry import RegistryConfig, storage_document
from tests.conftest import activate


def catalog():
    return {
        "catalog_id": "example.titles",
        "kind": "managed",
        "version": 1,
        "identity_rule": {
            "version": 1,
            "fields": ["scope", "media_type", "signal_type", "normalized_key"],
            "normalizer": "normalize_key",
        },
        "classification_schema": {"type": "enum", "values": ["other", "example"]},
        "unassigned_value": "other",
        "master_depth": 1,
    }


def configured(config):
    config = copy.deepcopy(config)
    config.update(config_schema_version=3, fingerprint_version=3, catalogs=[catalog()])
    return config


def change(key="Synthetic First", **extra):
    return {
        "scope": "example",
        "media_type": "music",
        "signal_type": "title",
        "key": key,
        "classification": "example",
        **extra,
    }


def request(runtime, changes=None, revision=0, request_id=None):
    return {
        "catalog_id": "example.titles",
        "definition_version": 1,
        "expected_active_revision": runtime.state.active_revision,
        "expected_content_revision": revision,
        "request_id": request_id or str(uuid4()),
        "changes": changes or [change()],
        "reason": "Synthetic test",
    }


@pytest.mark.asyncio
async def test_atomic_idempotent_occ_and_restart(runtime, config):
    await activate(runtime, configured(config))
    body = request(runtime)
    result = await runtime.submit("catalog_write", {"request": body})
    assert result["content_revision"] == 1
    assert result["changed"] == 1
    assert result["publication_seq"] == runtime.state.publication_seq
    assert await runtime.submit("catalog_write", {"request": body}) == result
    runtime.state = await runtime.store.load()
    assert await runtime.submit("catalog_write", {"request": body}) == result
    with pytest.raises(ValueError, match="catalog_request_conflict"):
        await runtime.submit("catalog_write", {"request": {**body, "reason": "Different"}})
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await runtime.submit("catalog_write", {"request": request(runtime)})
    assert len(await runtime.store.rows("catalog_content_revision")) == 1


@pytest.mark.asyncio
async def test_impact_preview_and_history_check_do_not_publish_or_write(runtime, config):
    from core_contracts.managed_catalog import verify_stored_heads

    await activate(runtime, configured(config))
    before = runtime.state.model_dump()
    persisted = runtime.store.state.model_dump()
    impact = await runtime.submit("catalog_preview", {"request": request(runtime)})
    assert impact == {"affected_keys": 1, "affected_contracts": 0}
    assert runtime.state.model_dump() == before
    assert runtime.store.state.model_dump() == persisted
    await runtime.submit("catalog_write", {"request": request(runtime)})
    await verify_stored_heads(runtime.store, runtime.state, runtime.config)
    damaged = runtime.state.clone()
    next(iter(damaged.tables["catalog_head"].values()))["entries"] = {}
    with pytest.raises(ValueError, match="catalog_restore_incompatible"):
        await verify_stored_heads(runtime.store, damaged, runtime.config)


@pytest.mark.asyncio
async def test_full_catalog_history_exceeds_generic_page_and_filters_other_catalogs(
    runtime, config
):
    from core_contracts.managed_catalog import verify_stored_heads

    await activate(runtime, configured(config))
    for start in range(0, 1100, 100):
        await runtime.submit(
            "catalog_write",
            {
                "request": request(
                    runtime,
                    [change(f"Synthetic {i}") for i in range(start, start + 100)],
                    start // 100,
                )
            },
        )
    await verify_stored_heads(runtime.store, runtime.state, runtime.config)
    current = next(iter(runtime.state.tables["catalog_head"]))
    assert len(await runtime.store.managed_entries(current, 11)) == 1100
    assert len(await runtime.store.managed_entries(current, 1)) == 100
    # Pure history storage fixture: independent catalog rows must not consume pages.
    rows = runtime.store.state.tables["catalog_content_revision"]
    for revision in range(12, 132):
        rows[f"{current}:{revision}"] = {"catalog": current, "revision": revision}
        rows[f"other:{revision}"] = {"catalog": "other", "revision": revision}
    first = await runtime.store.catalog_revisions(current, 132)
    second = await runtime.store.catalog_revisions(current, first[-1]["revision"])
    assert [row["revision"] for row in first] == list(range(131, 31, -1))
    assert [row["revision"] for row in second] == list(range(31, 0, -1))


@pytest.mark.asyncio
async def test_master_one_level_and_affected_variants(runtime, config):
    await activate(runtime, configured(config))
    await runtime.submit(
        "catalog_write",
        {
            "request": request(
                runtime,
                [
                    change(),
                    change("Synthetic Variant", classification=None, master_key="Synthetic First"),
                ],
            )
        },
    )
    result = await runtime.submit(
        "catalog_write", {"request": request(runtime, [change(classification="other")], 1)}
    )
    assert len(result["affected_keys"]) == 2
    before = runtime.state.model_dump()
    with pytest.raises(ValueError, match="catalog_master_invalid"):
        await runtime.submit(
            "catalog_write",
            {
                "request": request(
                    runtime,
                    [
                        change(
                            "Synthetic Child", classification=None, master_key="Synthetic Variant"
                        )
                    ],
                    2,
                )
            },
        )
    assert runtime.state.model_dump() == before
    with pytest.raises(ValueError, match="catalog_master_invalid"):
        await runtime.submit(
            "catalog_write", {"request": request(runtime, [change(tombstone=True)], 2)}
        )


@pytest.mark.asyncio
async def test_display_noop_rollback_retains_content(runtime, config):
    await activate(runtime, configured(config))
    await runtime.submit("catalog_write", {"request": request(runtime)})
    seq = runtime.state.publication_seq
    result = await runtime.submit(
        "catalog_write", {"request": request(runtime, [change(display_name="Display")], 1)}
    )
    assert result["affected_keys"] == [] and result["content_revision"] == 2
    assert runtime.state.publication_seq == seq
    result = await runtime.submit(
        "catalog_write", {"request": request(runtime, [change(display_name="Display")], 2)}
    )
    assert result["content_revision"] == 2 and result["changed"] == 0
    await activate(runtime, configured(config))
    cat = ManagedCatalog.model_validate(catalog())
    assert head(runtime.state, cat)["revision"] == 2


def test_version_gate_and_legacy_serialization(config):
    legacy = storage_document(RegistryConfig.model_validate(config))
    assert "fingerprint_version" not in legacy
    with pytest.raises(ValueError, match="registry_schema_3_required"):
        RegistryConfig.model_validate({**config, "catalogs": [catalog()]})


@pytest.mark.asyncio
async def test_definition_immutable(runtime, config):
    document = configured(config)
    await activate(runtime, document)
    document["catalogs"][0]["unassigned_value"] = "example"
    with pytest.raises(ValueError):
        await activate(runtime, document)


@pytest.mark.asyncio
async def test_failed_commit_preserves_head_results_and_history(runtime, config):
    await activate(runtime, configured(config))
    before = runtime.state.model_dump()
    persisted = runtime.store.state.model_dump()

    def fail():
        raise RuntimeError("synthetic commit failure")

    runtime.store.before_commit = fail
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await runtime.submit("catalog_write", {"request": request(runtime)})
    runtime.store.before_commit = None
    assert runtime.state.model_dump() == before
    assert runtime.store.state.model_dump() == persisted


@pytest.mark.asyncio
async def test_lg_pattern_uses_only_its_declared_default(runtime, config):
    document = configured(config)
    document["catalogs"][0]["catalog_id"] = "example.sources"
    document["catalogs"][0]["classification_schema"]["values"] = [
        "manual_active",
        "automatic_active",
    ]
    document["catalogs"][0]["unassigned_value"] = "manual_active"
    await activate(runtime, document)
    from core_contracts.managed_catalog import definition, effective

    current = definition(runtime.config, "example.sources", 1)
    assert effective({}, identity("device", "source", "source", "Synthetic HDMI"), current) == (
        "manual_active",
        False,
        None,
    )
    assert runtime.state.tables["catalog_content_revision"] == {}


def test_key_identity_unicode_scope_and_normalization():
    assert identity("scope", "music", "title", "Example_-One") == identity(
        "scope", "music", "title", "example one"
    )
    assert identity("other", "music", "title", "example one") != identity(
        "scope", "music", "title", "example one"
    )
    assert identity("scope", "music", "title", "é") != identity("scope", "music", "title", "e")


@pytest.mark.asyncio
async def test_equivalent_master_link_changes_no_semantic_basis(runtime, config):
    await activate(runtime, configured(config))
    await runtime.submit(
        "catalog_write",
        {
            "request": request(
                runtime,
                [
                    change("Synthetic Master A"),
                    change("Synthetic Master B"),
                    change(
                        "Synthetic Variant", classification=None, master_key="Synthetic Master A"
                    ),
                ],
            )
        },
    )
    result = await runtime.submit(
        "catalog_write",
        {
            "request": request(
                runtime,
                [change("Synthetic Variant", classification=None, master_key="Synthetic Master B")],
                1,
            )
        },
    )
    assert result["content_revision"] == 2
    assert result["affected_keys"] == []


@pytest.mark.asyncio
async def test_catalog_change_cannot_start_machine_episode_from_old_evidence(runtime, config):
    from core_contracts.managed_catalog import invalidate
    from core_contracts.producer import Context, state_machine
    from core_contracts.registry import Contract

    from .test_alpha9_deadlines import field, machine

    configured_contract = Contract.model_validate(config["contracts"][0])
    state = runtime.state.clone()
    engine = machine()
    old = engine.initial(runtime.clock.now_utc(), 1, "synthetic-fingerprint")
    root = {
        "fingerprint": "synthetic-fingerprint",
        "machine": old.model_dump(mode="json"),
        "catalog_dependencies": {"catalog:synthetic:example": "old"},
    }
    state.tables["node_state"][configured_contract.contract_id] = root
    invalidate(state, {"catalog": "synthetic", "affected_keys": ["example"]}, {})
    ctx = Context(
        runtime.clock,
        {},
        configured_contract,
        {"edge": field(False)},
        root,
        state,
        "revision",
        "valid",
        None,
    )
    result = state_machine(ctx, engine, reinitialize=lambda ctx: True)
    assert result.status == "unknown"
    assert root["catalog_machine_pending"]["episode_id"] == old.episode_id
    assert "machine" not in root
    ctx.origin = "timer"
    assert state_machine(ctx, engine, reinitialize=lambda ctx: True).status == "unknown"
    ctx.origin = "input"
    assert state_machine(ctx, engine, reinitialize=lambda ctx: True).status == "valid"
    assert root["machine"]["episode_id"] != old.episode_id


@pytest.mark.asyncio
async def test_sightings_count_playbacks_and_assignment_preserves_measurement_time(runtime, config):
    from core_contracts.evidence import Observation

    from .test_declarative import schema

    document = configured(config)
    document["sources"][0]["source_type"] = "text"
    document["sources"][0]["value_schema"] = schema("text")
    document["bindings"][0]["transform"] = {"kind": "type_cast", "target": "text"}
    lookup = lookup_rule("unused")
    lookup["key"] = {"op": "input", "name": "a"}
    document["definitions"] = [
        {
            "type_id": "example.classify",
            "type_version": 1,
            "language_version": 4,
            "inputs": [{"name": "a", "schema": schema("text")}],
            "schema": {
                "fields": {"value": schema("enum", value_catalog=["other", "example"])},
                "available_projection": ["value"],
            },
            "outputs": {"value": lookup},
        }
    ]
    document["contracts"][0].update(type_id="example.classify")
    await activate(runtime, document)
    await runtime.submit("bridge_state", ("connected", "connected"))
    runtime.first_snapshot = True
    now = runtime.clock.now_utc()
    observation = Observation(
        source_id="source.a",
        binding_id="binding.a",
        adapter="ha_state",
        value_raw="Synthetic First",
        observation_kind="live_change",
        ha_time_fired=now,
        received_at=now,
        epoch_id=runtime.state.epoch_id,
        ingest_seq=1,
        reference_version=2,
        registry_revision=runtime.state.active_revision,
    )
    await runtime.submit("observation", observation)
    assert runtime.state.tables["catalog_sighting"] == {}
    runtime.clock.advance(2)
    await runtime.submit("catalog_tick")
    assert sum(row["seen_count"] for row in runtime.state.tables["catalog_sighting"].values()) == 1
    for index, extra in enumerate(
        (
            {"mqtt_retained": True},
            {"observation_kind": "snapshot"},
            {"observation_kind": "restore"},
        ),
        2,
    ):
        await runtime.submit(
            "observation", observation.model_copy(update={"ingest_seq": index, **extra})
        )
    assert sum(row["seen_count"] for row in runtime.state.tables["catalog_sighting"].values()) == 1
    await runtime.submit(
        "observation",
        observation.model_copy(
            update={"ingest_seq": 5, "observation_kind": "report", "ha_last_reported": now}
        ),
    )
    assert sum(row["seen_count"] for row in runtime.state.tables["catalog_sighting"].values()) == 1
    runtime.clock.advance(1)
    await runtime.submit(
        "observation",
        observation.model_copy(
            update={
                "ingest_seq": 6,
                "observation_kind": "report",
                "ha_last_reported": runtime.clock.now_utc(),
                "received_at": runtime.clock.now_utc(),
            }
        ),
    )
    assert sum(row["seen_count"] for row in runtime.state.tables["catalog_sighting"].values()) == 1
    previous = runtime.state.tables["contract_state_current"]["fixture.echo"]["fields"]["value"]
    assert previous["value"] == "other" and previous["status"] == "valid"
    runtime.clock.advance(10)
    await runtime.submit("catalog_write", {"request": request(runtime)})
    current = runtime.state.tables["contract_state_current"]["fixture.echo"]["fields"]["value"]
    assert current["value"] == "example" and current["measured_at"] == previous["measured_at"]
    assert sum(row["seen_count"] for row in runtime.state.tables["catalog_sighting"].values()) == 1


def lookup_rule(key):
    from .test_declarative import literal, schema

    return {
        "op": "managed_catalog_lookup",
        "catalog_id": "example.titles",
        "definition_version": 1,
        "scope": literal("example", "text"),
        "media_type": literal("music", "text"),
        "signal_type": literal("title", "text"),
        "key": literal(key, "text"),
        "schema": schema("enum", value_catalog=["other", "example"]),
    }


@pytest.mark.asyncio
async def test_history_restore_is_new_revision_and_keeps_original(runtime, config):
    await activate(runtime, configured(config))
    await runtime.submit("catalog_write", {"request": request(runtime)})
    await runtime.submit(
        "catalog_write", {"request": request(runtime, [change(classification="other")], 1)}
    )
    original = copy.deepcopy(await runtime.store.rows("catalog_entry_revision"))
    body = {k: v for k, v in request(runtime, revision=2).items() if k != "changes"}
    body["restore_revision"] = 1
    result = await runtime.submit("catalog_write", {"request": body})
    assert result["content_revision"] == 3
    assert await runtime.submit("catalog_write", {"request": body}) == result
    past = await runtime.submit(
        "catalog_revision", {"catalog_id": "example.titles", "definition_version": 1, "revision": 2}
    )
    assert past["items"][0]["classification"] == "other"
    for row in original:
        assert row in await runtime.store.rows("catalog_entry_revision")


@pytest.mark.asyncio
async def test_independent_lookup_output_does_not_reset_machine_in_same_contract(runtime, config):
    from .test_declarative import machine_definition, registry, schema

    data = machine_definition()
    data["language_version"] = 4
    data["outputs"]["classification"] = lookup_rule("Synthetic First")
    data["schema"]["fields"]["classification"] = schema("enum", value_catalog=["other", "example"])
    await activate(runtime, configured(registry(config, data)))
    before = copy.deepcopy(runtime.state.tables["node_state"]["fixture.echo"]["machine"])
    await runtime.submit("catalog_write", {"request": request(runtime)})
    root = runtime.state.tables["node_state"]["fixture.echo"]
    assert root["machine"] == before
    assert "catalog_machine_pending" not in root
    assert (
        runtime.state.tables["contract_state_current"]["fixture.echo"]["fields"]["classification"][
            "value"
        ]
        == "example"
    )


@pytest.mark.asyncio
async def test_command_guard_reads_current_catalog_and_replays(runtime, config):
    from datetime import timedelta

    from .test_declarative import literal, machine_definition, registry, schema

    data = machine_definition()
    data["language_version"] = 4
    data["machine"]["transitions"][0]["guard"] = {
        "op": "compare",
        "comparison": "eq",
        "left": lookup_rule("Synthetic First"),
        "right": {
            **literal("example", "enum"),
            "schema": schema("enum", value_catalog=["other", "example"]),
        },
    }
    await activate(runtime, configured(registry(config, data)))
    command = dict(
        command_id=str(uuid4()),
        contract_id="fixture.echo",
        command="start",
        args={"confirm": True},
        origin=dict(kind="test", actor="test", client_name="test"),
        issued_at=runtime.clock.now_utc(),
        valid_until=runtime.clock.now_utc() + timedelta(seconds=30),
    )
    before = copy.deepcopy(runtime.state.tables["node_state"]["fixture.echo"])
    rejected = await runtime.submit("command", command)
    assert rejected["reason"] == "guard_rejected"
    assert runtime.state.tables["node_state"]["fixture.echo"] == before
    await runtime.submit("catalog_write", {"request": request(runtime)})
    accepted = await runtime.submit("command", {**command, "command_id": str(uuid4())})
    assert accepted["status"] == "accepted"
    node = runtime.state.tables["node_state"]["fixture.echo"]
    assert node["machine"]["state"] == "active"
    assert node["catalog_machine"]["catalog_dependencies"]
    assert await runtime.submit("command", command) == rejected
    episode = node["machine"]["episode_id"]
    await runtime.submit(
        "catalog_write", {"request": request(runtime, [change(classification="other")], 1)}
    )
    node = runtime.state.tables["node_state"]["fixture.echo"]
    assert node["catalog_machine_pending"]["episode_id"] == episode
    assert "machine" not in node


@pytest.mark.asyncio
async def test_negative_lookup_and_only_changed_temporal_slot(runtime, config):
    from .test_declarative import literal, schema

    document = configured(config)
    document.update(dependencies=[], sources=[], bindings=[], contracts=[], definitions=[])
    for index, key in enumerate(("Synthetic First", "Synthetic Second")):
        lookup = lookup_rule(key)
        rule = {
            "op": "temporal",
            "id": "timer",
            "operation": "stable_for",
            "duration_s": 60,
            "accepts_held": False,
            "args": [
                {
                    "op": "compare",
                    "comparison": "eq",
                    "left": lookup,
                    "right": {
                        **literal("other", "enum"),
                        "schema": schema("enum", value_catalog=["other", "example"]),
                    },
                }
            ],
        }
        document["definitions"].append(
            {
                "type_id": f"example.lookup{index}",
                "type_version": 1,
                "language_version": 4,
                "inputs": [],
                "schema": {"fields": {"value": schema()}, "available_projection": ["value"]},
                "outputs": {"value": rule},
            }
        )
        document["contracts"].append(
            {
                "contract_id": f"example.lookup{index}",
                "type_id": f"example.lookup{index}",
                "type_version": 1,
                "enabled": True,
                "inputs": [],
                "parameters": {},
            }
        )
    await activate(runtime, document)
    first = runtime.state.tables["contract_state_current"]["example.lookup0"]["fields"]["value"]
    assert first["status"] == "valid" and first["quality"] == "healthy"
    assert first["configuration"]["hints"][0]["code"] == "catalog_assignment_missing"
    other_before = copy.deepcopy(
        runtime.state.tables["node_state"]["example.lookup1"]["rules"]["timer"]
    )
    runtime.clock.advance(10)
    await runtime.submit("catalog_write", {"request": request(runtime)})
    assert runtime.state.tables["node_state"]["example.lookup1"]["rules"]["timer"] == other_before
    current = runtime.state.tables["contract_state_current"]["example.lookup0"]["fields"]["value"]
    assert current["status"] == "valid" and current["value"] is False
    assert "configuration" not in current
