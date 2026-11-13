"""UX-3 portable reference closure and schema/format compatibility."""

import copy

import pytest

from core_contracts.freshness_templates import upgrade_document
from core_contracts.portable import read_package
from core_contracts.registry import storage_document
from core_contracts.validation import validate_document as validate
from tests.conftest import activate, activate_draft
from tests.test_managed_catalog import configured
from tests.test_portable import export, plan, stage


def template(interval):
    return dict(
        template_id="freshness.test",
        display_name="Synthetic contacts",
        sensor_kind="contact",
        rule=dict(mode="report_heartbeat", interval_s=interval, future_tolerance_s=0),
    )


def shared(config, interval=10800):
    result = upgrade_document(config)
    result["freshness_templates"] = [template(interval)]
    for source in result["sources"]:
        source.update(
            sensor_kind="contact",
            sensor_kind_origin="confirmed",
            freshness_selection=dict(kind="shared", template_id="freshness.test"),
        )
    return result


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4])
async def test_schema_1_2_3_4_export_and_format_1_2_roundtrip(runtime, config, schema_version):
    config["config_schema_version"] = schema_version
    config["fingerprint_version"] = 2 if schema_version < 3 else 3
    if schema_version == 4:
        config["config_schema_version"] = 3
        config = upgrade_document(config)
    await activate(runtime, config)
    package = await export(runtime)
    assert package["format_version"] == (2 if schema_version == 4 else 1)
    assert read_package(package)["config"] == package["config"]
    preview = await plan(runtime, package)
    assert not preview["unresolved"]
    draft = await stage(runtime, package, preview)
    await activate_draft(runtime, draft)
    assert storage_document(runtime.config) == package["config"]
    package["format_version"] = 1 if schema_version == 4 else 2
    with pytest.raises(ValueError, match="import_schema_format_mismatch"):
        read_package(package)


async def test_v4_keeps_managed_catalog_and_language_4_guards(runtime, config):
    data = upgrade_document(configured(config))
    assert validate(data, runtime.types).config_schema_version == 4
    data["fingerprint_version"] = 3
    with pytest.raises(ValueError):
        validate(data, runtime.types)


async def test_format_2_browser_numeric_roundtrip_preserves_digest_and_detects_changes(
    runtime, config
):
    await activate(runtime, shared(config))
    package = await export(runtime)

    def browser_numbers(value):
        if isinstance(value, dict):
            return {key: browser_numbers(child) for key, child in value.items()}
        if isinstance(value, list):
            return [browser_numbers(child) for child in value]
        return int(value) if isinstance(value, float) and value.is_integer() else value

    downloaded = browser_numbers(copy.deepcopy(package))
    assert read_package(downloaded)["format_version"] == 2
    downloaded["config"]["freshness_templates"][0]["rule"]["interval_s"] += 1
    with pytest.raises(ValueError, match="import_digest_mismatch"):
        read_package(downloaded)


async def test_contract_export_closes_only_referenced_templates_and_liveness(runtime, config):
    document = shared(config)
    document["freshness_templates"].append({**template(100), "template_id": "freshness.unused"})
    await activate(runtime, document)
    package = await export(runtime, contract_id="fixture.echo")
    assert package["format_version"] == 2
    assert len(package["config"]["freshness_templates"]) == 1
    assert "freshness_templates:freshness.test" in package["manifest"]["object_digests"]
    assert "freshness_templates:freshness.unused" not in package["manifest"]["object_digests"]


async def test_contract_closure_includes_indirect_liveness_and_own_comparison(runtime, config):
    document = shared(config)
    document["freshness_templates"][0]["rule"]["mode"] = "liveness_source"
    document["sources"][0]["freshness_selection"]["liveness_source"] = "source.live"
    companion = copy.deepcopy(config["sources"][0])
    companion.update(
        source_id="source.live",
        physical_source_key="physical.live",
        freshness_selection=dict(
            kind="own",
            rule=companion.pop("freshness"),
            comparison_template_id="freshness.comparison",
        ),
    )
    document["sources"].append(companion)
    binding = copy.deepcopy(config["bindings"][0])
    binding.update(binding_id="binding.live", source_id="source.live")
    binding["adapter"]["ha_entity_id"] = "binary_sensor.synthetic_liveness"
    document["bindings"].append(binding)
    document["freshness_templates"].extend(
        [
            {**template(100), "template_id": "freshness.comparison"},
            {**template(200), "template_id": "freshness.unused"},
        ]
    )
    await activate(runtime, document)
    package = await export(runtime, contract_id="fixture.echo")
    checked = validate(read_package(package)["config"], runtime.types)
    assert {source.source_id for source in checked.sources} == {"source.a", "source.live"}
    assert {row.template_id for row in checked.freshness_templates} == {
        "freshness.test",
        "freshness.comparison",
    }
    assert {row.binding_id for row in checked.bindings} == {"binding.a", "binding.live"}


