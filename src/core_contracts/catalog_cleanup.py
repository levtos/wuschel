"""Read-only plans and explicitly confirmed, idempotent catalog runtime cleanup."""

import asyncio
import copy
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from .catalog_care import all_rows
from .history import HistoryQuery
from .managed_catalog import definition, head, head_key
from .model import canonical, checksum

LIMIT = 100_000
PLAN_SECONDS = 30 * 60


def basis(state: Any) -> str:
    return checksum(
        [
            state.active_revision,
            state.tables["catalog_head"],
            state.tables["catalog_sighting"],
            state.tables["service_state"].get("catalog_counter_epoch"),
        ]
    )


def transitions(
    history: list[dict[str, Any]],
    config: Any,
    bounds: dict[str, list[tuple[float, float]]] | None = None,
) -> tuple[set[str], dict[str, set[str]]]:
    """Only complete A–B–C sequences without a separate long B occurrence qualify."""
    bindings = {
        b.binding_id: b
        for b in config.bindings
        if b.adapter.ha_attribute in {"media_artist", "media_title"}
    }
    streams: dict[str, list[tuple[float, str]]] = defaultdict(list)
    current: dict[str, dict[str, Any]] = defaultdict(dict)
    # Group observations from the same HA event before reconstructing the pair.
    events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        binding = bindings.get(row.get("binding_id", ""))
        if not binding:
            continue
        at = row.get("ha_time_fired") or row.get("ha_last_updated") or row["received_at"]
        events[(str(binding.adapter.ha_entity_id), at)].append(row)
    for (source, _), rows in sorted(
        events.items(), key=lambda item: min(r["received_at"] for r in item[1])
    ):
        at = min(r["received_at"] for r in rows)
        if any(
            row.get("observation_kind") not in {"live_change", "report"}
            or row.get("ha_restored")
            or row.get("mqtt_retained")
            or row.get("assumed_state")
            or row.get("invalid_timestamp")
            or row.get("transform_error")
            or row.get("availability") != "available"
            for row in rows
        ):
            # A missing/unusable segment cannot prove a continuous transition.
            current[source].clear()
            streams[source].append((datetime.fromisoformat(at).timestamp(), "null"))
            continue
        for row in rows:
            binding = bindings[row["binding_id"]]
            current[source][str(binding.adapter.ha_attribute)] = (
                row.get("value_normalized") if row.get("normalized") else row.get("value_raw")
            )
        pair = current[source]
        if not pair.get("media_title") or not pair.get("media_artist"):
            continue
        key = canonical([pair["media_artist"], pair["media_title"]])
        if not streams[source] or streams[source][-1][1] != key:
            streams[source].append((datetime.fromisoformat(at).timestamp(), key))
    observed: dict[str, list[bool]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)
    for source, sequence in streams.items():
        for index, (at, key) in enumerate(sequence):
            if key == "null":
                continue
            sources[key].add(checksum(source))
            proven = False
            if 0 < index < len(sequence) - 1:
                before, after = (
                    json.loads(sequence[index - 1][1]),
                    json.loads(sequence[index + 1][1]),
                )
                pair = json.loads(key)
                delay = sequence[index + 1][0] - at
                proven = (
                    isinstance(before, list)
                    and isinstance(after, list)
                    and 0 <= delay <= 8
                    and (
                        before[0] == pair[0]
                        and pair[1] == after[1]
                        and before[1] != pair[1]
                        and pair[0] != after[0]
                        or before[1] == pair[1]
                        and pair[0] == after[0]
                        and before[0] != pair[0]
                        and pair[1] != after[1]
                    )
                )
            observed[key].append(proven)
            if proven and bounds is not None:
                bounds.setdefault(key, []).append((at, sequence[index + 1][0]))
    return {key for key, results in observed.items() if results and all(results)}, sources


