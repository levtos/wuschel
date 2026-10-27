"""Browser numeric round-trips preserve published bytes and runtime identities."""

import copy
import json
import math

import pytest

from core_contracts.authoring import bind_plan, merge
from core_contracts.model import canonical, checksum
from core_contracts.numeric_equivalence import equivalent, preserve_config, preserve_numbers
from core_contracts.registry import diff, fingerprints, storage_document, validate
from core_contracts.validation import RegistryValidationError
from tests.conftest import activate, activate_draft
from tests.test_alpha9_sensor_origin import sensor_config
from tests.test_portable import export, plan, stage


def document():
    schema = dict(type="number", required=True, physical_state=False)
    return dict(
        config_schema_version=4,
        fingerprint_version=4,
        dependencies=[],
        sources=[],
        bindings=[],
        catalogs=[
            dict(catalog_id="synthetic.numeric.map", version=1, kind="map", entries={"a": 1.0})
        ],
        definitions=[
            dict(
                type_id="synthetic.numeric.curve",
                type_version=1,
                language_version=5,
                inputs=[],
                schema=dict(fields=dict(value=schema), available_projection=["value"]),
                outputs=dict(
                    value=dict(
                        op="annual_curve",
                        at=dict(
                            op="literal",
                            value="2030-01-01",
                            schema=dict(type="text", required=True, physical_state=False),
                        ),
                        points=[dict(day="01-01", value=840.0), dict(day="07-01", value=900.0)],
                    )
                ),
            )
        ],
        contracts=[
            dict(
                contract_id="synthetic.numeric.value",
                type_id="synthetic.numeric.curve",
                type_version=1,
                enabled=True,
                inputs=[],
                parameters={},
            )
        ],
    )


def browser(value):
    if isinstance(value, dict):
        return {key: browser(child) for key, child in value.items()}
    if isinstance(value, list):
        return [browser(child) for child in value]
    return int(value) if type(value) is float and value.is_integer() else value


def point(config):
    return config["definitions"][0]["outputs"]["value"]["points"][0]["value"]


@pytest.mark.parametrize(
    "left,right,expected",
    [
        (840, 840.0, True),
        (-840.0, -840, True),
        (0, -0.0, True),
        (0.5, 0.5, True),
        (840, 841.0, False),
        (0.5, 0.5000000001, False),
        (True, 1, False),
        (False, 0.0, False),
        ("840", 840, False),
        (None, 0, False),
        (2**53 + 1, float(2**53), False),
        (math.inf, math.inf, False),
        (math.nan, math.nan, False),
    ],
)
def test_numeric_comparison_has_no_tolerance_or_bool_text_coercion(left, right, expected):
    assert equivalent({"value": [left]}, {"value": [right]}) is expected
    assert equivalent({"value": [right]}, {"value": [left]}) is expected
    if expected and type(left) is not type(right):
        assert checksum(left) != checksum(right)


def test_preservation_matches_object_identity_and_keeps_real_edits():
    stored = dict(
        definitions=[
            dict(type_id="a", type_version=1, value=840.0),
            dict(type_id="b", type_version=1, value=42.0),
        ]
    )
    incoming = dict(
        definitions=[
            dict(type_id="b", type_version=1, value=42),
            dict(type_id="a", type_version=1, value=841),
        ]
    )
    restored = preserve_config(incoming, stored)
    assert type(restored["definitions"][0]["value"]) is float
    assert restored["definitions"][1]["value"] == 841
    assert canonical(incoming) == json.dumps(incoming, sort_keys=True, separators=(",", ":"))
    assert preserve_numbers({"a": True, "b": "1"}, {"a": 1.0, "b": 1}) == {"a": True, "b": "1"}


async def test_browser_validate_save_activate_keeps_published_bytes_and_fingerprints(runtime):
    await activate(runtime, document())
    stored = storage_document(runtime.config)
    original_definition = canonical(await runtime.store.definition("synthetic.numeric.curve", 1))
    before = fingerprints(runtime.config, runtime.types)
    incoming = browser(stored)
    assert type(point(incoming)) is int
    assert checksum(incoming) != checksum(stored)
    verdict = await runtime.submit("validate", incoming)
    assert verdict["valid"] and verdict["issues"] == []
    assert verdict["diff"]["fingerprint_changes"] == []
    assert all(not group["changed"] for group in verdict["diff"]["objects"].values())
    direct_diff = diff(runtime.config, validate(incoming, runtime.types), runtime.types)
    assert direct_diff["fingerprint_changes"] == []
    draft = await runtime.submit("draft_create", {"config": incoming})
    assert canonical(draft["config"]) == canonical(stored)
    assert type(point(draft["config"])) is float
    saved = await runtime.submit(
        "draft_update",
        {
            "draft_id": draft["draft_id"],
            "draft_version": draft["draft_version"],
            "config": browser(draft["config"]),
        },
    )
    assert canonical(saved["config"]) == canonical(stored)
    await activate_draft(runtime, saved)
    assert canonical(storage_document(runtime.config)) == canonical(stored)
    assert fingerprints(runtime.config, runtime.types) == before
    assert (
        canonical(await runtime.store.definition("synthetic.numeric.curve", 1))
        == original_definition
    )