async def test_template_identity_remap_rewrites_shared_choice_and_preserves_target(runtime, config):
    await activate(runtime, shared(config, 14400))
    package = await export(runtime, contract_id="fixture.echo")
    target = upgrade_document({**config, "sources": [], "bindings": [], "contracts": []})
    target["freshness_templates"] = [template(10800)]
    await activate(runtime, target)
    draft = await runtime.submit("draft_create", {"config": target})
    preview = await plan(
        runtime,
        package,
        target_draft_id=draft["draft_id"],
        target_draft_version=draft["draft_version"],
        resolutions={"freshness_templates:freshness.test": {"new_id": "freshness.imported"}},
        freshness_choices={
            "source.a": {
                "kind": "shared",
                "template_id": "freshness.test",
                "sensor_kind": "contact",
            }
        },
    )
    assert not preview["unresolved"]
    checked = validate(preview["config"], runtime.types)
    assert checked.sources[0].freshness_selection.template_id == "freshness.imported"
    assert {row.template_id: row.rule.interval_s for row in checked.freshness_templates} == {
        "freshness.test": 10800,
        "freshness.imported": 14400,
    }


async def test_single_contract_keeps_target_template_or_explicit_own_rule(runtime, config):
    await activate(runtime, shared(config, 14400))
    package = await export(runtime, contract_id="fixture.echo")
    target = upgrade_document(
        dict(
            config_schema_version=3,
            fingerprint_version=3,
            sources=[],
            bindings=[],
            dependencies=[],
            contracts=[],
            catalogs=[],
        )
    )
    target["freshness_templates"] = [template(10800)]
    await activate(runtime, target)
    draft = await runtime.submit("draft_create", {"config": target})
    request = dict(
        target_draft_id=draft["draft_id"],
        target_draft_version=draft["draft_version"],
        resolutions={"freshness_templates:freshness.test": "keep"},
        freshness_choices={config["sources"][0]["source_id"]: {"kind": "own"}},
    )
    preview = await plan(runtime, package, **request)
    assert not preview["unresolved"]
    result = validate(preview["config"], runtime.types)
    assert result.freshness_templates[0].rule.interval_s == 10800
    assert result.sources[0].freshness.interval_s == 14400
    assert result.sources[0].freshness_selection.kind == "own"
    with pytest.raises(ValueError, match="import_format_unknown"):
        await plan(
            runtime,
            package,
            **{**request, "resolutions": {"freshness_templates:freshness.test": "file"}},
        )
    imported = await stage(runtime, package, preview, **request)
    await activate_draft(runtime, imported)
    assert runtime.config.freshness_templates[0].rule.interval_s == 10800


async def test_replace_import_requires_explicit_file_template_choice(runtime, config):
    await activate(runtime, shared(config, 14400))
    package = await export(runtime)
    await activate(runtime, shared(config, 10800))
    unresolved = await plan(runtime, package)
    assert "freshness_templates:freshness.test" in unresolved["unresolved"]
    preview = await plan(
        runtime, package, resolutions={"freshness_templates:freshness.test": "file"}
    )
    assert not preview["unresolved"]
    assert validate(preview["config"], runtime.types).sources[0].freshness.interval_s == 14400
    assert runtime.config.sources[0].freshness.interval_s == 10800


async def test_freshness_resolution_api_is_read_only_and_admin_only(
    runtime, config, aiohttp_client, tmp_path
):
    from tests.test_workbench_api import api_client

    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    generation = runtime.state.generation
    response = await client.post("/api/v1/registry/freshness", json=shared(config), headers=headers)
    assert response.status == 200
    body = await response.json()
    assert body["sources"][config["sources"][0]["source_id"]]["rule"]["interval_s"] == 10800
    assert runtime.state.generation == generation
    response = await client.post("/api/v1/registry/freshness", json=shared(config))
    assert response.status in {401, 403}
