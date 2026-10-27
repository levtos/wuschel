"""Explicit v3 device mapping into the existing portable transaction boundary."""

import copy
from collections import Counter
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .catalog_models import EntryChange
from .managed_catalog import definition, definition_digest, head, identity
from .model import Model, canonical, checksum
from .portable import export, payload_digest, stage, strict_json
from .portable import preview as portable_preview
from .title_import import MAX_BYTES, MAX_ENTRIES, plan_title_import


def context_key(context: dict[str, Any]) -> str:
    return canonical([context["context"], context.get("source_app", "")])


class TargetMapping(Model):
    catalog_id: str = Field(min_length=1, max_length=128)
    definition_version: int = Field(ge=1, strict=True)
    scope: str = Field(min_length=1, max_length=128)
    profile: Literal["game", "music", "private"]


class MusicKeyRequired(ValueError):
    def __init__(self, item: dict[str, Any]) -> None:
        super().__init__("artist_title_mapping_required")
        self.row = item["row"]
        self.key = item["display_name"]


def classification(profile: str, value: int) -> str:
    if profile == "game":
        return {0: "heimkino", 1: "grind", 2: "headset", 3: "grind"}[value]
    if profile == "music":
        return {0: "normal", 1: "boost", 2: "stumm"}[value]
    if profile == "private":
        return "normal" if value == 0 else "privat"
    raise ValueError("title_import_profile")


def target_key(item: dict[str, Any], profile: str, music_keys: dict[str, Any]) -> str:
    if profile != "music":
        return str(item["display_name"])
    parts = music_keys.get(str(item["row"]))
    if parts is None:
        key = item["display_name"]
        if key.count(" - ") != 1:
            raise MusicKeyRequired(item)
        parts = key.split(" - ")
    if (
        not isinstance(parts, list)
        or len(parts) != 2
        or any(not isinstance(value, str) or not value.strip() for value in parts)
    ):
        raise MusicKeyRequired(item)
    # Same canonical tuple as the existing compound_key operator, never a guess
    # at an ambiguous legacy flattened title.
    return canonical([part.strip() for part in parts])


def preview(runtime: Any, state: Any, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) - {
        "source_text",
        "expected_active_revision",
        "mappings",
        "include_hidden",
        "music_keys",
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
    include_hidden = request.get("include_hidden", False)
    if type(include_hidden) is not bool:
        raise ValueError("title_import_hidden")
    music_keys = request.get("music_keys", {})
    if not isinstance(music_keys, dict) or len(music_keys) > MAX_ENTRIES:
        raise ValueError("title_import_mapping")
    if any(not k.isdecimal() or not 1 <= int(k) <= len(document["entries"]) for k in music_keys):
        raise ValueError("title_import_mapping")
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
            importer=1,
            mappings=mappings,
            music_keys=music_keys,
            include_hidden=include_hidden,
            resolutions=request.get("resolutions", {}),
        )
    )
    applied = state.tables["catalog_import"].get("title:" + import_id)
    if applied:
        return {**applied["result"], "already_applied": True}
    rows = document["entries"]
    parents = {tuple(row["identity"]): row for row in parsed["candidates"]}
    generated: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    expanded = 0
    counts: Counter[str] = Counter()
    outcomes: list[dict[str, Any]] = []
    errors = list(parsed["rejected_rows"])
    errors.extend(
        {"row": row, "reasons": ["normalization_collision"]}
        for group in parsed["normalization_collisions"]
        for row in group["rows"]
    )
    for candidate in parsed["candidates"]:
        row_number = candidate["row"]
        original = rows[row_number - 1]
        if music_keys.get(str(row_number)) == "exclude":
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
            entries = generated.setdefault((catalog.catalog_id, catalog.version), {})
            parent = (
                parents.get(tuple(candidate["parent_identity"]))
                if candidate["parent_identity"]
                else None
            )
            if parent and parent["hidden_at"] and not include_hidden:
                errors.append({"row": row_number, "reasons": ["hidden_master_required"]})
                continue

            def entry(item: dict[str, Any], master: dict[str, Any] | None) -> dict[str, Any]:
                source_row = rows[item["row"] - 1]
                matching_contexts = [
                    c for c in source_row.get("contexts", []) if context_key(c) == key
                ]
                value = matching_contexts[0].get("enum_override") if matching_contexts else None
                if value is None:
                    value = item["source_class"]
                return EntryChange(
                    scope=mapping["scope"],
                    media_type=source_row["media_type"],
                    signal_type=source_row["signal_type"],
                    key=target_key(item, profile, music_keys),
                    display_name=item["display_name"],
                    classification=None if master else classification(profile, value),
                    master_key=target_key(master, profile, music_keys) if master else None,
                ).model_dump(mode="json")

            try:
                if parent and music_keys.get(str(parent["row"])) == "exclude":
                    raise ValueError("excluded_master_required")
                additions = (
                    [entry(parent, None), entry(candidate, parent)]
                    if parent
                    else [entry(candidate, None)]
                )
            except MusicKeyRequired as error:
                errors.append(
                    {
                        "row": error.row,
                        "key": error.key,
                        "reasons": ["artist_title_mapping_required"],
                    }
                )
                continue
            except ValueError, KeyError:
                errors.append(
                    {
                        "row": row_number,
                        "key": original["key"],
                        "reasons": ["class_or_master_mapping_required"],
                    }
                )
                continue
            for value in additions:
                ident = identity(
                    value["scope"], value["media_type"], value["signal_type"], value["key"]
                )
                if ident in entries and entries[ident] != value:
                    errors.append({"row": row_number, "reasons": ["target_collision"]})
                if ident not in entries:
                    expanded += 1
                entries[ident] = value
                if expanded > MAX_ENTRIES:
                    raise ValueError("title_export_limit")
            planned += 1
        outcome = "planned" if planned else "excluded" if excluded else "unmapped"
        counts[outcome] += 1
        outcomes.append(
            {
                "row": row_number,
                "outcome": outcome,
                "targets": planned,
                "excluded_contexts": excluded,
            }
        )
    report = dict(
        input_rows=len(rows),
        target_entries=expanded,
        outcomes={
            **dict(counts),
            **(
                {"rejected": parsed["summary"]["rejected"]} if parsed["summary"]["rejected"] else {}
            ),
        },
        rejected_rows=parsed["summary"]["rejected"],
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
