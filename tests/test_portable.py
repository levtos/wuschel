import copy
from uuid import uuid4

import pytest

from core_contracts.portable import strict_json
from core_contracts.portable_stage_work import prepare_stage
from tests.conftest import activate, activate_draft

from .test_managed_catalog import configured, request


async def export(runtime, **kwargs):
    return await runtime.submit(
        "portable_export", {"expected_active_revision": runtime.state.active_revision, **kwargs}
    )


async def plan(runtime, package, **kwargs):
    return await runtime.submit("portable_preview", {"package": package, **kwargs})


async def stage(runtime, package, preview, **kwargs):
    return await runtime.submit(
        "portable_stage",
        {
            "package": package,
            "plan_digest": preview["plan_digest"],
            "request_id": str(uuid4()),
            **kwargs,
        },
    )


@pytest.mark.asyncio
async def test_whole_roundtrip_contains_metadata_and_catalog_not_runtime(runtime, config):
    await activate(runtime, configured(config))
    await runtime.submit("catalog_write", {"request": request(runtime)})
    await runtime.submit(
        "editorial_put",
        {
            "request": {
                "object": {"kind": "contract", "id": "fixture.echo"},
                "expected_editorial_version": 0,
                "category": "media",
                "room": {"mode": "manual", "name": "Synthetic Room"},
            },
            "_actor": {"kind": "internal", "role": "system"},
        },
    )
    package = await export(runtime)
    assert package["editorial"][0]["room"]["name"] == "Synthetic Room"
    assert len(package["catalog_contents"][0]["entries"]) == 1
    assert "epoch_id" not in str(package) and "source_observation_current" not in str(package)
    preview = await plan(runtime, package)
    assert preview["unresolved"] == []
    imported = await stage(runtime, package, preview)
    assert runtime.state.active_revision == 1
    await activate_draft(runtime, imported, expected_active_revision=1)
    assert runtime.state.active_revision == 2
    assert (
        runtime.state.tables["editorial_current"]["contract:fixture.echo"]["editorial_version"] == 1
    )


@pytest.mark.asyncio
async def test_legacy_a9_preserves_editorial_and_unknown_rejected(runtime, config):
    await activate(runtime, config)
    assert (await plan(runtime, config))["package"]["legacy"]
    assert (await plan(runtime, {"config": config, "display_name": "Synthetic"}))["package"][
        "legacy"
    ]
    with pytest.raises(ValueError, match="import_format_unknown"):
        await plan(runtime, {"format": "unknown"})


@pytest.mark.asyncio
async def test_plan_and_activation_races_leave_contents_unchanged(runtime, config):
    await activate(runtime, configured(config))
    package = await export(runtime)
    preview = await plan(runtime, package)
    await runtime.submit("catalog_write", {"request": request(runtime)})
    with pytest.raises(ValueError, match="import_plan_conflict"):
        await stage(runtime, package, preview)
    preview = await plan(runtime, package)
    imported = await stage(runtime, package, preview)
    await runtime.submit("catalog_write", {"request": request(runtime, revision=1)})
    # Identical request changes no head, so activation remains valid.
    await activate_draft(runtime, imported, expected_active_revision=1)


@pytest.mark.asyncio
async def test_staging_is_idempotent_and_activation_rejects_changed_catalog_head(runtime, config):
    from .test_managed_catalog import change

    await activate(runtime, configured(config))
    package = await export(runtime)
    preview = await plan(runtime, package)
    body = {"package": package, "plan_digest": preview["plan_digest"], "request_id": str(uuid4())}
    imported = await runtime.submit("portable_stage", body)
    assert await runtime.submit("portable_stage", body) == imported
    assert len(runtime.state.tables["registry_draft"]) == 2
    await runtime.submit("catalog_write", {"request": request(runtime, [change()])})
    before = runtime.state.model_dump()
    with pytest.raises(ValueError, match="catalog_content_conflict"):
        await activate_draft(runtime, imported, expected_active_revision=1)
    assert runtime.state.model_dump() == before


