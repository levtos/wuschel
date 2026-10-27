import copy
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from core_contracts.catalog_models import ClassificationSchema
from core_contracts.managed_catalog import head
from core_contracts.model import canonical
from core_contracts.persistence import PersistenceUnavailable
from core_contracts.runtime import Runtime
from core_contracts.text_rules import normalize_key

from .conftest import activate, activate_draft
from .test_managed_catalog import catalog, configured


def document(config):
    doc = configured(config)
    item = catalog()
    item["classification_schema"]["values"] = ["normal", "boost", "stumm", "leiser"]
    item["unassigned_value"] = "normal"
    doc["catalogs"] = [item]
    return doc


def row(key, **extra):
    key = "Synthetic Artist - " + key
    return dict(
        scope="synthetic",
        media_type="music",
        signal_type="title",
        key=key,
        normalized_key=normalize_key(key),
        enum=1,
        contexts=[dict(context="synthetic", source_app="synthetic")],
        **extra,
    )


def request(runtime, rows, **extra):
    return dict(
        source_text=json.dumps(dict(version=3, entries=rows)),
        expected_active_revision=runtime.state.active_revision,
        mappings=extra.pop(
            "mappings",
            {
                canonical(["synthetic", "synthetic"]): dict(
                    catalog_id="example.titles",
                    definition_version=1,
                    scope="synthetic",
                    profile="music",
                )
            },
        ),
        **extra,
    )


async def stage(runtime, body):
    preview = await runtime.submit("title_preview", body)
    assert not preview["unresolved"], preview
    return await runtime.submit(
        "title_stage",
        {
            **body,
            "plan_digest": preview["plan_digest"],
            "request_id": str(uuid4()),
        },
    )


def test_legend_complete_trimmed_and_legacy_serialization():
    original = dict(type="enum", values=["normal", "privat"])
    assert ClassificationSchema.model_validate(original).model_dump(mode="json") == original
    legend = {
        value: dict(display_name=value, description="Synthetic explanation.")
        for value in original["values"]
    }
    updated = ClassificationSchema.model_validate({**original, "legend": legend})
    assert updated.model_dump(mode="json")["legend"] == legend
    for invalid in ({}, {"normal": legend["normal"]}, {**legend, "alien": legend["normal"]}):
        with pytest.raises(ValidationError):
            ClassificationSchema.model_validate({**original, "legend": invalid})
    legend["normal"]["display_name"] = " "
    with pytest.raises(ValidationError):
        ClassificationSchema.model_validate({**original, "legend": legend})


async def test_3181_rows_one_revision_no_preview_writes_restart_idempotency(runtime, config):
    await activate(runtime, document(config))
    body = request(runtime, [row(f"Synthetic {i}") for i in range(3181)])
    before = copy.deepcopy(runtime.state.tables)
    preview = await runtime.submit("title_preview", body)
    assert preview["report"]["target_entries"] == 3181
    assert runtime.state.tables == before
    draft = await stage(runtime, body)
    assert head(runtime.state, runtime.config.catalogs[0])["revision"] == 0
    await activate_draft(runtime, draft)
    current = head(runtime.state, runtime.config.catalogs[0])
    assert current["revision"] == 1 and len(current["entries"]) == 3181
    assert len(await runtime.store.rows("catalog_content_revision")) == 1
    await runtime.stop()
    restored = Runtime(runtime.store, runtime.clock, runtime.types)
    await restored.start()
    try:
        repeated = await restored.submit(
            "title_preview", {**body, "expected_active_revision": restored.state.active_revision}
        )
        assert repeated["already_applied"] is True
        assert len(await restored.store.rows("catalog_content_revision")) == 1
    finally:
        await restored.stop()


