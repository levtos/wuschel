"""Public manual-first authoring, diagnostics, security and cursor contracts."""

import copy
from datetime import timedelta
from uuid import uuid4

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.api import API
from core_contracts.history import HistoryQuery
from core_contracts.quality import ReasonCode
from core_contracts.registry import validate
from core_contracts.security import Tokens
from core_contracts.validation import RegistryValidationError
from dev.fake_ha import FakeHA

from .conftest import activate, activation_body
from .test_declarative import definition, machine_definition, registry
from .test_phase1a import observation


async def api_client(runtime, aiohttp_client, tmp_path):
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    client = await aiohttp_client(api.application())
    headers = {"X-Authoring-Protocol": "2", "Authorization": "Bearer " + api.tokens.values["admin"]}
    return api, client, headers


async def test_calendar_source_status_excludes_event_contents_and_requires_admin(
    runtime, config, aiohttp_client, tmp_path
):
    from .test_alpha9_calendar import calendar_config, fetch

    await activate(runtime, calendar_config(config))
    await fetch(runtime)
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.get("/api/v1/sources/calendar-status", headers=headers)
    assert response.status == 200
    rows = await response.json()
    assert len(rows) == 1
    assert set(rows[0]) == {"binding_id", "source_id", "fetched_at", "expires_at", "error_code"}
    assert rows[0]["fetched_at"] and rows[0]["expires_at"] and rows[0]["error_code"] is None
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    assert (await client.get("/api/v1/sources/calendar-status", headers=consumer)).status == 403


async def test_schema_capabilities_and_reason_catalog(runtime, aiohttp_client, tmp_path):
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.get("/api/v1/capabilities", headers=headers)
    assert response.status == 200
    data = await response.json()
    assert data["schemas"]["definition"]["properties"]["schema"]
    assert data["schemas"]["registry"]["$defs"]["Transform"]["allOf"]
    assert {item["op"] for item in data["operators"]} >= {
        "machine",
        "temporal",
        "fusion",
        "compare",
    }
    assert {item["code"] for item in data["reasons"]["codes"]} == {r.value for r in ReasonCode}
    assert data["limits"]["history_page"] == 200
    assert set(data["history"]["kinds"]) == {
        "contracts",
        "observations",
        "transitions",
        "episodes",
        "deadlines",
        "commands",
        "gaps",
        "diagnostics",
        "revisions",
        "activations",
    }
    rules = {item["code"] for item in data["schemas"]["registry"]["x-semantic-validation"]["rules"]}
    assert rules >= {
        "definition.duplicate_transition_event",
        "definition.unreachable_machine",
        "definition.invalid_map_key",
        "binding_type_mismatch",
        "source_dependency_cycle",
        "immutable_definition_version",
        "monitor_unbound",
    }
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    assert (await client.get("/api/v1/capabilities", headers=consumer)).status == 200
    assert (await client.get("/api/v1/registry/schema", headers=consumer)).status == 403


async def test_drafts_incomplete_roundtrip_occ_delete_and_audit(
    runtime, config, aiohttp_client, tmp_path
):
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.post(
        "/api/v1/registry/drafts",
        headers=headers,
        json={
            "display_name": "Manual editor",
            "config": {},
            "expected_active_revision": 0,
            "_actor": {"kind": "spoof"},
        },
    )
    draft = await response.json()
    assert response.status == 200
    assert draft["base_revision"] == 0 and draft["draft_version"] == 1
    path = "/api/v1/registry/drafts/" + draft["draft_id"]
    invalid = await client.post(path + "/validate", headers=headers, json={})
    assert invalid.status == 400
    assert len((await invalid.json())["issues"]) >= 5
    changed = await client.put(
        path,
        headers=headers,
        json={
            "draft_version": 1,
            "config": registry(config, definition()),
            "display_name": "Ready",
        },
    )
    assert changed.status == 200
    assert (await changed.json())["draft_version"] == 2
    assert (
        await client.put(path, headers=headers, json={"draft_version": 1, "config": {}})
    ).status == 409
    assert (await client.post(path + "/validate", headers=headers, json={})).status == 200
    assert (
        await client.post(
            path + "/activate",
            headers=headers,
            json=await activation_body(client, path, headers, 2),
        )
    ).status == 200
    exported = await (await client.get("/api/v1/registry/active/export", headers=headers)).json()
    assert (
        await client.post("/api/v1/registry/validate", headers=headers, json=exported)
    ).status == 200
    assert (await client.delete(path, headers=headers, json={"draft_version": 1})).status == 409
    assert (await client.delete(path, headers=headers, json={"draft_version": 2})).status == 409
    history = await (await client.get("/api/v1/history/query/diagnostics", headers=headers)).json()
    assert {row["operation"] for row in history["items"]} == {
        "draft_create",
        "draft_update",
        "activate",
    }
    assert all(row["actor"] == {"kind": "token", "role": "admin"} for row in history["items"])
    assert api.tokens.values["admin"] not in str(history)


