"""Catalog contributions to the core's explicit extension slots."""

import copy
from typing import Any
from uuid import uuid4

from core_contracts import declarative_models as dm
from core_contracts import slots
from core_contracts.authoring import MISSING, AuthoringError, conflict_row
from core_contracts.extensions.catalog.interface_upgrade import migrate_runtime, migration_preview
from core_contracts.extensions.catalog.managed_catalog import (
    definition,
    definition_digest,
    dependencies,
    head,
    head_key,
    identity,
    invalidate,
    lookup,
    prepare,
    suspend_machine,
)
from core_contracts.numeric_equivalence import equivalent, preserve_numbers
from core_contracts.rules.catalog_models import CatalogWrite, EntryChange, ManagedCatalog
from core_contracts.schema import FieldSchema


def check(
    rule: dm.ManagedCatalogRule, definition: Any, inputs: Any, args: Any, require: Any, scalar: Any
) -> FieldSchema:
    if rule.profile == "apple_tv_title" and definition.language_version < 8:
        raise ValueError("language_version_8_required")
    if rule.profile and definition.language_version < 7:
        raise ValueError("language_version_7_required")
    if definition.language_version < 4:
        raise ValueError("language_version_4_required")
    for arg in args:
        require(arg, scalar("text"))
    if rule.field_schema.type != "enum" or not rule.field_schema.value_catalog:
        raise ValueError("catalog_classification_schema")
    return rule.field_schema


def evaluate(evaluation: Any, rule: dm.ManagedCatalogRule) -> Any:
    assert evaluation.context is not None
    return lookup(
        evaluation.context, rule, [evaluation.evaluate(child) for _, child in children(rule)]
    )


def children(rule: dm.ManagedCatalogRule) -> list[tuple[str, Any]]:
    return [(name, getattr(rule, name)) for name in ("scope", "media_type", "signal_type", "key")]


def export_contents(checked: Any, state: Any, request: Any, draft: Any) -> Any:
    contents = []
    for catalog in checked.catalogs:
        if isinstance(catalog, ManagedCatalog):
            current = head(state, catalog)
            contents.append(
                {
                    "catalog_id": catalog.catalog_id,
                    "definition_version": catalog.version,
                    "definition_digest": current["definition_digest"],
                    "source_revision": current["revision"],
                    "entries": [row for _, row in sorted(current["entries"].items())],
                }
            )
            if request.get("draft_id"):
                migration = next(
                    (
                        row.get("class_migration")
                        for row in draft.get("portable", {})
                        .get("package", {})
                        .get("catalog_contents", [])
                        if row["catalog_id"] == catalog.catalog_id
                        and row["definition_version"] == catalog.version
                    ),
                    None,
                )
                if migration:
                    contents[-1]["class_migration"] = copy.deepcopy(migration)
    return contents


def verify_contents(package: Any, incoming: Any) -> None:
    for content in package["catalog_contents"]:
        original_catalog = next(
            (
                catalog
                for catalog in incoming.catalogs
                if isinstance(catalog, ManagedCatalog)
                and (catalog.catalog_id, catalog.version)
                == (content["catalog_id"], content["definition_version"])
            ),
            None,
        )
        if (
            original_catalog is None
            or definition_digest(original_catalog) != content["definition_digest"]
        ):
            raise ValueError("import_catalog_definition")


def remap_contents(package: Any, incoming: Any) -> None:
    for content in package["catalog_contents"]:
        catalog = next(
            c
            for c in incoming.catalogs
            if c.catalog_id == content["catalog_id"] and c.version == content["definition_version"]
        )
        assert isinstance(catalog, ManagedCatalog)
        content["definition_digest"] = definition_digest(catalog)


