"""Configuration-only replacements retain strict plan/revision guards."""

import copy

import pytest

from core_contracts.model import canonical
from core_contracts.registry import storage_document
from tests.conftest import activate, activate_draft
from tests.test_portable import export, plan, stage


async def test_file_replaces_binding_and_contract_at_same_id_without_activation(runtime, config):
    await activate(runtime, config)
    incoming = copy.deepcopy(config)
    incoming["bindings"][0]["adapter"]["ha_entity_id"] = "sensor.synthetic_changed"
    incoming["contracts"][0]["enabled"] = False
    unresolved = await plan(runtime, incoming)
    assert set(unresolved["unresolved"]) == {"bindings:binding.a", "contracts:fixture.echo"}
    choices = {key: "file" for key in unresolved["conflicts"]}
    before = canonical(storage_document(runtime.config))
    preview = await plan(runtime, incoming, resolutions=choices)
    assert not preview["unresolved"]
    assert (await plan(runtime, incoming, resolutions=choices))["plan_digest"] == preview[
        "plan_digest"
    ]
    draft = await stage(runtime, incoming, preview, resolutions=choices)
    assert draft["config"]["bindings"][0] == preview["config"]["bindings"][0]
    assert draft["config"]["bindings"][0]["binding_id"] == "binding.a"
    assert draft["config"]["bindings"][0]["adapter"]["ha_entity_id"] == "sensor.synthetic_changed"
    assert draft["config"]["contracts"][0]["contract_id"] == "fixture.echo"
    assert draft["config"]["contracts"][0]["enabled"] is False
    assert canonical(storage_document(runtime.config)) == before
    await activate_draft(runtime, draft)
    assert runtime.config.contracts[0].enabled is False


async def test_file_plan_rejects_changed_choice_and_active_revision(runtime, config):
    await activate(runtime, config)
    incoming = copy.deepcopy(config)
    incoming["contracts"][0]["enabled"] = False
    choices = {"contracts:fixture.echo": "file"}
    preview = await plan(runtime, incoming, resolutions=choices)
    with pytest.raises(ValueError, match="import_plan_conflict"):
        await stage(runtime, incoming, preview, resolutions={"contracts:fixture.echo": "keep"})
    draft = await stage(runtime, incoming, preview, resolutions=choices)
    await activate(runtime, config)
    before = runtime.state.model_dump()
    with pytest.raises(ValueError, match="import_plan_conflict"):
        await stage(runtime, incoming, preview, resolutions=choices)
    assert runtime.state.model_dump() == before
    with pytest.raises(ValueError, match="draft_base_conflict"):
        await activate_draft(runtime, draft)
    assert runtime.state.model_dump() == before


async def test_contract_package_still_rejects_file_replacement(runtime, config):
    await activate(runtime, config)
    package = await export(runtime, contract_id="fixture.echo")
    draft = await runtime.submit("draft_create", {"config": config})
    with pytest.raises(ValueError, match="import_format_unknown"):
        await plan(
            runtime,
            package,
            target_draft_id=draft["draft_id"],
            target_draft_version=1,
            resolutions={"contracts:fixture.echo": "file"},
        )


async def test_editorial_differences_ignore_versions_and_expand_room_defaults(runtime, config):
    await activate(runtime, config)
    await runtime.submit(
        "editorial_put",
        {
            "request": {
                "object": {"kind": "contract", "id": "fixture.echo"},
                "category": "other",
                "room": {"mode": "none"},
                "expected_editorial_version": 0,
            },
            "_actor": {"kind": "internal", "role": "system"},
        },
    )
    package = await export(runtime)
    assert (await plan(runtime, package))["editorial_changed"] == {"contract:fixture.echo": False}
    # Export again after metadata changes: the version is not itself an organization difference.
    await runtime.submit(
        "editorial_put",
        {
            "request": {
                "object": {"kind": "contract", "id": "fixture.echo"},
                "category": "time",
                "room": {"mode": "none"},
                "expected_editorial_version": 1,
            },
            "_actor": {"kind": "internal", "role": "system"},
        },
    )
    assert (await plan(runtime, package))["editorial_changed"] == {"contract:fixture.echo": True}
    fresh = await export(runtime)
    assert (await plan(runtime, fresh))["editorial_changed"] == {"contract:fixture.echo": False}