@pytest.mark.asyncio
async def test_prepared_stage_publishes_once_after_separate_read(runtime, config):
    await activate(runtime, configured(config))
    package = await export(runtime)
    preview = await plan(runtime, package)
    body = {"package": package, "plan_digest": preview["plan_digest"], "request_id": str(uuid4())}
    draft, prepared = await prepare_stage(runtime, body)
    assert prepared is not None
    assert draft["draft_id"] not in runtime.state.tables["registry_draft"]
    assert await runtime.store.get("catalog_import", "stage:" + body["request_id"]) is None
    assert await runtime.submit("portable_stage_prepared", prepared) == draft
    assert await runtime.submit("portable_stage_prepared", prepared) == draft
    assert await runtime.store.get("catalog_import", "stage:" + body["request_id"])
    assert runtime.state.active_revision == 1


@pytest.mark.asyncio
async def test_prepared_stage_commit_failure_leaves_no_draft_or_receipt(runtime, config):
    await activate(runtime, configured(config))
    package = await export(runtime)
    preview = await plan(runtime, package)
    body = {"package": package, "plan_digest": preview["plan_digest"], "request_id": str(uuid4())}
    draft, prepared = await prepare_stage(runtime, body)
    assert prepared is not None

    def stop_commit():
        raise RuntimeError("synthetic commit interrupted")

    runtime.store.before_commit = stop_commit
    try:
        with pytest.raises(RuntimeError, match="synthetic commit interrupted"):
            await runtime.submit("portable_stage_prepared", prepared)
    finally:
        runtime.store.before_commit = None
    assert draft["draft_id"] not in runtime.state.tables["registry_draft"]
    assert await runtime.store.get("catalog_import", "stage:" + body["request_id"]) is None


@pytest.mark.asyncio
async def test_prepared_stage_checks_catalog_revision_again(runtime, config):
    await activate(runtime, configured(config))
    package = await export(runtime)
    preview = await plan(runtime, package)
    body = {"package": package, "plan_digest": preview["plan_digest"], "request_id": str(uuid4())}
    draft, prepared = await prepare_stage(runtime, body)
    assert prepared is not None
    await runtime.submit("catalog_write", {"request": request(runtime)})
    with pytest.raises(ValueError, match="import_plan_conflict"):
        await runtime.submit("portable_stage_prepared", prepared)
    assert draft["draft_id"] not in runtime.state.tables["registry_draft"]
    assert await runtime.store.get("catalog_import", "stage:" + body["request_id"]) is None


@pytest.mark.asyncio
async def test_conflict_keep_and_explicit_new_id_remaps_references(runtime, config):
    await activate(runtime, config)
    incoming = copy.deepcopy(config)
    incoming["sources"][0]["physical_source_key"] = "synthetic.changed"
    preview = await plan(runtime, incoming)
    assert preview["unresolved"] == ["sources:source.a"]
    kept = await plan(runtime, incoming, resolutions={"sources:source.a": "keep"})
    assert kept["config"]["sources"][0]["physical_source_key"] == "physical.a"
    renamed = await plan(
        runtime, incoming, resolutions={"sources:source.a": {"new_id": "source.imported"}}
    )
    assert renamed["config"]["bindings"][0]["source_id"] == "source.imported"
    assert renamed["config"]["contracts"][0]["inputs"][0]["ref"] == "source.imported"


