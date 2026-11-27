"""Preview and restore registry and catalog contents in the same writer transaction."""

from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from core_contracts import slots
from core_contracts.extensions.catalog.catalog_mapping import current_mapping
from core_contracts.extensions.catalog.managed_catalog import (
    definition,
    head,
    head_key,
    historical_entries,
    prepare,
    validate_heads,
)
from core_contracts.model import Model, checksum
from core_contracts.persistence import State
from core_contracts.registry import RegistryConfig
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange, ManagedCatalog

if TYPE_CHECKING:
    from core_contracts.runtime import Runtime

PREFIX = "catalog_class_migration:"
RECEIPT = "catalog_class_rollback:"
ROLLBACK_TABLES = (
    "catalog_head",
    "catalog_content_revision",
    "catalog_entry_revision",
    "title_sync_mapping",
)


class PreparedClassRollback(Model):
    request: dict[str, Any]
    legacy_heads: dict[str, int]
    legacy_sightings: str
    catalog_rows: dict[str, dict[str, dict[str, Any]]]


def legacy_sightings(state: State, keys: dict[str, int]) -> str:
    return checksum(
        {
            key: row
            for key, row in state.tables["catalog_sighting"].items()
            if row["catalog"] in keys
        }
    )


def register_migration(state: Any, contents: list[dict[str, Any]]) -> None:
    migrations = [
        row
        for row in contents
        if row.get("class_migration", {}).get("operation") == "named_classes"
    ]
    if not migrations:
        return
    new_revision = max((int(number) for number in state.tables["registry_revision"]), default=0) + 1
    state.tables["service_state"][PREFIX + str(new_revision)] = {
        "before_revision": state.active_revision,
        "migration_revision": new_revision,
        "catalogs": [
            {
                "catalog_id": item["catalog_id"],
                "old_version": item["class_migration"]["source_version"],
                "old_revision": item["class_migration"]["source_revision"],
                "new_version": item["definition_version"],
                "new_revision": state.tables["catalog_head"][
                    checksum([item["catalog_id"], item["definition_version"]])
                ]["revision"],
                "sightings": {
                    row["identity"]: checksum(row)
                    for row in state.tables["catalog_sighting"].values()
                    if row["catalog"] == checksum([item["catalog_id"], item["definition_version"]])
                },
            }
            for item in migrations
        ],
    }


def record_for(
    state: Any, current: RegistryConfig, *, migration_revision: int | None = None
) -> Any:
    available = {
        row.catalog_id: row.version
        for row in current.catalogs
        if isinstance(row, ManagedCatalog) and row.class_assignments
    }
    candidates = [
        row
        for key, row in state.tables["service_state"].items()
        if key.startswith(PREFIX)
        and not row.get("rollback_revision")
        and (migration_revision is None or row["migration_revision"] == migration_revision)
        and all(
            available.get(item["catalog_id"], 0) >= item["new_version"] for item in row["catalogs"]
        )
    ]
    return max(candidates, key=lambda row: row["migration_revision"], default=None)


def preview(
    state: Any, current: RegistryConfig | None, *, migration_revision: int | None = None
) -> Any:
    if current is None:
        return None
    record = record_for(state, current, migration_revision=migration_revision)
    if not record:
        return None
    impacts = []
    current_catalogs = {
        row.catalog_id: row
        for row in current.catalogs
        if isinstance(row, ManagedCatalog) and row.class_assignments
    }
    for item in record["catalogs"]:
        catalog = current_catalogs[item["catalog_id"]]
        active = head(state, catalog)
        sightings = {
            row["identity"]: checksum(row)
            for row in state.tables["catalog_sighting"].values()
            if row["catalog"] == head_key(catalog)
        }
        previous = item["sightings"]
        care_revisions = (
            sum(
                row["revision"]
                for row in state.tables["catalog_head"].values()
                if row["catalog_id"] == catalog.catalog_id
                and item["new_version"] <= row["definition_version"] <= catalog.version
            )
            - item["new_revision"]
        )
        impacts.append(
            {
                "catalog_id": catalog.catalog_id,
                "content_revision": active["revision"],
                "definition_version": catalog.version,
                "care_revisions": care_revisions,
                "changed_sightings": sum(
                    sightings.get(key) != previous.get(key)
                    for key in sightings.keys() | previous.keys()
                ),
                "sightings_digest": checksum(sightings),
            }
        )
    result = {
        "expected_active_revision": state.active_revision,
        "migration_revision": record["migration_revision"],
        "target_revision": record["before_revision"],
        "catalogs": impacts,
        "mapping_version": current_mapping(state)["version"],
    }
    return {**result, "plan_digest": checksum(result)}


def receipt_key(payload: Any) -> str:
    request_id = payload.get("request_id")
    if not isinstance(request_id, str):
        raise ValueError("catalog_rollback_request_invalid")
    try:
        return RECEIPT + str(UUID(request_id))
    except ValueError as error:
        raise ValueError("catalog_rollback_request_invalid") from error


def prior(state: Any, payload: Any) -> Any:
    if "catalog_migration_revision" not in payload:
        return None
    row = state.tables["service_state"].get(receipt_key(payload))
    if row is None:
        return None
    if row["request_digest"] != checksum(payload):
        raise ValueError("catalog_rollback_request_conflict")
    return row["result"]


def check_legacy_transition(current: RegistryConfig | None, target: RegistryConfig) -> None:
    current_named = (
        {
            item.catalog_id
            for item in current.catalogs
            if isinstance(item, ManagedCatalog) and item.class_assignments
        }
        if current
        else set()
    )
    if any(
        isinstance(item, ManagedCatalog)
        and item.catalog_id in current_named
        and not item.class_assignments
        for item in target.catalogs
    ):
        raise ValueError("catalog_migration_rollback_required")