async def test_master_class_context_override_hidden_exclusion_and_invalid_json(runtime, config):
    await activate(runtime, document(config))
    master = row("Synthetic Master")
    master["contexts"][0]["enum_override"] = 2
    variant = row("Synthetic Variant", parent_key=master["normalized_key"])
    variant["enum"] = 0
    hidden = row("Synthetic Hidden", hidden_at="2032-01-01T00:00:00Z")
    body = request(runtime, [master, variant, hidden])
    draft = await stage(runtime, body)
    await activate_draft(runtime, draft)
    entries = head(runtime.state, runtime.config.catalogs[0])["entries"].values()
    assert len(entries) == 2
    parent = next(e for e in entries if e["display_name"] == master["key"])
    assert parent["classification"] == "stumm"
    assert parent["key"] == canonical(["Synthetic Artist", "Synthetic Master"])
    child = next(e for e in entries if e["display_name"] == variant["key"])
    assert child["classification"] is None and child["master_key"] == parent["key"]
    excluded = request(
        runtime,
        [row("Synthetic Excluded")],
        mappings={canonical(["synthetic", "synthetic"]): "exclude"},
    )
    result = await runtime.submit("title_preview", excluded)
    assert result["report"]["outcomes"] == {"excluded": 1}
    assert result["report"]["target_entries"] == 0
    with pytest.raises(ValueError):
        await runtime.submit(
            "title_preview", {**excluded, "source_text": '{"version":3,"version":3,"entries":[]}'}
        )


async def test_occ_abort_commit_failure_and_transport_replay(runtime, config):
    await activate(runtime, document(config))
    body = request(runtime, [row("Synthetic First")])
    preview = await runtime.submit("title_preview", body)
    payload = {**body, "plan_digest": preview["plan_digest"], "request_id": str(uuid4())}
    draft = await runtime.submit("title_stage", payload)
    assert await runtime.submit("title_stage", payload) == draft
    with pytest.raises(ValueError, match="conflict"):
        await runtime.submit("title_stage", {**payload, "include_hidden": True})
    before = copy.deepcopy(runtime.state.tables)

    def fail():
        raise PersistenceUnavailable("synthetic_failure")

    runtime.store.before_commit = fail
    try:
        with pytest.raises(PersistenceUnavailable):
            await activate_draft(runtime, draft)
    finally:
        runtime.store.before_commit = None
    assert runtime.state.tables == before
    assert not await runtime.store.rows("catalog_content_revision")
    await runtime.submit("recover", None)
    await activate_draft(runtime, draft)
    with pytest.raises(ValueError, match="active_revision_conflict"):
        await runtime.submit("title_stage", {**payload, "request_id": str(uuid4())})


async def test_ambiguous_music_needs_explicit_parts_or_exclusion(runtime, config):
    await activate(runtime, document(config))
    ambiguous = row("Synthetic - Ambiguous")
    body = request(runtime, [ambiguous])
    plan = await runtime.submit("title_preview", body)
    assert plan["report"]["errors"][0]["reasons"] == ["artist_title_mapping_required"]
    assert plan["unresolved"]
    selected = {**body, "music_keys": {"1": ["Synthetic Artist", "Synthetic - Ambiguous"]}}
    draft = await stage(runtime, selected)
    await activate_draft(runtime, draft)
    entry = next(iter(head(runtime.state, runtime.config.catalogs[0])["entries"].values()))
    assert entry["key"] == canonical(["Synthetic Artist", "Synthetic - Ambiguous"])
    excluded = await runtime.submit(
        "title_preview",
        {
            **body,
            "expected_active_revision": runtime.state.active_revision,
            "music_keys": {"1": "exclude"},
        },
    )
    assert excluded["report"]["target_entries"] == 0 and not excluded["unresolved"]


async def test_multiple_contexts_unknown_mapping_and_hidden_master(runtime, config):
    doc = document(config)
    second = copy.deepcopy(doc["catalogs"][0])
    second["catalog_id"] = "second.titles"
    doc["catalogs"].append(second)
    await activate(runtime, doc)
    item = row("Synthetic Multi")
    item["contexts"].append(dict(context="second", source_app="synthetic", enum_override=2))
    body = request(runtime, [item])
    plan = await runtime.submit("title_preview", body)
    assert plan["report"]["error_count"] == 1 and plan["unresolved"]
    mapping = copy.deepcopy(body["mappings"])
    mapping[canonical(["second", "synthetic"])] = dict(
        catalog_id="second.titles", definition_version=1, scope="second", profile="music"
    )
    draft = await stage(runtime, {**body, "mappings": mapping})
    await activate_draft(runtime, draft)
    assert [
        next(iter(head(runtime.state, c)["entries"].values()))["classification"]
        for c in runtime.config.catalogs
    ] == ["boost", "stumm"]
    master = row("Synthetic Hidden Master", hidden_at="2032-01-01T00:00:00Z")
    variant = row("Synthetic Variant", parent_key=master["normalized_key"])
    blocked = await runtime.submit("title_preview", request(runtime, [master, variant]))
    assert any("hidden_master_required" in e["reasons"] for e in blocked["report"]["errors"])
    assert not (
        await runtime.submit(
            "title_preview", request(runtime, [master, variant], include_hidden=True)
        )
    )["unresolved"]