@pytest.mark.parametrize("value", [841, True, "840"])
async def test_actual_value_or_type_changes_remain_errors(runtime, value):
    await activate(runtime, document())
    incoming = browser(storage_document(runtime.config))
    incoming["definitions"][0]["outputs"]["value"]["points"][0]["value"] = value
    with pytest.raises(RegistryValidationError) as failure:
        await runtime.submit("validate", incoming)
    if value == 841:
        assert any(issue.code == "immutable_definition_version" for issue in failure.value.issues)


@pytest.mark.parametrize("value", [True, "1", 2])
async def test_catalog_bool_text_and_real_number_edits_are_immutable(runtime, value):
    await activate(runtime, document())
    incoming = browser(storage_document(runtime.config))
    incoming["catalogs"][0]["entries"]["a"] = value
    with pytest.raises(RegistryValidationError) as failure:
        await runtime.submit("validate", incoming)
    assert any(issue.code == "immutable_definition_version" for issue in failure.value.issues)


async def test_import_reuses_numeric_equivalents_and_old_import_draft_needs_no_reimport(runtime):
    await activate(runtime, document())
    stored = storage_document(runtime.config)
    package = browser(await export(runtime))
    preview = await plan(runtime, package)
    assert preview["conflicts"] == preview["unresolved"] == []
    assert canonical(preview["config"]) == canonical(stored)
    draft = await stage(runtime, package, preview)
    assert type(point(draft["config"])) is float
    # Simulate a browser save performed by b12; keep its original strict plan binding.
    old = runtime.state.tables["registry_draft"][draft["draft_id"]]
    old["config"] = browser(copy.deepcopy(old["config"]))
    bind_plan(old)
    state_before = canonical(runtime.state.model_dump(mode="json"))
    check = await runtime.submit("validate", old["config"])
    assert check["issues"] == []
    await runtime.submit("draft_preview", {"draft_id": old["draft_id"]})
    assert canonical(runtime.state.model_dump(mode="json")) == state_before
    await activate_draft(runtime, copy.deepcopy(old))
    assert canonical(storage_document(runtime.config)) == canonical(stored)


async def test_inactive_published_definition_uses_its_historical_numeric_form(runtime):
    await activate(runtime, document())
    original = storage_document(runtime.config)
    empty = {**original, "definitions": [], "contracts": [], "catalogs": []}
    await activate(runtime, empty)
    draft = await runtime.submit("draft_create", {"config": browser(original)})
    assert type(point(draft["config"])) is float
    await activate_draft(runtime, draft)
    assert canonical(storage_document(runtime.config)) == canonical(original)


async def test_sensor_profile_roundtrip_keeps_identity_and_invalid_draft_stays_invalid(
    runtime, config
):
    await activate(runtime, sensor_config(config))
    original = storage_document(runtime.config)
    before = fingerprints(runtime.config, runtime.types)
    incoming = browser(original)
    assert (await runtime.submit("validate", incoming))["issues"] == []
    saved = await runtime.submit("draft_create", {"config": incoming})
    assert canonical(saved["config"]) == canonical(original)
    assert fingerprints(validate(saved["config"], runtime.types), runtime.types) == before
    incoming["sources"][0]["sensor_profile"]["stuck"]["min_reports"] = True
    invalid = await runtime.submit("draft_create", {"config": incoming})
    assert invalid["config"]["sources"][0]["sensor_profile"]["stuck"]["min_reports"] is True
    with pytest.raises(RegistryValidationError):
        await runtime.submit("validate", invalid["config"])


def test_three_way_numeric_roundtrip_retains_stored_form_and_type_changes_conflict():
    result, conflicts = merge({"value": 840.0}, {"value": 840}, {"value": 840.0}, {})
    assert conflicts == [] and type(result["value"]) is float
    _, conflicts = merge({"value": None}, {"value": True}, {"value": 1}, {})
    assert len(conflicts) == 1