@pytest.mark.parametrize("document", [None, [], "not a document", 12])
async def test_bad_import_forms(runtime, aiohttp_client, tmp_path, document):
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    for route in ("registry/drafts", "registry/validate"):
        response = await client.post("/api/v1/" + route, headers=headers, json=document)
        assert response.status == 400
    assert runtime.state.tables["registry_draft"] == {}


async def test_unsaved_validation_aggregates_paths_and_no_mutation(
    runtime, config, aiohttp_client, tmp_path
):
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    before = runtime.state.model_dump()
    config["sources"][0]["dependency_id"] = "missing"
    config["bindings"][0]["source_id"] = "source.missing"
    config["contracts"][0]["inputs"][0]["ref"] = "source.unknown"
    response = await client.post("/api/v1/registry/validate", headers=headers, json=config)
    assert response.status == 400
    issues = (await response.json())["issues"]
    assert {item["code"] for item in issues} >= {
        "source_dependency",
        "binding_source",
        "input_reference",
    }
    assert all(item["path"].startswith("/") and item["object_id"] for item in issues)
    assert runtime.state.model_dump() == before


async def test_semantic_diff_and_definition_immutability(runtime, config):
    original = registry(config, definition())
    await activate(runtime, original)
    changed = copy.deepcopy(original)
    changed["definitions"][0]["outputs"]["value"] = {
        "op": "not",
        "args": [{"op": "input", "name": "a"}],
    }
    with pytest.raises(RegistryValidationError, match="immutable definition"):
        await runtime.submit("validate", changed)
    changed["definitions"][0]["type_version"] = 2
    changed["contracts"][0]["type_version"] = 2
    report = await runtime.submit("validate", changed)
    assert report["diff"]["affected_contracts"] == ["fixture.echo"]
    assert report["diff"]["fingerprint_changes"] == ["fixture.echo"]
    assert report["diff"]["predicts_values"] is False
    await activate(runtime, changed)
    await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 2})
    assert runtime.state.active_revision == 3
    # A version remains immutable even when no longer active.
    changed["definitions"][0]["outputs"]["value"] = {"op": "input", "name": "a"}
    with pytest.raises(RegistryValidationError):
        await runtime.submit("validate", changed)


async def test_explicit_fingerprint_upgrade_and_dependency_impact(runtime, config):
    await activate(runtime, config)
    old = runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"]
    upgraded = copy.deepcopy(config)
    upgraded["fingerprint_version"] = 2
    report = await runtime.submit("validate", upgraded)
    assert report["diff"]["fingerprint_version"]["context_migration"] is True
    await activate(runtime, upgraded)
    assert runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"] != old
    assert runtime.state.tables["contract_lifecycle"]["fixture.echo"]["start_case"] == "valid"
    changed = copy.deepcopy(upgraded)
    changed["dependencies"][0]["kind"] = "mqtt"
    report = await runtime.submit("validate", changed)
    assert report["diff"]["directly_affected_contracts"] == ["fixture.echo"]
    assert report["diff"]["changed_source_fingerprints"] == ["source.a"]
    assert report["diff"]["source_identity_changes"] == []
    assert report["diff"]["affected_fields"] == {"fixture.echo": ["value"]}
    assert report["diff"]["objects"]["dependencies"]["field_paths"]["dep.test"] == ["/kind"]
    await runtime.submit("rollback", {"revision": 1, "expected_active_revision": 2})
    assert runtime.config.fingerprint_version == 1
    assert runtime.state.tables["node_state"]["fixture.echo"]["fingerprint"] == old


