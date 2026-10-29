"""Bounded, actor-owned snapshot runs; CPU work never runs on the asyncio loop."""

import asyncio
import copy
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID, uuid4

from .managed_catalog import affected_contracts, head, invalidate, prepare
from .managed_catalog import identity as entry_identity
from .model import canonical, checksum
from .title_import import MAX_ENTRIES, source_identity
from .title_sync import current_mapping, plan, public_plan, write_request

PAGE_SIZE = 350
MAX_RUNS = 16


def selected(runtime: Any, actor: Any, plan_id: str) -> dict[str, Any]:
    if not isinstance(plan_id, str):
        raise ValueError("title_sync_plan_conflict")
    run: dict[str, Any] | None = runtime.title_sync_plans.get(plan_id)
    if not run or run.get("_owner") != checksum(actor) or run.get("_cancelled"):
        raise ValueError("title_sync_plan_conflict")
    return run


def cancel(runtime: Any, actor: Any, plan_id: str) -> dict[str, Any]:
    if isinstance(plan_id, str) and plan_id not in runtime.title_sync_plans:
        return {"plan_id": plan_id, "cancelled": True}
    run = selected(runtime, actor, plan_id)
    run["_cancelled"] = True
    reading = run.get("_reading")
    if reading is not None:
        reading.cancel()
    runtime.title_sync_plans.pop(plan_id, None)
    return {"plan_id": plan_id, "cancelled": True}


def stored(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return (
        {k: v for k, v in row.items() if k not in {"normalized_key", "master_identity"}}
        if row
        else None
    )


def build(
    runtime: Any, state: Any, snapshot: dict[str, Any], body: dict[str, Any]
) -> dict[str, Any]:
    result = plan(runtime, state, snapshot, body)
    rows = snapshot["entries"]
    families: dict[str, list[int]] = defaultdict(list)
    owner: dict[str, str] = {}
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str)
            for key in ("scope", "media_type", "signal_type", "normalized_key")
        ):
            continue
        identity = canonical(source_identity(row))
        family = canonical(source_identity(row, row.get("parent_key") or row["normalized_key"]))
        owner[identity] = family
        families[family].append(number)
    bad = {error["row"] for error in result["errors"]}
    counts = Counter(result["counts"])
    for family, numbers in families.items():
        if len(numbers) > PAGE_SIZE:
            result["errors"].extend(
                {"row": n, "context": "", "reasons": ["title_sync_family_limit"]} for n in numbers
            )
            bad.update(numbers)
            for number in numbers:
                previous = result["_outcomes"][number]
                if previous != "unmappable":
                    counts[previous] -= 1
                    counts["unmappable"] += 1
    result["counts"] = {key: count for key, count in counts.items() if count}
    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    changed: set[str] = set()
    config = runtime._config(state)
    if config is None:
        raise ValueError("no_active_registry")
    catalogs = {(c.catalog_id, c.version): c for c in config.catalogs}
    views: dict[tuple[str, str], dict[str, Any]] = {}
    for group_id, group in result["groups"].items():
        catalog = catalogs[(group["catalog_id"], group["definition_version"])]
        old_entries = head(state, catalog)["entries"]
        new_entries = {**old_entries, **group["entries"]}

        def classification(row: Any, entries: Any) -> str:
            if not row or row.get("tombstone"):
                return "–"
            master = row.get("master_key")
            chosen = (
                entries.get(
                    entry_identity(row["scope"], row["media_type"], row["signal_type"], master), {}
                )
                if master
                else row
            )
            value = chosen.get("classification")
            if value is None or chosen.get("tombstone"):
                return "–"
            legend = (catalog.classification_schema.legend or {}).get(value)
            return str(legend.display_name if legend else value)

        for key, row in group["entries"].items():
            family = owner[canonical(row["tc_metadata"]["source_identity"])]
            bucket = groups[family].setdefault(group_id, {**group, "entries": {}, "before": {}})
            bucket["entries"][key] = row
            bucket["before"][key] = group["before"].get(key)
            if stored(group["before"].get(key)) != row:
                changed.add(family)
            if not row["tc_metadata"].get("is_alias"):
                before = group["before"].get(key)
                master = (
                    new_entries.get(
                        entry_identity(
                            row["scope"], row["media_type"], row["signal_type"], row["master_key"]
                        ),
                        {},
                    )
                    if row.get("master_key")
                    else {}
                )
                views[(group_id, key)] = {
                    "catalog_id": group["catalog_id"],
                    "before": before,
                    "after": row,
                    "old_class": classification(before, old_entries),
                    "new_class": classification(row, new_entries),
                    "master": master.get("display_name"),
                    "change": "new"
                    if not before
                    else "unchanged"
                    if stored(before) == row
                    else "changed",
                }
    pages: list[dict[str, Any]] = []
    page: dict[str, Any] = {"sources": [], "groups": {}}
    for family in sorted(families):
        numbers = families[family]
        if family not in changed or bad.intersection(numbers):
            continue
        if len(page["sources"]) + len(numbers) > PAGE_SIZE:
            pages.append(page)
            page = {"sources": [], "groups": {}}
        page["sources"].extend(numbers)
        for key, group in groups[family].items():
            bucket = page["groups"].setdefault(key, {**group, "entries": {}, "before": {}})
            bucket["entries"].update(group["entries"])
            bucket["before"].update(group["before"])
    if page["sources"]:
        pages.append(page)
    result["_pages"] = pages or [{"sources": [], "groups": {}}]
    for page in result["_pages"]:
        page["items"] = [
            views[(group_id, key)]
            for group_id, group in sorted(page["groups"].items())
            for key in sorted(group["entries"])
            if (group_id, key) in views
        ]
    result["_index"] = 0
    result["_heads"] = {key: group["expected"] for key, group in result["groups"].items()}
    result["_completed"] = False
    result["error_groups"] = []
    errors: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for error in result["errors"]:
        source = rows[error["row"] - 1]
        error.setdefault("key", source.get("key") if isinstance(source, dict) else None)
        for reason in error["reasons"]:
            errors[(error.get("context", ""), reason)].append(error)
    result["error_groups"] = [
        {"context": ctx, "reason": reason, "count": len(details), "details": details}
        for (ctx, reason), details in sorted(errors.items())
    ]
    result["_basis"] = result["plan_digest"]
    position(result)
    return result