def build(
    state: Any, catalog: Any, history: list[dict[str, Any]], config: Any
) -> list[dict[str, Any]]:
    bounds: dict[str, list[tuple[float, float]]] = {}
    proven, sources = transitions(history, config, bounds)
    contents = head(state, catalog)["entries"]
    result = []
    for key, row in all_rows(state, catalog).items():
        sighting = row.get("local_sightings")
        protected = key in contents or bool(
            row.get("tc_metadata")
            or row.get("classification")
            or row.get("reviewed_at")
            or row.get("hidden_at")
            or row.get("master_identity")
            or row.get("variant_count")
        )
        # Retention or another source may hide a separate genuine occurrence.
        complete = (
            sighting is not None
            and len(sources.get(row["key"], set())) == 1
            and all(
                any(
                    start <= datetime.fromisoformat(sighting[field]).timestamp() <= end
                    for start, end in bounds.get(row["key"], [])
                )
                for field in ("first_seen", "last_seen")
            )
        )
        kind = (
            "protected"
            if protected
            else "transition"
            if row["key"] in proven and complete
            else "unclear"
        )
        result.append(
            {
                "identity": key,
                "key": row["key"],
                "kind": kind,
                "seen_count": (sighting or {}).get("seen_count", 0),
                "first_seen": (sighting or {}).get("first_seen"),
                "source": row.get("source")
                or (
                    sorted(sources[row["key"]])[0]
                    if len(sources.get(row["key"], set())) == 1
                    else "historical"
                ),
                "protected": protected,
            }
        )
    if len(result) > LIMIT or len(canonical(result).encode()) > 50 * 1024 * 1024:
        raise ValueError("catalog_cleanup_limit")
    return result