def test_declarative_requires_explicit_fingerprint_upgrade(config, runtime):
    document = registry(config, definition())
    document.pop("fingerprint_version")
    with pytest.raises(RegistryValidationError) as error:
        validate(document, runtime.types)
    assert any(issue.code == "fingerprint_upgrade_required" for issue in error.value.issues)


def test_validation_collects_structural_and_independent_semantic_errors(config, runtime):
    config["sources"][0]["freshness"] = {}
    config["contracts"][0]["inputs"][0]["ref"] = "source.missing"
    with pytest.raises(RegistryValidationError) as error:
        validate(config, runtime.types)
    assert any(issue.code.startswith("schema.") for issue in error.value.issues)
    assert any(
        issue.code == "input_reference" and issue.path == "/contracts/0/inputs/0/ref"
        for issue in error.value.issues
    )


async def test_history_cursor_evidence_filters_and_roles(runtime, config, aiohttp_client, tmp_path):
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    await activate(runtime, config)
    references = []
    for index in range(6):
        runtime.clock.advance(1)
        item = observation(runtime, bool(index % 2)).model_copy(update={"ingest_seq": index + 1})
        references.append(item.reference)
        await runtime.submit("observation", item)
    first = await (
        await client.get("/api/v1/history/query/observations?limit=2", headers=headers)
    ).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = await (
        await client.get(
            "/api/v1/history/query/observations",
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=headers,
        )
    ).json()
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    for reference in references:
        response = await client.get("/api/v1/evidence/reference/" + reference, headers=headers)
        assert response.status == 200
        assert (await response.json())["ingest_seq"] >= 0
    assert (
        await client.get(
            "/api/v1/history/query/commands",
            params={"cursor": first["next_cursor"]},
            headers=headers,
        )
    ).status == 400
    assert (
        await client.get("/api/v1/history/query/observations?limit=201", headers=headers)
    ).status == 400
    since = runtime.clock.now_utc() - timedelta(seconds=1)
    recent = await runtime.store.history("source_observation_history", HistoryQuery(since=since))
    assert len(recent["items"]) == 2
    consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
    assert (await client.get("/api/v1/history/query/observations", headers=consumer)).status == 403


async def test_machine_and_temporal_diagnostics_read_only(
    runtime, config, aiohttp_client, tmp_path
):
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    await activate(runtime, registry(config, machine_definition()))
    await runtime.submit("observation", observation(runtime, True))
    before = runtime.state.model_dump()
    result = await (
        await client.get("/api/v1/diagnostics/contracts/fixture.echo", headers=headers)
    ).json()
    assert result["machine"]["states"] == ["idle", "active", "done"]
    assert result["fingerprint_compatible"] is True
    assert result["next_due"]
    assert result["trace"]["steps"]
    assert runtime.state.model_dump() == before
    transitions = await (
        await client.get(
            "/api/v1/history/query/transitions?contract_id=fixture.echo", headers=headers
        )
    ).json()
    assert transitions["items"]
    episodes = await (
        await client.get("/api/v1/history/query/episodes?contract_id=fixture.echo", headers=headers)
    ).json()
    assert episodes["items"]
    deadlines = await (
        await client.get(
            "/api/v1/history/query/deadlines?contract_id=fixture.echo", headers=headers
        )
    ).json()
    assert deadlines["items"]
    # Old persisted rows lacked contract_id; transition ownership still makes
    # them queryable without a migration or an in-place historical rewrite.
    for table in ("sm_episode", "deadline"):
        for row in runtime.store.state.tables[table].values():
            row.pop("contract_id", None)
        page = await runtime.store.history(table, HistoryQuery(contract_id="fixture.echo"))
        assert page["items"]
        assert not (await runtime.store.history(table, HistoryQuery(contract_id="fixture.other")))[
            "items"
        ]


async def test_versioned_evidence_is_unique_and_legacy_references_remain_resolvable(
    runtime, config
):
    from core_contracts.evidence import Observation

    await activate(runtime, config)
    legacy = observation(runtime, False)
    await runtime.submit("observation", legacy)
    # Simulate Alpha-3 archived rows keyed by UUID, without new optional fields.
    record = legacy.model_dump(mode="json", exclude={"reference_version"})
    runtime.store.state.tables["source_observation_history"] = {"old-uuid": record}
    assert (await runtime.store.evidence(legacy.reference))["value_raw"] is False
    references = {legacy.reference}
    for revision, adapter in ((1, "ha_state"), (1, "mqtt"), (2, "ha_state")):
        item = Observation.model_validate(
            {**record, "reference_version": 2, "registry_revision": revision, "adapter": adapter}
        )
        assert item.reference not in references
        references.add(item.reference)
        runtime.store.state.tables["source_observation_history"][item.reference] = item.model_dump(
            mode="json"
        )
        resolved = await runtime.store.evidence(item.reference)
        assert resolved["registry_revision"] == revision and resolved["adapter"] == adapter
    assert len(references) == 4
    with pytest.raises(ValueError, match="versioned_evidence_requires_revision"):
        Observation.model_validate({**record, "reference_version": 2, "registry_revision": None})