@pytest.mark.asyncio
async def test_single_contract_closure_includes_upstream_excludes_downstream(runtime, config):
    document = copy.deepcopy(config)
    first = document["contracts"][0]
    document["contracts"].extend(
        [
            {
                **first,
                "contract_id": "example.middle",
                "inputs": [{"name": "a", "ref": "fixture.echo.value"}],
            },
            {
                **first,
                "contract_id": "example.downstream",
                "inputs": [{"name": "a", "ref": "example.middle.value"}],
            },
        ]
    )
    await activate(runtime, document)
    package = await export(runtime, contract_id="example.middle")
    assert {c["contract_id"] for c in package["config"]["contracts"]} == {
        "fixture.echo",
        "example.middle",
    }
    assert (
        len(package["config"]["sources"])
        == len(package["config"]["bindings"])
        == len(package["config"]["dependencies"])
        == 1
    )
    assert package["scope"] == "contract"


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_strict_json_rejects_ambiguous_and_nonfinite(text):
    with pytest.raises(ValueError):
        strict_json(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("scope", []), ("producer", []), ("format_version", True), ("manifest", None)]
)
async def test_malformed_package_is_rejected_without_mutating_state(runtime, config, field, value):
    await activate(runtime, config)
    package = await export(runtime)
    package[field] = value
    before = runtime.state.model_dump()
    with pytest.raises(ValueError, match="import_format_unknown"):
        await plan(runtime, package)
    assert runtime.state.model_dump() == before


@pytest.mark.asyncio
async def test_single_contract_uses_exact_saved_own_draft_with_occ(runtime, config):
    await activate(runtime, config)
    package = await export(runtime, contract_id="fixture.echo")
    with pytest.raises(ValueError, match="import_target_draft_required"):
        await plan(runtime, package)
    own_config = copy.deepcopy(config)
    own_config["contracts"].append({**own_config["contracts"][0], "contract_id": "synthetic.own"})
    own = await runtime.submit("draft_create", {"config": own_config, "display_name": "Own"})
    target = {"target_draft_id": own["draft_id"], "target_draft_version": own["draft_version"]}
    preview = await plan(runtime, package, **target)
    imported = await stage(runtime, package, preview, **target)
    assert imported["draft_id"] == own["draft_id"]
    assert imported["draft_version"] == own["draft_version"] + 1
    assert {c["contract_id"] for c in imported["config"]["contracts"]} == {
        "fixture.echo",
        "synthetic.own",
    }
    assert runtime.config.contracts[0].contract_id == "fixture.echo"
    with pytest.raises(ValueError, match="draft_version_conflict"):
        await plan(runtime, package, **target)


@pytest.mark.asyncio
async def test_imported_draft_can_adjust_binding_before_activation(runtime, config):
    await activate(runtime, config)
    package = await export(runtime)
    preview = await plan(runtime, package)
    imported = await stage(runtime, package, preview)
    edited = copy.deepcopy(imported["config"])
    edited["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.synthetic_other"
    saved = await runtime.submit("draft_update", {**imported, "config": edited})
    await activate_draft(runtime, saved, expected_active_revision=runtime.state.active_revision)
    assert runtime.config.bindings[0].adapter.ha_entity_id == "sensor.synthetic_other"


@pytest.mark.asyncio
async def test_large_catalog_cross_row_master_is_one_atomic_import(runtime, config):
    from core_contracts.persistence import MemoryStore
    from core_contracts.runtime import Runtime

    from .test_managed_catalog import change

    await activate(runtime, configured(config))
    target = Runtime(MemoryStore(), runtime.clock, runtime.types)
    await target.start()
    try:
        for start in range(0, 200, 100):
            changes = [
                change(
                    f"Synthetic {i}",
                    classification=None if i else "example",
                    master_key="Synthetic 0" if i else None,
                )
                for i in range(start, start + 100)
            ]
            await runtime.submit(
                "catalog_write", {"request": request(runtime, changes, start // 100)}
            )
        package = await export(runtime)
        preview = await plan(target, package)
        imported = await stage(target, package, preview)
        assert not target.state.tables["catalog_head"]
        await activate_draft(target, imported, expected_active_revision=0)
        assert len(await target.store.rows("catalog_content_revision")) == 1
        assert len(next(iter(target.state.tables["catalog_head"].values()))["entries"]) == 200
    finally:
        await target.stop()
