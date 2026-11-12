from datetime import UTC, datetime, timedelta
from uuid import uuid4

import aiohttp
import pytest

from core_contracts.adapters.ha import BridgeAdapter
from core_contracts.api import API
from core_contracts.declarative import Evaluation, check_definition
from core_contracts.declarative_models import DeclarativeDefinition
from core_contracts.evidence import Observation
from core_contracts.ha_context import Attribution, ContextIndex, HAContext
from core_contracts.quality import FieldValue
from core_contracts.security import Tokens
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_declarative import definition

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def test_parent_user_missing_cycles_expiry_and_epoch_never_invent_manual_origin():
    index = ContextIndex()
    index.set_epoch("ha-1")
    child = HAContext(id="child", user_id="technical-account", parent_id="apply")
    assert index.attribution(child, NOW).kind == "unknown"
    assert index.attribution(HAContext(user_id="person"), NOW).kind == "unknown"
    index.register(HAContext(id="apply"), NOW)
    assert index.attribution(child, NOW).kind == "known_automatic"
    assert index.attribution(child, NOW).matched_context_id == "apply"
    assert index.attribution(child, NOW + timedelta(seconds=600)).kind == "unknown"
    index.observe(HAContext(id="a", parent_id="b"), NOW)
    index.observe(HAContext(id="b", parent_id="a"), NOW)
    assert index.attribution(HAContext(id="a", parent_id="b"), NOW).kind == "unknown"
    index.register(HAContext(id="apply"), NOW)
    index.set_epoch("ha-2")
    assert index.attribution(child, NOW).kind == "unknown"


def test_bounded_context_cache_and_parent_depth():
    index = ContextIndex()
    index.set_epoch("ha")
    index.register(HAContext(id="root"), NOW)
    for number in range(1, 10):
        index.observe(
            HAContext(id=f"ctx-{number}", parent_id="root" if number == 1 else f"ctx-{number - 1}"),
            NOW,
        )
    assert (
        index.attribution(HAContext(id="ctx-7", parent_id="ctx-6"), NOW).kind == "known_automatic"
    )
    assert index.attribution(HAContext(id="ctx-9", parent_id="ctx-8"), NOW).kind == "unknown"
    assert (
        index.attribution(HAContext(id="ctx-8", parent_id="ctx-7"), NOW).kind == "known_automatic"
    )
    for number in range(1100):
        index.observe(HAContext(id=f"extra-{number}"), NOW)
    assert len(index.links) <= 1024 and not index.proofs


def test_known_automatic_projection_is_closed_typed_and_preserves_source_value_and_quality():
    data = definition({"op": "known_automatic", "arg": {"op": "input", "name": "a"}})
    data["language_version"] = 3
    declaration = DeclarativeDefinition.model_validate(data)
    check_definition(declaration)
    for attribution, expected in [
        (Attribution(), False),
        (
            Attribution(
                kind="known_automatic", basis="registered_context", matched_context_id="apply"
            ),
            True,
        ),
    ]:
        source = FieldValue(status="valid", quality="healthy", value=False, attribution=attribution)
        result = Evaluation(declaration, {"a": source}, NOW).evaluate(declaration.outputs["value"])
        assert result.value is expected
        assert source.value is False and source.quality == "healthy"
    unavailable = FieldValue(
        status="unknown",
        value=None,
        quality="degraded",
        reasons=({"code": "input_stale", "input": "a", "since": NOW},),
    )
    result = Evaluation(declaration, {"a": unavailable}, NOW).evaluate(declaration.outputs["value"])
    assert result.status == "unknown" and result.value is None
    data["language_version"] = 2
    with pytest.raises(ValueError, match="language_version_3_required"):
        check_definition(DeclarativeDefinition.model_validate(data))


async def test_v2_reports_never_borrow_previous_state_context_and_v1_storage_stays_old(
    runtime, config
):
    await activate(runtime, config)
    async with aiohttp.ClientSession() as session:
        adapter = BridgeAdapter(runtime, session, "ws://unused", "test")
        adapter.protocol_selected = 2
        adapter.contexts.set_epoch("ha")
        adapter.contexts.register(HAContext(id="known"), runtime.clock.now_utc())
        state = {
            "state": "on",
            "attributes": {},
            "context": {"id": "known", "parent_id": None, "user_id": "service"},
            "last_changed": runtime.clock.now_utc().isoformat(),
        }
        no_context = adapter.observation(
            runtime.config.bindings[0], state, "report", {"context_id": "different"}, runtime.config
        )
        assert no_context.ha_context.id == "different" and no_context.ha_context.user_id is None
        assert no_context.attribution.kind == "unknown"
        with_context = adapter.observation(
            runtime.config.bindings[0],
            state,
            "report",
            {"context": state["context"]},
            runtime.config,
        )
        # Same ID with conflicting user information cannot inherit a previously registered proof.
        assert with_context.attribution.kind == "unknown"
        adapter.protocol_selected = 1
        legacy = adapter.observation(
            runtime.config.bindings[0], state, "report", {"context_id": "known"}, runtime.config
        )
        assert (
            "attribution" not in legacy.storage_document()
            and "ha_context" not in legacy.storage_document()
        )
        assert Observation.model_validate(legacy.storage_document()).ha_context_id == "known"


async def test_context_registration_requires_admin_approved_producer_and_current_ha_epoch(
    runtime, config, aiohttp_client, tmp_path
):
    config.update(
        config_schema_version=2,
        context_producers=[
            {"producer_id": "example.apply", "display_name": "Beispieldienst", "kind": "apply"}
        ],
    )
    validate(config, runtime.types)
    await activate(runtime, config)
    runtime.context_index = ContextIndex()
    runtime.context_index.set_epoch("current-ha")
    runtime.bridge, runtime.bridge_protocol_selected = "connected", 2
    tokens = Tokens(tmp_path)
    client = await aiohttp_client(API(runtime, tokens, str(uuid4())).application())
    request = {
        "context": {"id": "apply-context"},
        "producer_id": "example.apply",
        "ha_epoch_id": "current-ha",
        "lifetime_s": 600,
    }
    consumer = {"Authorization": "Bearer " + tokens.values["consumer"]}
    admin = {"Authorization": "Bearer " + tokens.values["admin"]}
    assert (await client.post("/api/v1/ha/contexts", json=request, headers=consumer)).status == 403
    assert (
        await client.post(
            "/api/v1/ha/contexts", json={**request, "producer_id": "unapproved"}, headers=admin
        )
    ).status == 400
    assert (
        await client.post(
            "/api/v1/ha/contexts", json={**request, "ha_epoch_id": "old-ha"}, headers=admin
        )
    ).status == 400
    assert not runtime.context_index.proofs
    assert (await client.post("/api/v1/ha/contexts", json=request, headers=admin)).status == 200
    assert (
        runtime.context_index.attribution(
            HAContext(id="child", parent_id="apply-context"), runtime.clock.now_utc()
        ).kind
        == "known_automatic"
    )
    runtime.store.available = False
    failed = await client.post(
        "/api/v1/ha/contexts", json={**request, "context": {"id": "uncommitted"}}, headers=admin
    )
    assert failed.status == 503
    assert "uncommitted" not in runtime.context_index.proofs
    runtime.store.available = True
    await runtime.submit("recover")
    assert not runtime.context_index.proofs
