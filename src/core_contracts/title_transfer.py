"""Explicit v3 device mapping into the existing portable transaction boundary."""

import copy
from collections import Counter
from collections.abc import Iterator
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .catalog_models import EntryChange
from .managed_catalog import definition, definition_digest, head, identity
from .model import Model, canonical, checksum
from .portable import export, payload_digest, stage, strict_json
from .portable import preview as portable_preview
from .title_import import MAX_BYTES, MAX_ENTRIES, plan_title_import, source_identity


def context_key(context: dict[str, Any]) -> str:
    return canonical([context["context"], context.get("source_app", "")])


class TargetMapping(Model):
    catalog_id: str = Field(min_length=1, max_length=128)
    definition_version: int = Field(ge=1, strict=True)
    scope: str = Field(min_length=1, max_length=128)
    profile: Literal["game", "music", "private"]


def classification(profile: str, value: int) -> str:
    if profile == "game":
        return {0: "heimkino", 1: "grind", 2: "headset", 3: "grind"}[value]
    if profile == "music":
        return {0: "normal", 1: "boost", 2: "stumm"}[value]
    if profile == "private":
        return "normal" if value == 0 else "privat"
    raise ValueError("title_import_profile")


def music_forms(key: str) -> Iterator[str]:
    """Enumerate every runtime tuple; the first split is the primary form."""
    start = 0
    while (at := key.find(" - ", start)) >= 0:
        yield canonical([key[:at], key[at + 3 :]])
        start = at + 3
    yield canonical([key])


def target_key(item: dict[str, Any], profile: str) -> str:
    key = str(item["display_name"])
    return next(music_forms(key)) if profile == "music" else key


