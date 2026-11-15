"""Writer operations owned by catalog_operations."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from core_contracts import slots
from core_contracts.model import checksum
from core_contracts.persistence import State
from core_contracts.registry import RegistryConfig, resolve_ref, topological

if TYPE_CHECKING:
    from core_contracts.runtime import Runtime

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core_contracts.runtime import Runtime


def care_timing(runtime: Runtime, payload: Any, care_started: float) -> None:
    runtime.catalog_care_timings = {
        "writer_wait_ms": (care_started - payload.get("_queued_at", care_started)) * 1000,
        "state_copy_ms": (runtime.clock.monotonic() - care_started) * 1000,
    }


async def operation_catalog_scope_preview(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from core_contracts.extensions.catalog.catalog_scope import process as scope_process

    return await scope_process(runtime, state, operation, payload, now)


slots.OPERATIONS["catalog_scope_preview"] = slots.Operation(
    operation_catalog_scope_preview, state="full", early=False, heavy=True
)
slots.OPERATIONS["catalog_scope_apply"] = slots.Operation(
    operation_catalog_scope_preview, state="full", early=False, heavy=True
)


async def operation_catalog_cleanup_snapshot(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    return state


slots.OPERATIONS["catalog_cleanup_snapshot"] = slots.Operation(
    operation_catalog_cleanup_snapshot, state="full", early=False
)


async def operation_catalog_cleanup_apply(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from uuid import UUID

    from core_contracts.extensions.catalog.catalog_cleanup import apply as cleanup_apply

    cleanup_previous = await runtime.store.get("catalog_request", str(UUID(payload["request_id"])))
    if cleanup_previous:
        state.tables["catalog_request"][str(UUID(payload["request_id"]))] = cleanup_previous
    result = cleanup_apply(runtime, state, payload)
    if cleanup_previous:
        return result
    await runtime._commit(state)
    runtime.catalog_cleanup_plans.pop(payload.get("plan_id"), None)
    return result


slots.OPERATIONS["catalog_cleanup_apply"] = slots.Operation(
    operation_catalog_cleanup_apply, state="full", early=False, heavy=True
)


async def operation_catalog_tick(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from core_contracts.extensions.catalog.catalog_playback import flush

    flush(state, now)
    if state.tables != runtime.state.tables:
        await runtime._commit(state)
    return None


slots.OPERATIONS["catalog_tick"] = slots.Operation(
    operation_catalog_tick, state="live", early=False, offline=True
)


async def operation_catalog_revision(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from core_contracts.extensions.catalog.managed_catalog import definition, historical_entries

    catalog = definition(config, payload["catalog_id"], payload["definition_version"])
    entries = await historical_entries(runtime.store, catalog, payload["revision"])
    selected = [
        (key, row) for key, row in sorted(entries.items()) if key > payload.get("after", "")
    ]
    return {
        "items": [{"identity": key, **row} for key, row in selected[:100]],
        "total": len(entries),
        "next_cursor": selected[99][0] if len(selected) > 100 else None,
    }


slots.OPERATIONS["catalog_revision"] = slots.Operation(
    operation_catalog_revision, state="full", early=False
)


async def operation_catalog_write(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from core_contracts.extensions.catalog.managed_catalog import (
        affected_contracts,
        definition,
        head,
        historical_entries,
        invalidate,
        prepare,
    )
    from core_contracts.registry import safe_document
    from core_contracts.rules.catalog_models import CatalogRestore, CatalogWrite, EntryChange

    care = operation.startswith("catalog_care_")
    care_phase = runtime.clock.monotonic()
    care_digest = None
    if care:
        from core_contracts.extensions.catalog.catalog_care import CareRequest, change_request

        care_request = CareRequest.model_validate(payload["request"])
        care_digest = checksum(care_request)
        if operation == "catalog_care_write":
            previous_care = await runtime.store.get("catalog_request", care_request.request_id)
            if previous_care:
                if previous_care["digest"] != care_digest:
                    raise ValueError("catalog_request_conflict")
                return previous_care["result"]
        care_catalog = definition(config, care_request.catalog_id, care_request.definition_version)
        payload = {
            **payload,
            "request": change_request(state, care_catalog, care_request, now),
        }
        operation = "catalog_preview" if operation.endswith("preview") else "catalog_write"
    restoring = not care and "restore_revision" in payload["request"]
    request = (
        payload["request"]
        if care
        else (CatalogRestore if restoring else CatalogWrite).model_validate(payload["request"])
    )
    safe_document(request.model_dump(mode="json"))
    digest = care_digest or checksum(request)
    catalog_previous = (
        await runtime.store.get("catalog_request", request.request_id)
        if operation == "catalog_write"
        else None
    )
    if catalog_previous:
        if catalog_previous["digest"] != digest:
            raise ValueError("catalog_request_conflict")
        return catalog_previous["result"]
    if isinstance(request, CatalogRestore):
        catalog = definition(config, request.catalog_id, request.definition_version)
        past = await historical_entries(runtime.store, catalog, request.restore_revision)
        current_entries = head(state, catalog)["entries"]
        changes = []
        for key in past.keys() | current_entries.keys():
            row = past.get(key) or {**current_entries[key], "tombstone": True}
            changes.append(
                EntryChange.model_validate(
                    {k: v for k, v in row.items() if k not in {"normalized_key", "master_identity"}}
                )
            )
        request = request.model_copy(update={"changes": tuple(changes)})
    if care:
        runtime.catalog_care_timings["catalog_plan_ms"] = (
            runtime.clock.monotonic() - care_phase
        ) * 1000
    care_phase = runtime.clock.monotonic()
    result = prepare(
        state,
        config,
        request,
        payload.get("_actor"),
        now,
        expanded_limit=100_000 if restoring else 1000,
    )
    if care:
        runtime.catalog_care_timings["prepare_ms"] = (runtime.clock.monotonic() - care_phase) * 1000
    care_phase = runtime.clock.monotonic()
    if operation == "catalog_preview":
        care_effects: list[dict[str, str]] = []
        if care:
            invalidate(state, result, runtime.latches, care_effects)
        affected = affected_contracts(runtime.state, result)
        if config:
            for contract in topological(config):
                if any(resolve_ref(item.ref, config)[0] in affected for item in contract.inputs):
                    affected.add(contract.contract_id)
        if care:
            runtime.catalog_care_timings["impact_ms"] = (
                runtime.clock.monotonic() - care_phase
            ) * 1000
        return {
            "affected_keys": len(result["affected_keys"]),
            "affected_contracts": len(affected),
            **(
                {
                    "context_effects": care_effects,
                    "changes": [row.model_dump(mode="json") for row in request.changes],
                    "timings_ms": dict(runtime.catalog_care_timings),
                }
                if care
                else {}
            ),
        }
    if result["affected_keys"]:
        assert config is not None
        direct_catalog_contracts = affected_contracts(state, result)
        latches = invalidate(state, result, runtime.latches)
        failures = runtime._evaluate(
            state,
            config,
            origin="revision",
            initial_latches=latches,
            catalog_affected=direct_catalog_contracts,
        )
        if failures:
            runtime.pending_latches = None
            runtime.pending_refresh.clear()
            raise ValueError("catalog_evaluation_failed")
    result["publication_seq"] = state.publication_seq
    if care:
        runtime.catalog_care_timings["impact_ms"] = (runtime.clock.monotonic() - care_phase) * 1000
    state.tables["catalog_request"][request.request_id] = {
        "digest": digest,
        "result": result,
    }
    care_phase = runtime.clock.monotonic()
    await runtime._commit(state, publication=bool(result["affected_keys"]))
    if care:
        runtime.catalog_care_timings["commit_ms"] = (runtime.clock.monotonic() - care_phase) * 1000
    return result


slots.OPERATIONS["catalog_write"] = slots.Operation(
    operation_catalog_write, state="full", early=False, heavy=True
)
slots.OPERATIONS["catalog_preview"] = slots.Operation(
    operation_catalog_write, state="full", early=False, heavy=True
)
slots.OPERATIONS["catalog_care_write"] = slots.Operation(
    operation_catalog_write, state="catalog", early=False, after_prepare=care_timing, heavy=True
)
slots.OPERATIONS["catalog_care_preview"] = slots.Operation(
    operation_catalog_write, state="catalog", early=False, after_prepare=care_timing, heavy=True
)


async def submit_care(runtime: Runtime, operation: str, payload: Any) -> Any:
    payload = {**payload, "_queued_at": runtime.clock.monotonic()}
    return await runtime._enqueue(operation, payload)


slots.SUBMIT_PREFIXES["catalog_care_"] = submit_care
