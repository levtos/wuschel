"""UX-1/UX-2 and UX-5-09..12: synthetic writer, crash and migration contracts."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest

from core_contracts.authoring import AuthoringError, comparison, merge, migrate
from core_contracts.model import checksum
from core_contracts.persistence import PersistenceUnavailable
from core_contracts.registry import storage_document
from core_contracts.validation import validate_document as validate
from tests.conftest import activate, activate_draft
from tests.test_portable import export, plan, stage


async def envelope(runtime, draft, **changes):
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    return {
        "draft_id": draft["draft_id"],
        "draft_version": draft["draft_version"],
        "request_id": str(uuid4()),
        "expected_active_revision": runtime.state.active_revision,
        "comparison_digest": preview["comparison_digest"],
        "confirmations": preview["required_confirmations"],
        **changes,
    }


async def test_UX_1_01_04_atomic_completion_immutable_and_receipt(runtime, config):
    draft = await runtime.submit("draft_create", {"config": config})
    request = await envelope(runtime, draft)
    result = await runtime.submit("activate", request)
    closed = runtime.state.tables["registry_draft"][draft["draft_id"]]
    receipt = await runtime.store.get("activation_receipt", request["request_id"])
    assert closed["lifecycle"] == "completed" and closed["completed_revision"] == result["revision"]
    assert receipt["result_checksum"] == result["checksum"]
    for table in ("registry_revision", "registry_activation"):
        saved = await runtime.store.get(table, str(result["revision"]))
        assert saved["draft_id"] == draft["draft_id"]
        assert saved["draft_version"] == draft["draft_version"]
        assert saved["activation_request_id"] == request["request_id"]
    for operation in ("draft_update", "draft_delete", "activate"):
        with pytest.raises(AuthoringError, match="draft_closed"):
            await runtime.submit(
                operation,
                {**draft, **request, "request_id": str(uuid4()), "expected_active_revision": 1},
            )


async def test_UX_1_09_response_loss_restart_and_identity(runtime, config):
    draft = await runtime.submit("draft_create", {"config": config})
    request = await envelope(runtime, draft)
    result = await runtime.submit("activate", request)
    await activate(runtime, config)
    await runtime._restore("process_restart")
    assert await runtime.submit("activate", request) == result
    assert runtime.state.active_revision == 2
    with pytest.raises(AuthoringError, match="request_identity_conflict"):
        await runtime.submit("activate", {**request, "note": "different"})


async def test_UX_1_03_transaction_failure_keeps_draft_plan_and_active(runtime, config):
    await activate(runtime, config)
    package = await export(runtime)
    draft = await stage(runtime, package, await plan(runtime, package))
    request = await envelope(runtime, draft)
    before = runtime.state.model_dump()

    def fail():
        raise OSError("synthetic commit failure")

    runtime.store.before_commit = fail
    try:
        with pytest.raises(OSError):
            await runtime.submit("activate", request)
    finally:
        runtime.store.before_commit = None
    assert runtime.state.model_dump() == before
    assert (
        runtime.state.tables["registry_draft"][draft["draft_id"]]["portable"]["status"]
        == "prepared"
    )
    assert request["request_id"] not in runtime.state.tables["activation_receipt"]


async def test_UX_2_01_stale_direct_runtime_update_and_spoofed_active(runtime, config):
    await activate(runtime, config)
    stale = await runtime.submit("draft_create", {"config": config})
    request = await envelope(runtime, stale)
    await activate(runtime, config)
    with pytest.raises(AuthoringError, match="draft_base_conflict"):
        await runtime.submit("activate", {**request, "expected_active_revision": 2})
    with pytest.raises(AuthoringError, match="draft_base_conflict"):
        await runtime.submit("draft_update", {**stale, "config": config})
    saved = await runtime.submit(
        "draft_update", {**stale, "config": config, "preservation_save": True}
    )
    assert saved["base_revision"] == 1 and saved["draft_version"] == 2


@pytest.mark.parametrize(("b", "d", "c", "expected"), [(1, 2, 1, 2), (1, 1, 2, 2), (1, 2, 2, 2)])
def test_UX_2_02_03_04_trivial_merge(b, d, c, expected):
    assert merge({"value": b}, {"value": d}, {"value": c}) == ({"value": expected}, [])


def test_UX_2_05_07_identity_property_order_and_digest_bound_choice():
    b = {
        "sources": [
            {"source_id": "source.a", "one": 1, "two": 1},
            {"source_id": "source.b", "one": 1},
        ]
    }
    d = deepcopy(b)
    c = deepcopy(b)
    d["sources"][0]["one"] = 2
    c["sources"][0]["two"] = 2
    c["sources"].reverse()
    result, conflicts = merge(b, d, c)
    assert not conflicts and result["sources"][0] == {"source_id": "source.a", "one": 2, "two": 2}
    c["sources"][1]["one"] = 3
    _, conflicts = merge(b, d, c)
    conflict = conflicts[0]
    assert conflict["path"] == "/sources/source.a/one"
    result, remaining = merge(
        b, d, c, {conflict["path"]: {"digests": conflict["digests"], "choice": "draft"}}
    )
    assert not remaining and result["sources"][0]["one"] == 2
    assert merge(b, d, c, {conflict["path"]: {"digests": [], "choice": "draft"}})[1]
    assert merge([1, 2], [2, 1], [1, 3])[1]  # ordered arguments stay ordered


async def test_UX_2_09_10_continuation_persisted_one_recompare(runtime, config):
    await activate(runtime, config)
    draft = await runtime.submit("draft_create", {"config": config})
    session = await runtime.submit("continuation_start", {**draft})
    await activate(runtime, config)
    next_session = await runtime.submit("continuation_decisions", {**session, "decisions": {}})
    assert next_session["recomparisons"] == 1
    await runtime._restore("process_restart")
    await activate(runtime, config)
    stopped = await runtime.submit("continuation_finish", next_session)
    assert stopped["status"] == "halted"
    assert runtime.state.tables["registry_draft"][draft["draft_id"]]["lifecycle"] == "open"
    assert "continued_to_draft_id" not in runtime.state.tables["registry_draft"][draft["draft_id"]]


async def test_UX_2_11_session_version_and_original_occ(runtime, config):
    await activate(runtime, config)
    draft = await runtime.submit("draft_create", {"config": config})
    session = await runtime.submit("continuation_start", draft)
    await runtime.submit("draft_update", {**draft, "config": config})
    with pytest.raises(AuthoringError, match="draft_version_conflict"):
        await runtime.submit("continuation_finish", session)


async def test_UX_2_12_unknown_basis_requires_explicit_manual_comparison(runtime, config):
    draft = await runtime.submit("draft_create", {"config": config})
    runtime.state = runtime.state.clone()  # Own the deliberately unproven draft row.
    runtime.state.tables["registry_draft"][draft["draft_id"]]["base_status"] = "needs_review"
    with pytest.raises(AuthoringError, match="draft_base_unknown"):
        await runtime.submit("continuation_start", draft)
    session = await runtime.submit("continuation_start", {**draft, "manual_comparison": True})
    assert session["manual"] and session["conflicts"]


@pytest.mark.parametrize(
    ("old", "new", "shortening"),
    [
        (None, None, []),
        ({"raw_days": 30, "rollup_days": 365}, {"raw_days": 60, "rollup_days": 365}, []),
        ({"raw_days": 30, "rollup_days": 365}, {"raw_days": 10, "rollup_days": 500}, ["raw_days"]),
        (None, {"raw_days": 30, "rollup_days": 365}, ["raw_days", "rollup_days"]),
        (
            {"raw_days": 30, "rollup_days": 365},
            {"raw_days": 30, "rollup_days": 365, "batch_size": 500},
            [],
        ),
    ],
)
async def test_UX_5_09_10_effective_retention_comparison(runtime, config, old, new, shortening):
    original = {**config, **({"history_retention": old} if old else {})}
    await activate(runtime, original)
    candidate = {**config, **({"history_retention": new} if new else {})}
    draft = await runtime.submit("draft_create", {"config": candidate})
    preview = comparison(runtime, runtime.state, draft)
    assert preview["retention"]["shortening"] == shortening


async def test_UX_5_11_comparison_and_confirmation_expire(runtime, config):
    draft = await runtime.submit("draft_create", {"config": config})
    request = await envelope(runtime, draft)
    await runtime.submit("draft_update", {**draft, "display_name": "Synthetic change"})
    with pytest.raises(AuthoringError, match="comparison_expired"):
        await runtime.submit("activate", {**request, "draft_version": 2})


async def legacy_import(runtime, config):
    # Synthetic staged snapshot, activated as configuration 2 (also reused by PostgreSQL tests).
    document = deepcopy(config)
    document["sources"][0]["freshness"] = {
        "mode": "report_heartbeat",
        "interval_s": 3600,
        "future_tolerance_s": 1,
    }
    second = deepcopy(document["sources"][0])
    second.update(source_id="source.b", physical_source_key="physical.b")
    document["sources"].append(second)
    binding = deepcopy(document["bindings"][0])
    binding.update(binding_id="binding.b", source_id="source.b")
    binding["adapter"]["ha_entity_id"] = "sensor.example_2"
    document["bindings"].append(binding)
    await activate(runtime, config)
    package = await export(runtime)
    # A plain registry import still preserves its full normalized staged config.
    preview = await plan(runtime, document, resolutions={"sources:source.a": "keep"})
    # Explicitly stage an unambiguous full configuration through the real planner.
    package = document
    preview = await plan(
        runtime, package, resolutions={"sources:source.a": {"new_id": "source.imported"}}
    )
    draft = await stage(
        runtime, package, preview, resolutions={"sources:source.a": {"new_id": "source.imported"}}
    )
    await activate_draft(runtime, draft)
    old = deepcopy(runtime.state.tables["registry_draft"][draft["draft_id"]])
    for key in (
        "authoring_version",
        "lifecycle",
        "base_status",
        "base_checksum",
        "completed_revision",
        "completed_at",
        "completion_kind",
        "activation_request_id",
    ):
        old.pop(key, None)
    old["portable"].pop("status", None)
    old["config"] = deepcopy(old["config"])
    for source in old["config"]["sources"]:
        source["freshness"]["interval_s"] = 7200
    old["draft_version"] += 2
    return old


async def test_UX_1_08_complete_proofs_continue_exact_two_changes_no_replay(runtime, config):
    old = await legacy_import(runtime, config)
    state = runtime.state.clone()
    state.tables["registry_draft"][old["draft_id"]] = old
    await migrate(runtime, state, runtime.clock.now_utc())
    original = state.tables["registry_draft"][old["draft_id"]]
    assert original["lifecycle"] == "continued"
    target = state.tables["registry_draft"][original["continued_to_draft_id"]]
    assert (
        target["base_revision"] == 2 and target["lifecycle"] == "open" and "portable" not in target
    )
    expected = deepcopy(
        storage_document(validate(runtime.config.model_dump(mode="json"), runtime.types))
    )
    for source in expected["sources"]:
        source["freshness"]["interval_s"] = 7200
    assert checksum(target["config"]) == checksum(expected)
    before = deepcopy(state.tables)
    await migrate(runtime, state, runtime.clock.now_utc())
    assert state.tables == before


async def test_UX_1_08_missing_effect_proof_keeps_original_and_changes(runtime, config):
    old = await legacy_import(runtime, config)
    old["portable"]["package"]["editorial"] = [
        {
            "object": {"kind": "contract", "id": "fixture.echo"},
            "category": "media",
            "room": {"mode": "manual", "name": "Synthetic Room"},
        }
    ]
    state = runtime.state.clone()
    state.tables["registry_draft"][old["draft_id"]] = old
    await migrate(runtime, state, runtime.clock.now_utc())
    result = state.tables["registry_draft"][old["draft_id"]]
    assert result["base_status"] == "needs_review" and result["lifecycle"] == "open"
    assert result["config"] == old["config"] and "continued_to_draft_id" not in result


def test_UX_2_06_delete_against_new_reference_requires_both_object_decisions():
    base = {"sources": [{"source_id": "source.a", "value": 1}], "contracts": []}
    draft = {"sources": [], "contracts": []}
    current = {
        **base,
        "contracts": [{"contract_id": "fixture.new", "inputs": [{"name": "a", "ref": "source.a"}]}],
    }
    _, conflicts = merge(base, draft, current)
    assert {c["path"] for c in conflicts} == {"/sources/source.a", "/contracts/fixture.new"}
    decisions = {c["path"]: {"digests": c["digests"], "choice": "current"} for c in conflicts}
    candidate, unresolved = merge(base, draft, current, decisions)
    assert candidate == current and not unresolved
    decisions = {c["path"]: {"digests": c["digests"], "choice": "draft"} for c in conflicts}
    assert merge(base, draft, current, decisions) == (draft, [])


async def test_UX_1_02_consumed_plan_and_second_request_cannot_replay(runtime, config):
    await activate(runtime, config)
    package = await export(runtime)
    draft = await stage(runtime, package, await plan(runtime, package))
    request = await envelope(runtime, draft)
    first = await runtime.submit("activate", request)
    original = runtime.state.tables["registry_draft"][draft["draft_id"]]
    assert original["portable"]["status"] == "consumed"
    assert await runtime.submit("activate", request) == first
    with pytest.raises(AuthoringError, match="draft_closed"):
        await runtime.submit(
            "activate", {**request, "request_id": str(uuid4()), "expected_active_revision": 2}
        )
    assert runtime.state.active_revision == 2


async def test_UX_1_05_only_open_drafts_count_towards_limit(runtime, config):
    await activate(runtime, config)
    closed = next(iter(runtime.state.tables["registry_draft"].values()))
    for index in range(101):
        runtime.state.tables["registry_draft"][f"closed-{index}"] = {
            **closed,
            "draft_id": f"closed-{index}",
        }
    draft = await runtime.submit("draft_create", {"config": config})
    assert draft["lifecycle"] == "open"


async def test_UX_1_no_effect_completion_is_explicit_durable_and_not_a_new_revision(
    runtime, config
):
    await activate(runtime, config)
    draft = await runtime.submit("draft_create", {"config": config})
    request = await envelope(runtime, draft)
    preview = await runtime.submit("draft_preview", draft)
    assert preview["no_effect"]
    result = await runtime.submit("draft_complete", request)
    assert result["revision"] == 1 and result["completion_kind"] == "already_active"
    assert await runtime.submit("draft_complete", request) == result
    assert runtime.state.active_revision == 1


async def test_UX_1_08_bad_legacy_content_does_not_block_startup_or_discard_input(runtime, config):
    old = await legacy_import(runtime, config)
    old["config"]["sources"][0]["freshness"]["interval_s"] = "unfinished"
    state = runtime.state.clone()
    state.tables["registry_draft"][old["draft_id"]] = old
    await migrate(runtime, state, runtime.clock.now_utc())
    result = state.tables["registry_draft"][old["draft_id"]]
    assert result["base_status"] == "needs_review" and result["config"] == old["config"]


@pytest.mark.parametrize(
    "failure", [PersistenceUnavailable, asyncio.CancelledError, KeyboardInterrupt]
)
async def test_migration_propagates_persistence_failure_and_abort(
    runtime, config, monkeypatch, failure
):
    from core_contracts import authoring

    draft = await runtime.submit("draft_create", {"config": config})
    state = runtime.state.clone()
    state.tables["registry_draft"][draft["draft_id"]].pop("authoring_version")
    before = deepcopy(state.tables)

    async def fail(*args):
        raise failure("synthetic failure")

    monkeypatch.setattr(authoring, "_migrate_rows", fail)
    with pytest.raises(failure):
        await migrate(runtime, state, runtime.clock.now_utc())
    assert state.tables == before


async def test_UX_2_08_rebuild_import_plan_against_new_active_without_replaying_consumed_effects(
    runtime, config
):
    await activate(runtime, config)
    package = await export(runtime)
    draft = await stage(runtime, package, await plan(runtime, package))
    changed = deepcopy(config)
    changed["sources"][0]["display_name"] = "Synthetic new active name"
    await activate(runtime, changed)
    session = await runtime.submit("continuation_start", draft)
    assert not session["conflicts"]
    target = await runtime.submit("continuation_finish", session)
    assert target["portable"]["expected_active_revision"] == 2
    assert target["portable"]["plan_digest"] != draft["portable"]["plan_digest"]
    assert target["config"]["sources"][0]["display_name"] == "Synthetic new active name"
    await activate_draft(runtime, target)
    assert runtime.state.active_revision == 3


@pytest.mark.parametrize("choice", ["draft", "current"])
async def test_UX_2_08_new_catalog_identity_conflict_rebuilds_only_chosen_effect(
    runtime, config, choice
):
    from core_contracts.portable import payload_digest
    from tests.test_managed_catalog import change, configured, request

    await activate(runtime, configured(config))
    await runtime.submit("catalog_write", {"request": request(runtime)})
    package = await export(runtime)
    package["catalog_contents"][0]["entries"].append(
        change("Synthetic second", display_name="Incoming")
    )
    package["manifest"]["payload_digest"] = payload_digest(package)
    draft = await stage(runtime, package, await plan(runtime, package))
    await runtime.submit(
        "catalog_write",
        {
            "request": request(
                runtime, changes=[change("Synthetic second", display_name="Current")], revision=1
            )
        },
    )
    session = await runtime.submit("continuation_start", draft)
    assert len(session["conflicts"]) == 1
    row = session["conflicts"][0]
    assert row["kind"] == "catalog"
    decided = await runtime.submit(
        "continuation_decisions",
        {
            "session_id": session["session_id"],
            "session_version": session["session_version"],
            "decisions": {row["path"]: {"digests": row["digests"], "choice": choice}},
        },
    )
    assert not decided["conflicts"]
    target = await runtime.submit("continuation_finish", decided)
    entries = target["portable"]["package"]["catalog_contents"][0]["entries"]
    assert len(entries) == (1 if choice == "draft" else 0)
    await activate_draft(runtime, target)
    current = next(iter(runtime.state.tables["catalog_head"].values()))
    second = next(v for v in current["entries"].values() if v["key"] == "Synthetic second")
    assert second["display_name"] == ("Incoming" if choice == "draft" else "Current")


async def test_UX_2_08_editorial_independent_properties_keep_both_changes(runtime, config):
    from core_contracts.editorial import Room
    from core_contracts.portable import payload_digest

    await activate(runtime, config)
    actor = {"kind": "internal", "role": "system"}
    obj = {"kind": "contract", "id": "fixture.echo"}
    await runtime.submit(
        "editorial_put",
        {
            "request": {
                "object": obj,
                "expected_editorial_version": 0,
                "category": "other",
                "room": {"mode": "none"},
            },
            "_actor": actor,
        },
    )
    package = await export(runtime)
    package["editorial"][0]["category"] = "media"
    package["manifest"]["payload_digest"] = payload_digest(package)
    options = {"editorial_choices": {"contract:fixture.echo": "file"}}
    draft = await stage(runtime, package, await plan(runtime, package, **options), **options)
    await runtime.submit(
        "editorial_put",
        {
            "request": {
                "object": obj,
                "expected_editorial_version": 1,
                "category": "other",
                "room": {"mode": "manual", "name": "Synthetic Room"},
            },
            "_actor": actor,
        },
    )
    session = await runtime.submit("continuation_start", draft)
    assert not session["conflicts"]
    target = await runtime.submit("continuation_finish", session)
    await activate_draft(runtime, target)
    row = runtime.state.tables["editorial_current"]["contract:fixture.echo"]
    assert row["category"] == "media"
    assert (
        row["room"]
        == Room.model_validate({"mode": "manual", "name": "Synthetic Room"}).model_dump()
    )


async def test_creation_occ_rejects_changed_head_and_requires_http_precondition(
    runtime, config, aiohttp_client, tmp_path
):
    from tests.test_workbench_api import api_client

    await activate(runtime, config)
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.post(
        "/api/v1/registry/drafts", headers=headers, json={"config": config}
    )
    assert response.status == 400
    response = await client.post(
        "/api/v1/registry/drafts",
        headers=headers,
        json={"config": config, "expected_active_revision": 0},
    )
    assert response.status == 409
    response = await client.post(
        "/api/v1/registry/drafts",
        headers={k: v for k, v in headers.items() if k != "X-Authoring-Protocol"},
        json={"config": config, "expected_active_revision": 1},
    )
    assert (
        response.status == 409 and (await response.json())["error"] == "authoring_upgrade_required"
    )


async def test_finish_receipt_is_bound_to_command_and_original_result(runtime, config):
    await activate(runtime, config)
    draft = await runtime.submit("draft_create", {"config": config})
    session = await runtime.submit("continuation_start", draft)
    target = await runtime.submit("continuation_finish", session)
    await runtime.submit("draft_update", {**target, "display_name": "Synthetic later edit"})
    assert await runtime.submit("continuation_finish", session) == target
    with pytest.raises(AuthoringError, match="request_identity_conflict"):
        await runtime.submit("continuation_finish", {**session, "request_id": str(uuid4())})


@pytest.mark.parametrize("missing", [None, "config", "editorial", "catalog"])
async def test_migration_requires_snapshot_and_all_real_package_effects(runtime, config, missing):
    from tests.test_managed_catalog import configured, request

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
    draft = await stage(runtime, package, await plan(runtime, package))
    await activate_draft(runtime, draft)
    old = deepcopy(runtime.state.tables["registry_draft"][draft["draft_id"]])
    for key in (
        "authoring_version",
        "lifecycle",
        "base_status",
        "base_checksum",
        "completed_revision",
        "completed_at",
        "completion_kind",
        "activation_request_id",
    ):
        old.pop(key, None)
    old["portable"].pop("status", None)
    state = runtime.state.clone()
    state.tables["registry_draft"][old["draft_id"]] = old
    if missing == "config":
        old["portable"]["config_digest"] = "incorrect"
    if missing == "editorial":
        state.tables["editorial_current"].clear()
    if missing == "catalog":
        state.tables["catalog_head"].clear()
    await migrate(runtime, state, runtime.clock.now_utc())
    result = state.tables["registry_draft"][old["draft_id"]]
    assert result["lifecycle"] == ("open" if missing else "completed")
    assert result["base_status"] == ("needs_review" if missing else "known")


def test_missing_null_reserved_values_and_ordered_parameter_lists_remain_distinct():
    base = {"parameters": {"sources": [{"source_id": "a"}, {"source_id": "b"}], "value": None}}
    draft = deepcopy(base)
    current = deepcopy(base)
    draft["parameters"]["sources"].reverse()
    current["parameters"]["sources"].append({"source_id": "c"})
    assert merge(base, draft, current)[1]  # Ordered application parameters are never registry sets.
    value = {"$authoring_missing": True}
    assert merge({"value": None}, {"value": value}, {"value": None}) == ({"value": value}, [])
    _, conflicts = merge({"value": None}, {}, {"value": 1})
    assert conflicts[0]["presence"]["draft"]["present"] is False
    assert conflicts[0]["presence"]["base"]["present"] is True
    _, conflicts = merge(
        {"a/b": 0, "a": {"b": 0}}, {"a/b": 1, "a": {"b": 1}}, {"a/b": 2, "a": {"b": 2}}
    )
    assert {c["path"] for c in conflicts} == {"/a~1b", "/a/b"}
    _, invalid = merge({"sources": None}, {"sources": []}, {"sources": "invalid"})
    assert invalid  # Incomplete saved inputs produce a choice, never an internal error.


async def test_UX_2_11_same_draft_merges_saved_version_without_changing_active_basis(
    runtime, config
):
    config["sources"].append({**deepcopy(config["sources"][0]), "source_id": "source.b"})
    draft = await runtime.submit("draft_create", {"config": config})
    other = deepcopy(config)
    other["sources"][0]["freshness"]["ttl_s"] = 200
    saved = await runtime.submit(
        "draft_update", {"draft_id": draft["draft_id"], "draft_version": 1, "config": other}
    )
    local = deepcopy(config)
    local["sources"][1]["freshness"]["ttl_s"] = 300
    request = {
        "draft_id": draft["draft_id"],
        "draft_version": 1,
        "expected_server_version": 2,
        "config": local,
    }
    result = await runtime.submit("draft_reconcile", request)
    assert result["conflicts"] == []
    assert [s["freshness"]["ttl_s"] for s in result["config"]["sources"][:2]] == [200, 300]
    assert runtime.state.tables["registry_draft"][draft["draft_id"]] == saved
    assert runtime.state.active_revision == 0
    local["sources"][0]["freshness"]["ttl_s"] = 400
    conflict = await runtime.submit("draft_reconcile", request)
    row = conflict["conflicts"][0]
    request["decisions"] = {row["path"]: {"digests": row["digests"], "choice": "draft"}}
    chosen = await runtime.submit("draft_reconcile", request)
    assert chosen["conflicts"] == []
    assert chosen["config"]["sources"][0]["freshness"]["ttl_s"] == 400
    request["expected_server_version"] = 1
    with pytest.raises(AuthoringError, match="draft_version_conflict"):
        await runtime.submit("draft_reconcile", request)


async def test_cancel_is_durable_and_cannot_finish_or_reuse_deleted_draft_identity(runtime, config):
    draft = await runtime.submit(
        "draft_create", {"draft_id": "synthetic-identity", "config": config}
    )
    session = await runtime.submit(
        "continuation_start", {"draft_id": draft["draft_id"], "draft_version": 1}
    )
    cancelled = await runtime.submit(
        "continuation_cancel",
        {"session_id": session["session_id"], "session_version": session["session_version"]},
    )
    assert cancelled["status"] == "cancelled"
    assert runtime.state.tables["registry_draft"][draft["draft_id"]]["lifecycle"] == "open"
    with pytest.raises(AuthoringError, match="continuation_closed"):
        await runtime.submit(
            "continuation_finish",
            {
                "session_id": session["session_id"],
                "session_version": cancelled["session_version"],
                "request_id": session["session_id"],
            },
        )
    await runtime.submit("draft_delete", {"draft_id": draft["draft_id"], "draft_version": 1})
    with pytest.raises(ValueError, match="draft_identity_conflict"):
        await runtime.submit("draft_create", {"draft_id": draft["draft_id"], "config": config})