def preview_contents(
    state: Any, package: Any, incoming: Any, conflicts: Any, unresolved: Any, resolutions: Any
) -> Any:
    catalog_migrations = migration_preview(state, package["catalog_contents"])
    heads = {
        head_key(c): head(state, c)["revision"]
        for c in incoming.catalogs
        if isinstance(c, ManagedCatalog)
    }
    for content in package["catalog_contents"]:
        content_catalog = next(
            (
                c
                for c in incoming.catalogs
                if isinstance(c, ManagedCatalog)
                and (c.catalog_id, c.version)
                == (content["catalog_id"], content["definition_version"])
            ),
            None,
        )
        if (
            content_catalog is None
            or definition_digest(content_catalog) != content["definition_digest"]
        ):
            raise ValueError("import_catalog_definition")
        seen: set[str] = set()
        for row in content["entries"]:
            entry = EntryChange.model_validate(
                {k: v for k, v in row.items() if k not in {"master_identity", "normalized_key"}}
            )
            key = identity(entry.scope, entry.media_type, entry.signal_type, entry.key)
            if key in seen:
                raise ValueError("import_catalog_duplicate")
            seen.add(key)
            previous = head(state, content_catalog)["entries"].get(key)
            if previous:
                restored_entry = preserve_numbers(row, previous)
                row.clear()
                row.update(restored_entry)
            if previous and not equivalent(previous, row):
                conflict = f"catalog-entry:{head_key(content_catalog)}:{key}"
                conflicts.append(conflict)
                if resolutions.get(conflict) not in ("keep", "file"):
                    unresolved.append(conflict)
    return catalog_migrations, heads


def apply_contents(
    runtime: Any, state: Any, plan: Any, config: Any, now: Any, context_effects: Any
) -> Any:
    latches = runtime.latches
    for content in plan["package"]["catalog_contents"]:
        catalog = definition(config, content["catalog_id"], content["definition_version"])
        current = head(state, catalog)
        if (
            current["revision"] != plan["catalog_heads"][head_key(catalog)]
            or definition_digest(catalog) != content["definition_digest"]
        ):
            raise ValueError("catalog_content_conflict")
        changes = [
            EntryChange.model_validate(
                {k: v for k, v in row.items() if k not in {"master_identity", "normalized_key"}}
            )
            for row in content["entries"]
        ]
        changes = [
            entry
            for entry in changes
            if plan["resolutions"].get(
                f"catalog-entry:{head_key(catalog)}:{identity(entry.scope, entry.media_type, entry.signal_type, entry.key)}"
            )
            != "keep"
        ]
        # One staged mutation and one DB commit, including cross-row parent links.
        if changes:
            body = CatalogWrite(
                catalog_id=catalog.catalog_id,
                definition_version=catalog.version,
                expected_active_revision=state.active_revision,
                expected_content_revision=head(state, catalog)["revision"],
                request_id=str(uuid4()),
                changes=(changes[0],),
                reason="Konfiguration eingelesen",
            )
            body = body.model_copy(update={"changes": tuple(changes)})
            result = prepare(state, config, body, plan["actor"], now, expanded_limit=100_000)
            latches = invalidate(state, result, latches, context_effects)
    migrate_runtime(state, plan["package"]["catalog_contents"], now)
    return latches


def effects_match(state: Any, config: Any, package: Any) -> Any:
    for content in package.get("catalog_contents", []):
        if config is None:
            return False
        try:
            catalog = definition(config, content["catalog_id"], content["definition_version"])
        except ValueError:
            return False
        if definition_digest(catalog) != content["definition_digest"]:
            return False
        current_head = head(state, catalog)
        for row in content["entries"]:
            entry = EntryChange.model_validate(
                {k: v for k, v in row.items() if k not in {"master_identity", "normalized_key"}}
            )
            key = identity(entry.scope, entry.media_type, entry.signal_type, entry.key)
            current = current_head.get("entries", {}).get(key)
            if not current:
                return False
            desired = entry.model_dump(mode="json")
            if any(current.get(k) != v for k, v in desired.items() if k not in {"operation"}):
                return False
    return True