async def test_no_active_export_time_readiness_and_rotation_audit(
    runtime, aiohttp_client, tmp_path
):
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.get("/api/v1/registry/active/export", headers=headers)
    assert response.status == 409 and (await response.json())["error"] == "no_active_registry"
    info = await (await client.get("/api/v1/info", headers=headers)).json()
    assert info["server_time"] == runtime.clock.now_utc().isoformat()
    assert runtime.service_state()["reasons"] == ["no_active_registry"]
    rotated = await (
        await client.post("/api/v1/tokens/rotate", headers=headers, json={"role": "consumer"})
    ).json()
    audit = await runtime.store.history("diagnostic_event", HistoryQuery())
    assert {item["operation"] for item in audit["items"]} == {
        "token_rotation_requested",
        "token_rotated",
    }
    assert rotated["token"] not in str(audit)
    assert rotated["status"] == "rotated" and rotated["audit_status"] == "complete"


async def test_versioned_static_cache_only(runtime, aiohttp_client, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-AbC01234.js").write_text("export default 1")
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "unversioned.js").write_text("1")
    api = API(
        runtime,
        Tokens(tmp_path / "tokens"),
        str(uuid4()),
        frontend=tmp_path,
        ingress_address="127.0.0.1",
    )
    client = await aiohttp_client(api.application(ingress=True))
    assert "immutable" in (await client.get("/assets/index-AbC01234.js")).headers["Cache-Control"]
    for path in ("/", "/unversioned.js", "/api/v1/info"):
        assert (await client.get(path)).headers["Cache-Control"] == "no-store"


def test_validation_never_reflects_secrets(config, runtime):
    config["contracts"][0]["parameters"] = {"password": "never-reflect-me"}
    with pytest.raises(RegistryValidationError) as error:
        validate(config, runtime.types)
    assert "never-reflect-me" not in str(error.value.issues)


async def test_read_only_ha_discovery_is_role_limited_cached_and_redacted(
    runtime, aiohttp_client, aiohttp_server, tmp_path
):
    fixture = FakeHA(
        {
            "sensor.test": {
                "state": "secret-value",
                "attributes": {
                    "friendly_name": "Fixture",
                    "unit_of_measurement": "W",
                    "password": "never-disclose",
                    "measurement": 12,
                    "device_class": {"password": "nested-secret"},
                },
            }
        }
    )
    ha = await aiohttp_server(fixture.application())
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    async with aiohttp.ClientSession() as session:
        BridgeAdapter(runtime, session, str(ha.make_url("/websocket")), "test-only")
        result = await (await client.get("/api/v1/ha/entities", headers=headers)).json()
        assert result["items"][0]["entity_id"] == "sensor.test"
        assert "measurement" in result["items"][0]["attributes"]
        assert "secret-value" not in str(result) and "never-disclose" not in str(result)
        assert "nested-secret" not in str(result)
        assert result["items"][0]["device_class"] is None
        assert (await client.get("/api/v1/ha/entities", headers=headers)).status == 200
        assert [request["type"] for request in fixture.requests] == ["get_states"]
        consumer = {"Authorization": "Bearer " + api.tokens.values["consumer"]}
        assert (await client.get("/api/v1/ha/entities", headers=consumer)).status == 403


async def test_audit_commit_failure_does_not_rotate_token(runtime, aiohttp_client, tmp_path):
    api, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    original = api.tokens.values["consumer"]
    runtime.store.available = False
    response = await client.post(
        "/api/v1/tokens/rotate", headers=headers, json={"role": "consumer"}
    )
    assert response.status == 503
    assert api.tokens.values["consumer"] == original
    runtime.store.available = True
    await runtime.submit("recover")
