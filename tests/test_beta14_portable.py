"""Explicit contract names are organization; functional replacements stay conflicts."""

from copy import deepcopy

from core_contracts.registry import fingerprints, validate

from .conftest import activate, activate_draft
from .test_portable import plan, stage


async def test_name_only_import_has_visible_name_change_without_functional_conflict(
    runtime, config
):
    config["contracts"][0]["display_name"] = "Arbeitstag today"
    await activate(runtime, config)
    before = fingerprints(runtime.config, runtime.types)
    incoming = deepcopy(config)
    incoming["contracts"][0]["display_name"] = "Arbeitstag heute"
    preview = await plan(runtime, incoming)
    assert preview["conflicts"] == preview["unresolved"] == []
    assert preview["display_name_changes"] == [
        {"id": "fixture.echo", "before": "Arbeitstag today", "after": "Arbeitstag heute"}
    ]
    assert fingerprints(validate(preview["config"], runtime.types), runtime.types) == before
    draft = await stage(runtime, incoming, preview)
    assert runtime.config.contracts[0].display_name == "Arbeitstag today"
    await activate_draft(runtime, draft)
    assert runtime.config.contracts[0].display_name == "Arbeitstag heute"
    assert fingerprints(runtime.config, runtime.types) == before


async def test_omitted_name_preserves_existing_but_functional_edit_still_requires_a_choice(
    runtime, config
):
    config["contracts"][0]["display_name"] = "Gespeicherter Name"
    await activate(runtime, config)
    incoming = deepcopy(config)
    del incoming["contracts"][0]["display_name"]
    preview = await plan(runtime, incoming)
    assert not preview["conflicts"] and not preview["display_name_changes"]
    assert preview["config"]["contracts"][0]["display_name"] == "Gespeicherter Name"
    incoming["contracts"][0].update(display_name="Neuer Name", enabled=False)
    changed = await plan(runtime, incoming)
    assert changed["conflicts"] == changed["unresolved"] == ["contracts:fixture.echo"]