def filtered(rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(filters, dict) or set(filters) - {
        "kind",
        "since",
        "until",
        "one",
        "unmaintained",
        "source",
    }:
        raise ValueError("catalog_cleanup_invalid")
    if any(type(filters[k]) is not bool for k in ("one", "unmaintained") if k in filters) or any(
        not isinstance(filters[k], str)
        for k in ("kind", "since", "until", "source")
        if k in filters
    ):
        raise ValueError("catalog_cleanup_invalid")
    since = datetime.fromisoformat(filters["since"]).timestamp() if filters.get("since") else None
    until = datetime.fromisoformat(filters["until"]).timestamp() if filters.get("until") else None
    if since is not None and until is not None and since > until:
        raise ValueError("catalog_cleanup_invalid")
    return [
        row
        for row in rows
        if (not filters.get("kind") or row["kind"] == filters["kind"])
        and (not filters.get("one") or row["seen_count"] == 1)
        and (not filters.get("unmaintained") or not row["protected"])
        and (not filters.get("source") or row["source"] == filters["source"])
        and (
            since is None
            or row["first_seen"]
            and datetime.fromisoformat(row["first_seen"]).timestamp() >= since
        )
        and (
            until is None
            or row["first_seen"]
            and datetime.fromisoformat(row["first_seen"]).timestamp() <= until
        )
    ]


def owned(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    expire(runtime)
    plan: dict[str, Any] | None = runtime.catalog_cleanup_plans.get(body.get("plan_id"))
    if not plan or plan["owner"] != checksum(body.get("_actor")):
        raise ValueError("catalog_cleanup_conflict")
    return plan


def expire(runtime: Any) -> None:
    now = runtime.clock.now_utc()
    for key, plan in list(runtime.catalog_cleanup_plans.items()):
        if (now - datetime.fromisoformat(plan["at"])).total_seconds() >= PLAN_SECONDS:
            del runtime.catalog_cleanup_plans[key]


async def preview(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    expire(runtime)
    if body.get("plan_id"):
        plan = owned(runtime, body)
    else:
        if len(runtime.catalog_cleanup_plans) >= 16:
            raise ValueError("catalog_cleanup_limit")
        state = await runtime.submit("catalog_cleanup_snapshot")
        config = runtime._config(state)
        catalog = definition(config, body["catalog_id"], body["definition_version"])
        now = runtime.clock.now_utc()
        sightings = [
            r
            for r in state.tables["catalog_sighting"].values()
            if r["catalog"] == head_key(catalog)
        ]
        since = min((datetime.fromisoformat(r["first_seen"]) for r in sightings), default=now)
        history: list[dict[str, Any]] = []
        history_complete = True
        history_bytes = 0
        for binding in config.bindings:
            if binding.adapter.ha_attribute not in {"media_artist", "media_title"}:
                continue
            cursor = None
            while True:
                page = await runtime.store.history(
                    "source_observation_history",
                    HistoryQuery(
                        source_id=binding.source_id,
                        since=since - timedelta(seconds=10),
                        until=now,
                        limit=200,
                        cursor=cursor,
                    ),
                )
                history.extend(page["items"])
                history_bytes += len(canonical(page["items"]).encode())
                if len(history) > LIMIT or history_bytes > 50 * 1024 * 1024:
                    history_complete = False
                    break
                cursor = page["next_cursor"]
                if not cursor:
                    break
            if not history_complete:
                break
        if not history_complete:
            history = []  # Keep bulk review available; never preselect from a partial scan.
        rows = await asyncio.to_thread(build, state, catalog, history, config)
        expire(runtime)
        if len(runtime.catalog_cleanup_plans) >= 16:
            raise ValueError("catalog_cleanup_limit")
        plan = {
            "plan_id": str(uuid4()),
            "owner": checksum(body.get("_actor")),
            "basis": basis(state),
            "rows": rows,
            "at": now.isoformat(),
            "catalog": head_key(catalog),
            "since": since.isoformat(),
            "counters_before": sum(
                r.get("seen_count", 0) for r in state.tables["catalog_sighting"].values()
            ),
            "catalog_count": sum(c.kind == "managed" for c in config.catalogs),
            "selected": [r["identity"] for r in rows if r["kind"] == "transition"],
        }
        runtime.catalog_cleanup_plans[plan["plan_id"]] = plan
    filters = body.get("filters", {})
    visible = filtered(plan["rows"], filters)
    allowed = {r["identity"] for r in plan["rows"] if not r["protected"]}
    selected = body.get("selected", plan["selected"])
    if (
        not isinstance(selected, list)
        or len(selected) > LIMIT
        or any(not isinstance(item, str) for item in selected)
        or set(selected) - allowed
    ):
        raise ValueError("catalog_cleanup_invalid")
    if body.get("select_filtered"):
        selected = sorted(set(selected) | {r["identity"] for r in visible if not r["protected"]})
    reset = body.get("reset_counters", True)
    if type(reset) is not bool:
        raise ValueError("catalog_cleanup_invalid")
    plan.update(selected=sorted(set(selected)), reset=reset)
    plan["digest"] = checksum([plan["basis"], plan["selected"], reset])
    offset = body.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("catalog_cleanup_invalid")
    counts = Counter(r["kind"] for r in plan["rows"])
    return {
        "plan_id": plan["plan_id"],
        "plan_digest": plan["digest"],
        "at": plan["at"],
        "since": plan["since"],
        "counts": dict(counts),
        "total": len(plan["rows"]),
        "filtered": len(visible),
        "items": visible[offset : offset + 100],
        "next_offset": offset + 100 if offset + 100 < len(visible) else None,
        "selected": plan["selected"],
        "reset_counters": reset,
        "counters_before": plan["counters_before"],
        "catalog_count": plan["catalog_count"],
        "sources": sorted({r["source"] for r in plan["rows"]}),
    }


def apply(runtime: Any, state: Any, body: dict[str, Any]) -> dict[str, Any]:
    request_id = str(UUID(body["request_id"]))
    digest = checksum({k: v for k, v in body.items() if k != "request_id"})
    previous = state.tables["catalog_request"].get(request_id)
    if previous:
        if previous["digest"] != digest:
            raise ValueError("catalog_cleanup_conflict")
        return copy.deepcopy(previous["result"])
    plan = owned(runtime, body)
    if (
        body.get("confirmed") is not True
        or body.get("plan_digest") != plan["digest"]
        or basis(state) != plan["basis"]
    ):
        raise ValueError("catalog_cleanup_conflict")
    selected = set(plan["selected"])
    for key, row in list(state.tables["catalog_sighting"].items()):
        if row["catalog"] == plan["catalog"] and row["identity"] in selected:
            del state.tables["catalog_sighting"][key]
        elif plan["reset"]:
            row["seen_count"] = 0
    if plan["reset"]:
        for key, tracker in state.tables["service_state"].items():
            if key.startswith("catalog_playback:") and (pending := tracker.pop("pending", None)):
                tracker.update(
                    last=pending["identity"],
                    last_raw=pending["key"],
                    last_session=pending["session"],
                )
        state.tables["service_state"]["catalog_counter_epoch"] = {
            "at": runtime.clock.now_utc().isoformat(),
            "request_id": request_id,
        }
    result = {
        "removed": len(selected),
        "reset_counters": plan["reset"],
        "catalog_count": plan["catalog_count"],
        "request_id": request_id,
    }
    state.tables["catalog_request"][request_id] = {
        "digest": digest,
        "result": result,
        "actor": body.get("_actor"),
        "at": runtime.clock.now_utc().isoformat(),
    }
    return result