def preview(runtime: Any, state: Any, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) - {
        "source_text",
        "expected_active_revision",
        "mappings",
        "include_hidden",
        "excluded_rows",
        "resolutions",
        "plan_digest",
        "request_id",
        "_actor",
    }:
        raise ValueError("title_import_mapping")
    text = request.get("source_text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("title_export_limit")
    if (
        type(request.get("expected_active_revision")) is not int
        or request.get("expected_active_revision") != state.active_revision
    ):
        raise ValueError("active_revision_conflict")
    document = strict_json(text)
    if not isinstance(document, dict) or type(document.get("version")) is not int:
        raise ValueError("title_export_version_3_required")
    source_digest = checksum(document)
    parsed = plan_title_import(document, source_digest)
    mappings = request.get("mappings", {})
    if not isinstance(mappings, dict) or len(mappings) > 6400:
        raise ValueError("title_import_mapping")
    # Hidden TC entries are archived decisions and always belong to the transfer.
    include_hidden = True
    excluded_rows = request.get("excluded_rows", [])
    if not isinstance(excluded_rows, list) or len(excluded_rows) > MAX_ENTRIES:
        raise ValueError("title_import_mapping")
    if any(type(n) is not int or not 1 <= n <= len(document["entries"]) for n in excluded_rows):
        raise ValueError("title_import_mapping")
    excluded_set = set(excluded_rows)
    contexts = {
        context_key(context): context
        for row in parsed["candidates"]
        for context in (row["contexts"] or [{"context": "", "source_app": ""}])
    }
    if not mappings.keys() <= contexts.keys():
        raise ValueError("title_import_mapping")
    import_id = checksum(
        dict(
            source="title_classifier/v3",
            source_digest=source_digest,
            importer=2,
            mappings=mappings,
            excluded_rows=sorted(excluded_set),
            include_hidden=include_hidden,
            resolutions=request.get("resolutions", {}),
        )
    )
    applied = state.tables["catalog_import"].get("title:" + import_id)
    if applied:
        return {**applied["result"], "already_applied": True}
    rows = document["entries"]
    parents = {source_identity(rows[row["row"] - 1]): row for row in parsed["candidates"]}
    generated: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    owners: dict[tuple[str, int], dict[str, int]] = {}
    additional: dict[tuple[str, int, str], tuple[int, str, dict[str, Any]]] = {}
    expanded = 0
    expanded_bytes = 0

    def put(group: tuple[str, int], value: dict[str, Any], owner: int) -> str:
        nonlocal expanded, expanded_bytes
        ident = identity(value["scope"], value["media_type"], value["signal_type"], value["key"])
        expanded += 1
        expanded_bytes += len(canonical(value).encode("utf-8"))
        if expanded > MAX_ENTRIES or expanded_bytes > MAX_BYTES:
            raise ValueError("title_export_limit")
        generated.setdefault(group, {})[ident] = value
        owners.setdefault(group, {})[ident] = owner
        return ident

    counts: Counter[str] = Counter()
    outcomes: list[dict[str, Any]] = []
    errors = [row for row in parsed["rejected_rows"] if row["row"] not in excluded_set]
    for row in parsed["rejected_rows"]:
        if row["row"] in excluded_set:
            counts["excluded"] += 1
    errors.extend(
        {"row": row, "reasons": ["normalization_collision"]}
        for group in parsed["normalization_collisions"]
        if len(set(group["rows"]) - excluded_set) > 1 and group["identity"][1] != "music"
        for row in group["rows"]
        if row not in excluded_set
    )
    for candidate in parsed["candidates"]:
        row_number = candidate["row"]
        original = rows[row_number - 1]
        if row_number in excluded_set:
            counts["excluded"] += 1
            outcomes.append({"row": row_number, "outcome": "excluded"})
            continue
        if candidate["hidden_at"] and not include_hidden:
            counts["hidden_skipped"] += 1
            outcomes.append({"row": row_number, "outcome": "hidden_skipped"})
            continue
        planned, excluded = 0, 0
        for context in candidate["contexts"] or [{"context": "", "source_app": ""}]:
            key = context_key(context)
            mapping = mappings.get(key)
            if mapping == "exclude":
                excluded += 1
                continue
            if not isinstance(mapping, dict) or set(mapping) != {
                "catalog_id",
                "definition_version",
                "scope",
                "profile",
            }:
                errors.append({"row": row_number, "context": key, "reasons": ["context_unmapped"]})
                continue
            try:
                mapping = TargetMapping.model_validate(mapping).model_dump()
            except ValueError:
                errors.append(
                    {"row": row_number, "context": key, "reasons": ["context_mapping_invalid"]}
                )
                continue
            catalog = definition(
                runtime.config, mapping["catalog_id"], mapping["definition_version"]
            )
            profile = mapping["profile"]
            if (
                (profile == "game" and original["media_type"] != "game")
                or (profile == "music" and original["media_type"] != "music")
                or (profile == "private" and original["media_type"] != "video")
            ):
                errors.append(
                    {"row": row_number, "context": key, "reasons": ["profile_media_mismatch"]}
                )
                continue
            group = (catalog.catalog_id, catalog.version)
            entries = generated.setdefault(group, {})
            parent = (
                parents.get(source_identity(original, original["parent_key"]))
                if candidate["parent_identity"]
                else None
            )
            if candidate["parent_identity"] and parent is None:
                errors.append({"row": row_number, "reasons": ["class_or_master_mapping_required"]})
                continue
            if parent and parent["hidden_at"] and not include_hidden:
                errors.append({"row": row_number, "reasons": ["hidden_master_required"]})
                continue

            def entry(item: dict[str, Any], master: dict[str, Any] | None) -> dict[str, Any]:
                from .title_sync import keys, metadata

                source_row = rows[item["row"] - 1]
                matching_contexts = [
                    c for c in source_row.get("contexts", []) if context_key(c) == key
                ]
                value = matching_contexts[0].get("enum_override") if matching_contexts else None
                if value is None:
                    value = item["source_class"]
                primary = keys(source_row, profile)[0]
                parent_primary = keys(rows[master["row"] - 1], profile)[0] if master else None
                old = head(state, catalog)["entries"].get(
                    identity(
                        mapping["scope"],
                        source_row["media_type"],
                        source_row["signal_type"],
                        primary,
                    ),
                    {},
                )
                if (
                    master
                    and matching_contexts
                    and matching_contexts[0].get("enum_override") is not None
                ):
                    master_row = rows[master["row"] - 1]
                    master_context = next(
                        (c for c in master_row.get("contexts", []) if context_key(c) == key), {}
                    )
                    master_value = master_context.get("enum_override")
                    if classification(profile, value) != classification(
                        profile, master_row["enum"] if master_value is None else master_value
                    ):
                        raise ValueError("title_sync_variant_override")
                return EntryChange(
                    scope=mapping["scope"],
                    media_type=source_row["media_type"],
                    signal_type=source_row["signal_type"],
                    key=primary,
                    display_name=item["display_name"],
                    classification=None if master else classification(profile, value),
                    master_key=parent_primary,
                    note=old.get("note", ""),
                    reviewed_at=source_row.get("reviewed_at"),
                    hidden_at=source_row.get("hidden_at"),
                    tc_metadata=metadata(source_row),
                ).model_dump(mode="json")

            try:
                if parent and parent["row"] in excluded_set:
                    raise ValueError("excluded_master_required")
                additions = (
                    [(entry(parent, None), parent), (entry(candidate, parent), candidate)]
                    if parent
                    else [(entry(candidate, None), candidate)]
                )
            except ValueError, KeyError:
                errors.append(
                    {
                        "row": row_number,
                        "key": original["key"],
                        "reasons": ["class_or_master_mapping_required"],
                    }
                )
                continue
            for value, source_item in additions:
                ident = identity(
                    value["scope"], value["media_type"], value["signal_type"], value["key"]
                )
                if ident in entries:
                    if owners[group][ident] != source_item["row"] or entries[ident] != value:
                        errors.append({"row": row_number, "reasons": ["target_collision"]})
                    continue
                put(group, value, source_item["row"])
                if profile == "music":
                    additional[(*group, ident)] = (
                        source_item["row"],
                        source_item["display_name"],
                        value,
                    )
            planned += 1
        outcome = "planned" if planned else "excluded" if excluded else "unmapped"
        counts[outcome] += 1
        outcomes.append(
            {
                "row": row_number,
                "key": candidate["display_name"],
                "outcome": outcome,
                "targets": planned,
                "excluded_contexts": excluded,
            }
        )
    # Reserve every primary form first, independent of source row order. Extra
    # forms never displace another primary or an existing catalog entry.
    skipped_forms = 0
    created_forms = 0
    primary_ids = {group: set(entries) for group, entries in generated.items()}
    contested: dict[tuple[str, int], set[str]] = {}
    current_entries = {
        group: head(state, definition(runtime.config, *group))["entries"] for group in generated
    }
    for (catalog_id, version, _), (owner, key, primary) in additional.items():
        group = (catalog_id, version)
        existing = current_entries[group]
        from .title_sync import keys

        for form in keys(rows[owner - 1], "music"):
            if form == primary["key"]:
                continue
            ident = identity(primary["scope"], primary["media_type"], primary["signal_type"], form)
            if ident in generated[group]:
                if owners[group][ident] != owner:
                    skipped_forms += 1
                    if ident not in primary_ids[group]:
                        # Neither of two ambiguous extra forms wins by row order.
                        previous = generated[group].pop(ident)
                        owners[group].pop(ident)
                        expanded -= 1
                        expanded_bytes -= len(canonical(previous).encode("utf-8"))
                        created_forms -= 1
                        skipped_forms += 1
                        contested.setdefault(group, set()).add(ident)
                continue
            if ident in contested.get(group, set()):
                skipped_forms += 1
                continue
            if ident in existing:
                skipped_forms += 1
                continue
            value = {
                **primary,
                "key": form,
                "classification": None,
                "master_key": primary["master_key"] or primary["key"],
                "tc_metadata": {**primary["tc_metadata"], "is_alias": True},
            }
            put(group, value, owner)
            created_forms += 1
    rejected = sum(row["row"] not in excluded_set for row in parsed["rejected_rows"])
    report = dict(
        input_rows=len(rows),
        target_entries=expanded,
        additional_forms_created=created_forms,
        additional_forms_skipped=skipped_forms,
        outcomes={
            **dict(counts),
            **({"rejected": rejected} if rejected else {}),
        },
        rejected_rows=rejected,
        source_summary=parsed["summary"],
        variant_decisions=parsed["variant_decisions"][:100],
        context_override_decisions=parsed["context_override_decisions"][:100],
        errors=errors[:100],
        error_count=len(errors),
        rows=outcomes[:100],
        omitted_rows=max(0, len(outcomes) - 100),
    )
    result: dict[str, Any] = dict(
        import_id=import_id,
        report=report,
        contexts=[dict(key=k, **v) for k, v in contexts.items()],
        already_applied=False,
        unresolved=["title_import_errors"] if errors else [],
    )
    if errors:
        return result
    package = export(runtime, state, {"expected_active_revision": state.active_revision})
    package["catalog_contents"] = [
        dict(
            catalog_id=catalog_id,
            definition_version=version,
            definition_digest=definition_digest(definition(runtime.config, catalog_id, version)),
            source_revision=head(state, definition(runtime.config, catalog_id, version))[
                "revision"
            ],
            entries=list(entries.values()),
        )
        for (catalog_id, version), entries in generated.items()
    ]
    package["manifest"]["payload_digest"] = payload_digest(package)
    if len(canonical(package).encode()) > MAX_BYTES:
        raise ValueError("title_export_limit")
    portable_request = {
        "package": package,
        "resolutions": request.get("resolutions", {}),
        "_actor": request.get("_actor"),
    }
    plan = portable_preview(runtime, state, portable_request)
    # While TC leads, its overlapping content wins after the explicit file preview.
    portable_request["resolutions"] = {
        **portable_request["resolutions"],
        **{key: "file" for key in plan["conflicts"] if key.startswith("catalog-entry:")},
    }
    plan = portable_preview(runtime, state, portable_request)
    result.update(
        unresolved=plan["unresolved"],
        conflicts=plan["conflicts"],
        plan_digest=checksum([import_id, plan["plan_digest"]]),
    )
    # Private processor handoff; API removes this internal package from its response.
    result["_portable_request"] = portable_request
    result["_portable_digest"] = plan["plan_digest"]
    return result


def prepare(
    runtime: Any, state: Any, request: dict[str, Any], now: Any, plan: dict[str, Any]
) -> dict[str, Any]:
    if plan["already_applied"]:
        return plan
    if plan["unresolved"] or request.get("plan_digest") != plan.get("plan_digest"):
        raise ValueError("import_plan_conflict")
    draft = stage(
        runtime,
        state,
        {
            **plan["_portable_request"],
            "plan_digest": plan["_portable_digest"],
        },
        request.get("_actor"),
        now,
    )
    draft["display_name"] = "Titelkataloge einlesen"
    draft["portable"]["title_import"] = {
        "import_id": plan["import_id"],
        "result": {k: v for k, v in plan.items() if not k.startswith("_")},
    }
    return draft


async def process(
    runtime: Any, state: Any, operation: str, request: dict[str, Any], now: Any
) -> dict[str, Any]:
    if operation == "title_preview":
        plan = preview(runtime, state, request)
        applied = await runtime.store.get("catalog_import", "title:" + plan["import_id"])
        if applied:
            return {**applied["result"], "already_applied": True}
        return {k: v for k, v in plan.items() if not k.startswith("_")}
    request_id = str(UUID(request["request_id"]))
    key = "title-stage:" + request_id
    digest = checksum(request)
    previous = await runtime.store.get("catalog_import", key)
    if previous:
        if previous["digest"] != digest:
            raise ValueError("import_plan_conflict")
        return copy.deepcopy(previous["result"])
    plan = preview(runtime, state, request)
    applied = await runtime.store.get("catalog_import", "title:" + plan["import_id"])
    if applied:
        return {**applied["result"], "already_applied": True}
    result = prepare(runtime, state, request, now, plan)
    state.tables["catalog_import"][key] = {"digest": digest, "result": result}
    await runtime._commit(state)
    return result