async def apply(
    runtime: Any,
    state: Any,
    payload: Any,
    current: RegistryConfig | None,
    restored: RegistryConfig,
    now: Any,
    *,
    prepared: PreparedClassRollback | None = None,
) -> None:
    movement = payload.get("catalog_migration_revision")
    if movement is None:
        check_legacy_transition(current, restored)
        if any(
            isinstance(item, ManagedCatalog) and item.class_assignments
            for item in restored.catalogs
        ):
            validate_heads(state, restored)
        return
    if current is None:
        raise ValueError("catalog_migration_conflict")
    planned = preview(state, current, migration_revision=movement)
    if (
        not planned
        or payload["revision"] != planned["target_revision"]
        or payload.get("plan_digest") != planned["plan_digest"]
    ):
        raise ValueError("catalog_migration_conflict")
    record = record_for(state, current, migration_revision=movement)
    assert record is not None
    if prepared is not None:
        if (
            any(
                state.tables["catalog_head"].get(key, {}).get("revision") != revision
                for key, revision in prepared.legacy_heads.items()
            )
            or legacy_sightings(state, prepared.legacy_heads) != prepared.legacy_sightings
        ):
            raise ValueError("catalog_migration_conflict")
        for table in ROLLBACK_TABLES:
            for key, row in prepared.catalog_rows.get(table, {}).items():
                if "at" in row:
                    row = {**row, "at": now.isoformat()}
                state.tables[table][key] = row
        return
    actor = payload.get("_actor")
    for item in record["catalogs"]:
        catalog = definition(restored, item["catalog_id"], item["old_version"])
        snapshot = await historical_entries(runtime.store, catalog, item["old_revision"])
        existing = head(state, catalog)
        if snapshot != existing["entries"] or existing["revision"] != item["old_revision"]:
            raise ValueError("catalog_migration_conflict")
        changes = tuple(
            EntryChange.model_validate(
                {key: value for key, value in row.items() if key in EntryChange.model_fields}
            )
            for row in snapshot.values()
        )
        request = CatalogWrite.model_construct(
            catalog_id=catalog.catalog_id,
            definition_version=catalog.version,
            expected_active_revision=state.active_revision,
            expected_content_revision=existing["revision"],
            request_id=payload["request_id"],
            reason="Umzug der Katalogklassen zurückgenommen",
            changes=changes,
        )
        prepare(state, restored, request, actor, now, expanded_limit=100_000, force_revision=True)
    validate_heads(state, restored)
    mapping = deepcopy(current_mapping(state))
    reverse = {item["catalog_id"]: item for item in record["catalogs"]}
    targets = mapping["mapping"]["mappings"]
    for target in targets.values():
        previous = reverse.get(target["catalog_id"])
        if previous is not None and target["definition_version"] >= previous["new_version"]:
            target["definition_version"] = previous["old_version"]
    saved = current_mapping(state)
    if saved["mapping"] != mapping["mapping"]:
        version = saved["version"] + 1
        state.tables["title_sync_mapping"][str(version)] = {
            **mapping,
            "version": version,
            "at": now.isoformat(),
        }


async def prepare_work_rollback(runtime: Runtime, payload: dict[str, Any]) -> Any:
    movement = payload.get("catalog_migration_revision")
    if type(movement) is not int:
        raise ValueError("catalog_migration_conflict")
    if previous := prior(runtime.state, payload):
        return previous
    if type(payload.get("expected_active_revision")) is not int:
        raise ValueError("invalid_expected_revision")
    if payload["expected_active_revision"] != runtime.state.active_revision:
        raise ValueError("active_revision_conflict")
    current = runtime.config
    record = record_for(runtime.state, current, migration_revision=movement) if current else None
    if record is None:
        raise ValueError("catalog_migration_conflict")
    historical = await runtime.store.get("registry_revision", str(payload["revision"]))
    if historical is None:
        raise ValueError("revision_not_found")
    restored = await runtime._checked_config(historical["config"], restore=False)
    state = runtime.state.catalog_clone()
    await apply(runtime, state, payload, current, restored, runtime.clock.now_utc())
    keys = {
        head_key(definition(restored, row["catalog_id"], row["old_version"])): row["old_revision"]
        for row in record["catalogs"]
    }
    return PreparedClassRollback(
        request=payload,
        legacy_heads=keys,
        legacy_sightings=legacy_sightings(runtime.state, keys),
        catalog_rows={
            table: {key: state.tables[table][key] for key in state._changes.changed.get(table, ())}
            for table in ROLLBACK_TABLES
        },
    )


async def publish_rollback(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: PreparedClassRollback,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from core_contracts.authoring import operation_activate

    return await operation_activate(
        runtime, state, "rollback", payload.request, now, config, prepared_rollback=payload
    )


def complete(state: Any, payload: Any, result: Any) -> None:
    if (movement := payload.get("catalog_migration_revision")) is None:
        return
    key = PREFIX + str(movement)
    state.tables["service_state"][key] = {
        **state.tables["service_state"][key],
        "rollback_revision": result["revision"],
    }
    state.tables["service_state"][receipt_key(payload)] = {
        "request_digest": checksum(payload),
        "result": result,
    }


slots.OPERATIONS["catalog_class_rollback_prepared"] = slots.Operation(
    publish_rollback, state="full", early=False
)

slots.AUTHORING.update(
    catalog_rollback_prior=prior,
    catalog_rollback_apply=apply,
    catalog_rollback_complete=complete,
    catalog_legacy_transition=check_legacy_transition,
)