async def rebuild_contents(
    runtime: Any, state: Any, old: Any, plan: Any, config: Any, decisions: Any, conflicts: Any
) -> None:
    for content in plan["package"]["catalog_contents"]:
        catalog = definition(config, content["catalog_id"], content["definition_version"])
        key = head_key(catalog)
        current = head(state, catalog)
        if definition_digest(catalog) != content["definition_digest"]:
            raise AuthoringError("catalog_definition_conflict", catalog_id=catalog.catalog_id)
        plan["catalog_heads"][key] = current["revision"]
        original_entries = await runtime.store.managed_entries(
            key, old["catalog_heads"].get(key, 0)
        )
        kept_entries = []
        for row in content["entries"]:
            wanted_entry = EntryChange.model_validate(
                {k: v for k, v in row.items() if k not in {"master_identity", "normalized_key"}}
            )
            entry_key = identity(
                wanted_entry.scope,
                wanted_entry.media_type,
                wanted_entry.signal_type,
                wanted_entry.key,
            )
            actual = current["entries"].get(entry_key, MISSING)
            old_entry = original_entries.get(entry_key, MISSING)
            wanted = wanted_entry.model_dump(mode="json")
            actual_plain = {k: actual.get(k) for k in wanted} if actual != MISSING else MISSING
            old_plain = {k: old_entry.get(k) for k in wanted} if old_entry != MISSING else MISSING
            path = f"/catalog_contents/{key}/{entry_key}"
            # An explicit keep in the original plan carries no overwrite intent.
            if old.get("resolutions", {}).get(f"catalog-entry:{key}:{entry_key}") == "keep":
                continue
            if actual_plain == wanted or wanted == old_plain:
                continue
            if actual_plain == old_plain:
                kept_entries.append(row)
                continue
            entry_conflict = conflict_row(path, old_plain, wanted, actual_plain, "catalog")
            digests = entry_conflict["digests"]
            choice = decisions.get(path, {})
            if choice.get("digests") != digests or choice.get("choice") not in {"draft", "current"}:
                conflicts.append(entry_conflict)
            elif choice["choice"] == "draft":
                kept_entries.append(row)
        content["entries"] = kept_entries


def check_lookup(config: Any, add: Any) -> None:
    from core_contracts.declarative import rule_children
    from core_contracts.declarative_models import ManagedCatalogRule, ReferenceRule

    def check_lookup(rule: Any, path: str) -> None:
        if isinstance(rule, ManagedCatalogRule):
            catalog = next(
                (
                    c
                    for c in config.catalogs
                    if c.catalog_id == rule.catalog_id and c.version == rule.definition_version
                ),
                None,
            )
            if (
                not isinstance(catalog, ManagedCatalog)
                or catalog.classification_schema.field_schema() != rule.field_schema
            ):
                add("catalog_reference", path, "managed catalog definition or schema mismatch")
        if not isinstance(rule, ReferenceRule):
            for name, child in rule_children(rule):
                check_lookup(child, path + "/" + name)

    for index, lookup_definition in enumerate(config.definitions):
        for group in ("rules", "outputs"):
            for name, rule in getattr(lookup_definition, group).items():
                check_lookup(rule, f"/definitions/{index}/{group}/{name}")


slots.RULES[dm.ManagedCatalogRule] = slots.Rule(
    check=check,
    evaluate=evaluate,
    children=children,
    dependencies={"track": dependencies, "suspend": suspend_machine},
)
slots.PORTABLE.update(
    catalog_export=export_contents,
    catalog_verify=verify_contents,
    catalog_remap=remap_contents,
    catalog_preview=preview_contents,
    catalog_apply=apply_contents,
    catalog_migrations=migration_preview,
)
slots.AUTHORING.update(catalog_effects=effects_match, catalog_rebuild=rebuild_contents)
slots.VALIDATION["catalog"] = check_lookup