def position(run: dict[str, Any]) -> None:
    page = run["_pages"][run["_index"]]
    run["groups"] = page["groups"]
    run["page"] = run["_index"] + 1
    run["page_count"] = len(run["_pages"])
    run["page_source_rows"] = len(page["sources"])
    run["can_apply"] = not run["errors"]
    run["plan_digest"] = checksum(
        [run["_basis"], run["_index"], run["_heads"], run["mapping_version"]]
    )


async def preview(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    continuing = body.get("plan_id")
    if continuing is not None and (not isinstance(continuing, str) or not continuing):
        raise ValueError("title_sync_plan_conflict")
    run_id = (
        continuing or str(UUID(str(body["run_id"])))
        if body.get("run_id")
        else continuing or str(uuid4())
    )
    if continuing:
        record = selected(runtime, body.get("_actor"), continuing)
        if record.get("_index", 0) or record.get("_completed") or record.get("_busy"):
            raise ValueError("title_sync_plan_conflict")
    else:
        if run_id in runtime.title_sync_plans:
            raise ValueError("title_sync_plan_conflict")
        if sum(not row.get("_completed") for row in runtime.title_sync_plans.values()) >= MAX_RUNS:
            raise ValueError("title_sync_run_limit")
        # Completed records contain no snapshot; bound these small tombstones too.
        for key in list(runtime.title_sync_plans):
            if len(runtime.title_sync_plans) < MAX_RUNS * 2:
                break
            if runtime.title_sync_plans[key].get("_completed"):
                runtime.title_sync_plans.pop(key)
        record = {"_owner": checksum(body.get("_actor")), "_busy": True}
        runtime.title_sync_plans[run_id] = record
    record["_busy"] = True
    try:
        if not continuing:
            snapshot = body.get("_snapshot")
            if snapshot is None:
                record["_reading"] = asyncio.create_task(body["_reader"]())
                try:
                    snapshot = await record["_reading"]
                finally:
                    record.pop("_reading", None)
            if record.get("_cancelled"):
                raise ValueError("title_sync_plan_conflict")
            record["_read_at"] = runtime.clock.now_utc().isoformat()
            record["_snapshot"] = await asyncio.to_thread(copy.deepcopy, snapshot)
        state = runtime.state
        result = await asyncio.to_thread(build, runtime, state, record["_snapshot"], body)
        if record.get("_cancelled") or runtime.title_sync_plans.get(run_id) is not record:
            raise ValueError("title_sync_plan_conflict")
        result.update(
            plan_id=run_id,
            at=record["_read_at"],
            _read_at=record["_read_at"],
            _owner=record["_owner"],
            _snapshot=record["_snapshot"],
        )
        runtime.title_sync_plans[run_id] = result
        return result
    except BaseException as error:
        if continuing and not record.get("_cancelled"):
            record["_busy"] = False
        elif runtime.title_sync_plans.get(run_id) is record:
            runtime.title_sync_plans.pop(run_id, None)
        if isinstance(error, asyncio.CancelledError) and record.get("_cancelled"):
            raise ValueError("title_sync_plan_conflict") from None
        raise


def page_view(runtime: Any, actor: Any, plan_id: str) -> dict[str, Any]:
    run = selected(runtime, actor, plan_id)
    if run.get("_busy"):
        raise ValueError("title_sync_plan_conflict")
    items = run["_pages"][run["_index"]]["items"] if not run.get("_completed") else []
    return {"items": items, **public_plan(run)}


def prepare_page(
    runtime: Any, original: Any, run: dict[str, Any], body: dict[str, Any], now: Any
) -> tuple[Any, dict[str, Any]]:
    state = original.clone()
    config = runtime._config(state)
    if state.active_revision != run["expected_active_revision"]:
        raise ValueError("active_revision_conflict")
    if current_mapping(state)["version"] != run["mapping_version"]:
        raise ValueError("title_sync_mapping_conflict")
    for catalog in config.catalogs:
        from .managed_catalog import head_key

        key = head_key(catalog)
        if key in run["_heads"] and head(state, catalog)["revision"] != run["_heads"][key]:
            raise ValueError("catalog_content_conflict")
    affected: set[str] = set()
    latches = copy.deepcopy(runtime.latches)
    revisions = {}
    changed = 0
    for key, group in run["groups"].items():
        group = {**group, "expected": run["_heads"][key]}
        prepared = prepare(
            state,
            config,
            write_request(group, state.active_revision, body["request_id"]),
            body.get("_actor"),
            now,
            expanded_limit=MAX_ENTRIES,
        )
        revisions[key] = prepared["content_revision"]
        changed += prepared["changed"]
        affected.update(affected_contracts(state, prepared))
        latches = invalidate(state, prepared, latches)
    if affected and runtime._evaluate(
        state, config, origin="revision", initial_latches=latches, catalog_affected=affected
    ):
        runtime.pending_latches = None
        runtime.pending_refresh.clear()
        raise ValueError("catalog_evaluation_failed")
    saved = current_mapping(state)
    if saved["mapping"] != run["mapping"]:
        version = saved["version"] + 1
        state.tables["title_sync_mapping"][str(version)] = {
            "version": version,
            "mapping": run["mapping"],
            "at": now.isoformat(),
        }
    return state, {"revisions": revisions, "changed": changed, "affected": affected}


async def process(
    runtime: Any, state: Any, operation: str, body: dict[str, Any], now: Any
) -> dict[str, Any]:
    if operation == "title_sync_preview":
        return await preview(runtime, body)
    request_id = str(UUID(body["request_id"]))
    digest = checksum({key: body[key] for key in ("plan_id", "plan_digest", "request_id")})
    previous = await runtime.store.get("catalog_request", request_id)
    if previous:
        if previous["digest"] != digest:
            raise ValueError("catalog_request_conflict")
        return dict(previous["result"])
    run = selected(runtime, body.get("_actor"), body["plan_id"])
    if run.get("_completed"):
        raise ValueError("catalog_content_conflict")
    if body["plan_digest"] != run.get("plan_digest") or run.get("_busy"):
        raise ValueError("title_sync_plan_conflict")
    if not run["can_apply"]:
        raise ValueError("title_sync_mapping_invalid")
    run["_busy"] = True
    committed = False
    try:
        staged, prepared = await asyncio.to_thread(prepare_page, runtime, state, run, body, now)
        if run.get("_cancelled"):
            runtime.pending_latches = None
            runtime.pending_refresh.clear()
            raise ValueError("title_sync_plan_conflict")
        complete = run["_index"] + 1 == len(run["_pages"])
        result = {
            "request_id": request_id,
            "revisions": prepared["revisions"],
            "changed": prepared["changed"],
            "counts": run["counts"],
            "publication_seq": staged.publication_seq,
            "mapping_version": current_mapping(staged)["version"],
            "page": run["page"],
            "complete": complete,
        }
        staged.tables["catalog_request"][request_id] = {"digest": digest, "result": result}
        await runtime._commit(staged, publication=bool(prepared["affected"]))
        committed = True
        run["mapping_version"] = result["mapping_version"]
        run["_heads"].update(prepared["revisions"])
        if complete:
            for key in ("_snapshot", "_pages", "groups"):
                run.pop(key, None)
            run["_completed"] = True
            run["can_apply"] = False
        else:
            run["_index"] += 1
            position(run)
        return result
    finally:
        run["_busy"] = False
        if not committed:
            runtime.pending_latches = None
            runtime.pending_refresh.clear()