@pytest.mark.parametrize(
    "profile,media,signal,value,expected",
    [("game", "game", "title", 3, "grind"), ("private", "video", "app", 7, "privat")],
)
async def test_approved_legacy_class_mappings(
    runtime, config, profile, media, signal, value, expected
):
    doc = document(config)
    doc["catalogs"][0]["classification_schema"]["values"] = (
        ["heimkino", "grind", "headset"] if profile == "game" else ["normal", "privat"]
    )
    doc["catalogs"][0]["unassigned_value"] = doc["catalogs"][0]["classification_schema"]["values"][
        0
    ]
    await activate(runtime, doc)
    item = row("Synthetic Original")
    item.update(media_type=media, signal_type=signal, enum=value)
    body = request(runtime, [item])
    next(iter(body["mappings"].values()))["profile"] = profile
    draft = await stage(runtime, body)
    await activate_draft(runtime, draft)
    assert (
        next(iter(head(runtime.state, runtime.config.catalogs[0])["entries"].values()))[
            "classification"
        ]
        == expected
    )


async def test_title_api_auth_protocol_duplicates_and_stale_revision(
    runtime, config, aiohttp_client, tmp_path
):
    from .test_workbench_api import api_client

    await activate(runtime, document(config))
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    body = request(runtime, [row("Synthetic API")])
    path = "/api/v1/title-import/preview"
    assert (await client.post(path, json=body)).status == 401
    consumer = {**headers, "Authorization": "Bearer " + api.tokens.values["consumer"]}
    assert (await client.post(path, headers=consumer, json=body)).status == 403
    protocol = {k: v for k, v in headers.items() if k != "X-Authoring-Protocol"}
    assert (
        await client.post("/api/v1/title-import/stage", headers=protocol, json=body)
    ).status == 409
    assert (await client.post(path, headers=headers, json=body)).status == 200
    assert (
        await client.post(
            path,
            headers=headers,
            json={**body, "source_text": body["source_text"] + " " * 1_100_000},
        )
    ).status == 200
    assert (
        await client.post(path, headers=headers, json={**body, "expected_active_revision": 0})
    ).status == 409
    assert (
        await client.post(path, headers=headers, data='{"source_text":"x","source_text":"y"}')
    ).status == 400


async def test_legend_is_immutable_and_new_version_has_separate_contents(runtime, config):
    doc = document(config)
    await activate(runtime, doc)
    draft = await stage(runtime, request(runtime, [row("Synthetic Prior")]))
    await activate_draft(runtime, draft)
    item = copy.deepcopy(doc["catalogs"][0])
    item["classification_schema"]["legend"] = {
        v: dict(display_name=v, description="Synthetic explanation.")
        for v in item["classification_schema"]["values"]
    }
    changed = copy.deepcopy(doc)
    changed["catalogs"] = [item]
    with pytest.raises(ValueError):
        await activate(runtime, changed)
    item["version"] = 2
    changed["catalogs"] = [item]
    await activate(runtime, changed)
    assert not head(runtime.state, runtime.config.catalogs[0])["entries"]
    assert len(await runtime.store.rows("catalog_content_revision")) == 1


async def test_import_size_and_expanded_target_guards(runtime, config, monkeypatch):
    from core_contracts.portable import strict_json

    assert strict_json(" " * 20 + "{}", max_bytes=22) == {}
    with pytest.raises(ValueError, match="import_size_limit"):
        strict_json(" " * 20 + "{}", max_bytes=21)
    from core_contracts import title_transfer
    from core_contracts.title_import import MAX_ENTRIES, plan_title_import

    with pytest.raises(ValueError, match="title_export_limit"):
        plan_title_import({"version": 3, "entries": [{}] * (MAX_ENTRIES + 1)}, "synthetic")
    await activate(runtime, document(config))
    body = request(runtime, [row("Synthetic A"), row("Synthetic B")])
    monkeypatch.setattr(title_transfer, "MAX_ENTRIES", 1)
    with pytest.raises(ValueError, match="title_export_limit"):
        await runtime.submit("title_preview", body)
    monkeypatch.setattr(title_transfer, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="title_export_limit"):
        await runtime.submit("title_preview", body)
