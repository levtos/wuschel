"""Nonsemantic catalog observation; never schedules or batches contract evaluation."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from core_contracts import slots
from core_contracts.evidence import Observation
from core_contracts.model import checksum
from core_contracts.persistence import State
from core_contracts.quality import FieldValue

if TYPE_CHECKING:
    from core_contracts.runtime import Runtime

import json

from core_contracts.extensions.catalog.managed_catalog import head_key, identity

PREFIX = "catalog_playback:"
STABLE_SECONDS = 2
TRANSITION_SECONDS = 10  # Reported split updates take up to eight seconds, plus stability.


def end(tracker: dict[str, Any]) -> None:
    # Keep the previous pair as the reference for split-attribute transitions.
    for key in ("pending", "last", "last_session", "report"):
        tracker.pop(key, None)


def live_input(observation: Observation) -> bool:
    return observation.observation_kind in {"live_change", "report"} and not (
        observation.ha_restored
        or observation.mqtt_retained
        or observation.assumed_state
        or observation.invalid_timestamp
    )


def finish(
    state: Any, contract: str, fields: dict[str, FieldValue], origin: str, event: Observation | None
) -> None:
    """Observe positive non-applicability after the existing resolver has finished."""
    if (
        origin != "input"
        or event is None
        or not live_input(event)
        or not fields
        or not all(
            field.status == "not_applicable" and field.positively_not_applicable
            for field in fields.values()
        )
    ):
        return
    for name, tracker in state.tables["service_state"].items():
        consumers = tracker.get("consumers", {})
        if name.startswith(PREFIX) and contract in consumers:
            consumers[contract] = False
            if not any(consumers.values()):
                end(tracker)


def baseline_pending(state: Any) -> None:
    """A gap/restart cannot turn an unfinished candidate into a new playback."""
    for name, tracker in state.tables["service_state"].items():
        if name.startswith(PREFIX) and (pending := tracker.pop("pending", None)):
            tracker.update(
                last=pending["identity"], last_raw=pending["key"], last_session=pending["session"]
            )


def frame(state: Any, observation: Observation) -> None:
    for name, tracker in state.tables["service_state"].items():
        if (
            name.startswith(PREFIX)
            and observation.observation_kind == "snapshot"
            and observation.binding_id in tracker.get("bindings", [])
            and (pending := tracker.pop("pending", None))
        ):
            tracker.update(
                last=pending["identity"], last_raw=pending["key"], last_session=pending["session"]
            )
        if not name.startswith(PREFIX) or not live_input(observation):
            continue
        title_ended = observation.binding_id in tracker.get("title_bindings", []) and (
            observation.availability != "available"
            or observation.value is None
            or isinstance(observation.value, str)
            and not observation.value.strip()
        )
        player_ended = observation.binding_id in tracker.get("bindings", []) and (
            observation.catalog_frame or {}
        ).get("state") in {"off", "idle"}
        if title_ended or player_ended:
            end(tracker)
    if (
        observation.availability != "available"
        or observation.transform_error
        or observation.ha_restored
        or observation.mqtt_retained
        or observation.assumed_state
        or observation.invalid_timestamp
    ):
        for name, tracker in state.tables["service_state"].items():
            if name.startswith(PREFIX) and observation.binding_id in tracker.get("bindings", []):
                tracker.pop("pending", None)
    value = observation.catalog_frame
    if not value:
        return
    # Display metadata is durable independently of playback counting. A later
    # name report, pause or snapshot must not require another playback.
    app_id, app_name = value.get("app_id"), value.get("app_name")
    if app_id and isinstance(app_name, str) and app_name.strip():
        app_name = app_name.strip()[:256]
        name_key = "catalog_app_name:" + checksum(app_id)
        if state.tables["service_state"].get(name_key, {}).get("display_name") != app_name:
            state.tables["service_state"][name_key] = {"display_name": app_name}
            for row in state.tables["catalog_sighting"].values():
                if row.get("signal_type") == "app" and row["key"] == app_id:
                    row["display_name"] = app_name
    key = "catalog_frame:" + value["source"]
    old = state.tables["service_state"].get(key, {})
    if old.get("event") == value["event"]:
        return
    session = old.get("session", 0)
    live = observation.observation_kind in {"live_change", "report"} and not (
        observation.ha_restored or observation.mqtt_retained
    )
    position, duration = value.get("position"), old.get("duration")
    repeat = (
        live
        and old.get("state") == value.get("state") == "playing"
        and isinstance(position, (int, float))
        and isinstance(old.get("position"), (int, float))
        and isinstance(duration, (int, float))
        and duration > 3
        and old["position"] >= duration - 3
        and position <= 3
        and value.get("position_at") != old.get("position_at")
    )
    if live and value.get("state") == "playing" and (old.get("state") in {"idle", "off"} or repeat):
        session += 1
    state.tables["service_state"][key] = {**value, "session": session}


def offer(
    ctx: Any,
    catalog: Any,
    scope: str,
    media: str,
    signal: str,
    raw: str,
    evidence: tuple[str, ...],
    expires: datetime | None = None,
    *,
    hierarchy: dict[str, Any] | None = None,
) -> None:
    references = set(evidence)
    observations = [
        item
        for row in ctx.state.tables["source_observation_current"].values()
        if (item := Observation.model_validate(row)).reference in references
    ]
    if not observations:
        return
    framed = [item for item in observations if item.catalog_frame]
    selected = max(framed or observations, key=lambda item: item.ingest_seq)
    selected_frame = (
        selected.catalog_frame if hierarchy or media == "music" and signal == "title" else None
    )
    source = (
        selected_frame["source"]
        if selected_frame
        else checksum(sorted(item.binding_id for item in observations))
    )
    if selected_frame:
        # Wait only inside the catalog tracker until the lookup's HA inputs belong
        # to one event. Preserve the resolver's transformed values and tuple shape.
        if len({item.catalog_frame["event"] for item in framed if item.catalog_frame}) > 1:
            return
    stream = PREFIX + checksum([head_key(catalog), scope, media, signal, source])
    ctx.state.detach_rows("service_state", [stream])
    old = ctx.state.tables["service_state"].get(stream, {})
    consumers = (
        dict(old.get("consumers", {}))
        if old.get("consumer_revision") == ctx.state.active_revision
        else {}
    )
    consumers[ctx.contract.contract_id] = True
    title_evidence = set(ctx.inputs["title"].evidence) if "title" in ctx.inputs else references
    old = {
        **old,
        "consumers": consumers,
        "consumer_revision": ctx.state.active_revision,
        "title_bindings": sorted(
            item.binding_id for item in observations if item.reference in title_evidence
        ),
        "bindings": sorted(item.binding_id for item in observations),
    }
    ctx.state.tables["service_state"][stream] = old
    if not raw.strip():
        if ctx.origin == "input":
            end(old)
        return
    key = identity(scope, media, signal, raw)
    if hierarchy:
        row_key = head_key(catalog) + ":" + key
        if existing := ctx.state.tables["catalog_sighting"].get(row_key):
            ctx.state.detach_rows("catalog_sighting", [row_key])
            ctx.state.tables["catalog_sighting"][row_key] = {
                **existing,
                "display_metadata": hierarchy["display_metadata"],
                "display_name": hierarchy["display_metadata"].get("title", ""),
                **(
                    {"catalog_metadata": hierarchy["catalog_metadata"]}
                    if "catalog_metadata" in hierarchy
                    else {}
                ),
            }
    latest_frame = ctx.state.tables["service_state"].get("catalog_frame:" + source, {})
    session = latest_frame.get("session", 0)
    eligible = [
        item
        for item in observations
        if item.observation_kind in {"live_change", "report"}
        and not (
            item.mqtt_retained
            or item.ha_restored
            or item.assumed_state
            or item.invalid_timestamp
            or item.availability != "available"
        )
    ]
    if ctx.origin != "input" or not eligible:
        # A snapshot establishes a baseline, never a new playback.
        if ctx.origin in {"snapshot", "restore"} or (
            ctx.origin == "revision" and selected.observation_kind == "snapshot"
        ):
            ctx.state.tables["service_state"][stream] = {
                **old,
                "last": key,
                "last_raw": raw,
                "last_session": session,
                "source": source,
            }
            ctx.state.tables["service_state"][stream].pop("pending", None)
        return
    report = checksum(sorted(item.reference for item in eligible))
    if old.get("report") == report:
        return
    tracker = {
        **old,
        "report": report,
        "source": source,
        "bindings": sorted(item.binding_id for item in observations),
    }
    ctx.state.tables["service_state"][stream] = tracker
    if latest_frame.get("state") in {"paused", "idle", "off", "unavailable", "unknown"}:
        tracker.pop("pending", None)
        return
    if old.get("last") == key and old.get("last_session", 0) == session:
        tracker.pop("pending", None)
        return
    pending = old.get("pending", {})
    same_candidate = pending.get("hierarchy") == hierarchy
    if hierarchy and hierarchy.get("profile") == "apple_tv_title":
        same_candidate = (pending.get("hierarchy") or {}).get("fingerprint") == hierarchy[
            "fingerprint"
        ]
    if pending.get("identity") == key and pending.get("session") == session and same_candidate:
        pending["expires"] = expires.isoformat() if expires else None
        if hierarchy:
            pending["hierarchy"] = hierarchy
            pending["display_name"] = hierarchy["display_metadata"].get("title", "")
        return
    delay = hierarchy.get("stability_s", STABLE_SECONDS) if hierarchy else STABLE_SECONDS
    if (
        hierarchy
        and (pair := hierarchy.get("transition_pair"))
        and (before_pair := old.get("last_pair"))
    ):
        if (
            before_pair[0] == pair[0]
            and sum(a != b for a, b in zip(before_pair[1:], pair[1:], strict=True)) == 1
        ):
            delay = max(delay, TRANSITION_SECONDS)
    if media == "music" and old.get("last_raw"):
        try:
            before, after = json.loads(old["last_raw"]), json.loads(raw)
        except ValueError, RecursionError:
            before, after = [], []
        if (
            isinstance(before, list)
            and isinstance(after, list)
            and len(before) == len(after) == 2
            and sum(a != b for a, b in zip(before, after)) == 1
        ):
            delay = TRANSITION_SECONDS
    tracker["pending"] = {
        **({"hierarchy": hierarchy} if hierarchy else {}),
        "catalog": head_key(catalog),
        "identity": key,
        "scope": scope,
        "media_type": media,
        "signal_type": signal,
        "key": raw,
        "display_name": (hierarchy["display_metadata"].get("title", "") if hierarchy else "")
        or next(
            (
                item.catalog_frame["app_name"]
                for item in observations
                if item.catalog_frame
                and item.catalog_frame.get("app_id") == raw
                and item.catalog_frame.get("app_name")
            ),
            "",
        ),
        "session": session,
        "source": source,
        "due": (ctx.now + timedelta(seconds=delay)).isoformat(),
        "observed_at": ctx.now.isoformat(),
        "epoch": ctx.state.epoch_id,
        "active_revision": ctx.state.active_revision,
        "expires": expires.isoformat() if expires else None,
    }


def flush(state: Any, now: datetime) -> None:
    for name, tracker in state.tables["service_state"].items():
        if not name.startswith(PREFIX) or not (pending := tracker.get("pending")):
            continue
        if (
            pending["epoch"] != state.epoch_id
            or pending["active_revision"] != state.active_revision
        ):
            tracker.pop("pending", None)
            continue
        if pending.get("expires") and datetime.fromisoformat(pending["expires"]) <= now:
            tracker.pop("pending", None)
            continue
        if datetime.fromisoformat(pending["due"]) > now:
            continue
        current_frame = state.tables["service_state"].get("catalog_frame:" + pending["source"], {})
        if hierarchy := pending.get("hierarchy"):
            if hierarchy.get("profile") == "apple_tv_title":
                candidate = current_frame.get("title_candidate") or {}
                matches = (
                    candidate.get("key") == pending["key"]
                    and candidate.get("fingerprint") == hierarchy["fingerprint"]
                )
            else:
                from core_contracts.extensions.catalog.stash_catalog import studio_key

                matches = (
                    current_frame.get("catalog_profile") == "stash_slot"
                    and current_frame.get("scene_id") == pending["key"]
                    and bool(current_frame.get("studio"))
                    and studio_key(current_frame["studio"]) == hierarchy["master_key"]
                )
            if not matches:
                tracker.pop("pending", None)
                continue
        if current_frame.get("state") in {"paused", "idle", "off", "unavailable", "unknown"}:
            tracker.pop("pending", None)
            continue
        sighting_key = pending["catalog"] + ":" + pending["identity"]
        hierarchy = pending.get("hierarchy")
        if hierarchy and hierarchy.get("master_key"):
            parent = identity(
                pending["scope"],
                pending["media_type"],
                pending["signal_type"],
                hierarchy["master_key"],
            )
            parent_key = pending["catalog"] + ":" + parent
            if parent_key not in state.tables["catalog_sighting"]:
                state.tables["catalog_sighting"][parent_key] = {
                    **{
                        key: pending[key]
                        for key in ("catalog", "scope", "media_type", "signal_type", "source")
                    },
                    "identity": parent,
                    "key": hierarchy["master_key"],
                    "display_name": hierarchy["master_display_name"],
                    "first_seen": pending["observed_at"],
                    "last_seen": pending["observed_at"],
                    "seen_count": 0,
                    "hierarchy_master": True,
                    **(
                        {"catalog_metadata": hierarchy["catalog_metadata"]}
                        if "catalog_metadata" in hierarchy
                        else {}
                    ),
                }
        previous = state.tables["catalog_sighting"].get(sighting_key, {})
        tombstone = (
            state.tables["catalog_head"]
            .get(pending["catalog"], {})
            .get("entries", {})
            .get(pending["identity"], {})
            .get("tombstone", False)
        )
        state.tables["catalog_sighting"][sighting_key] = {
            **(
                {
                    "master_key": hierarchy["master_key"],
                    "master_identity": parent,
                }
                if hierarchy and hierarchy.get("master_key")
                else {}
            ),
            **(
                {
                    "display_metadata": hierarchy["display_metadata"],
                    **(
                        {"catalog_metadata": hierarchy["catalog_metadata"]}
                        if "catalog_metadata" in hierarchy
                        else {}
                    ),
                }
                if hierarchy
                else {}
            ),
            **{
                k: pending[k]
                for k in (
                    "catalog",
                    "identity",
                    "scope",
                    "media_type",
                    "signal_type",
                    "key",
                    "source",
                )
            },
            "first_seen": previous.get("first_seen", pending["observed_at"]),
            "last_seen": pending["observed_at"],
            "seen_count": previous.get("seen_count", 0) + 1,
            "report": tracker["report"],
            "display_name": pending.get("display_name") or previous.get("display_name", ""),
            **({"after_removal": True} if tombstone else {}),
        }
        tracker.update(
            last=pending["identity"], last_raw=pending["key"], last_session=pending["session"]
        )
        if hierarchy and hierarchy.get("transition_pair"):
            tracker["last_pair"] = hierarchy["transition_pair"]
        else:
            tracker.pop("last_pair", None)
        tracker.pop("pending", None)


def next_due(state: Any, now: datetime) -> datetime | None:
    return min(
        (
            datetime.fromisoformat(row["pending"]["due"])
            for key, row in state.tables["service_state"].items()
            if key.startswith(PREFIX)
            and row.get("pending")
            and row["pending"]["epoch"] == state.epoch_id
            and row["pending"]["active_revision"] == state.active_revision
        ),
        default=None,
    )


async def restore_baseline(runtime: Runtime, state: State, context: Any) -> None:
    baseline_pending(state)


slots.RESTORE[20] = slots.Hook(restore_baseline, "migration", "restore_catalog_baseline")
slots.RESTORE[25] = slots.Hook(restore_baseline, "gap")


slots.OBSERVATION[30] = slots.Hook(frame, "frame")
slots.AFTER_EVALUATE[20] = slots.Hook(finish, "contract")


def plan_deadline(runtime: Runtime, state: State) -> None:
    if runtime.catalog_alarm:
        runtime.catalog_alarm.cancel()
    catalog_due = next_due(state, runtime.clock.now_utc())
    if catalog_due and not runtime.stopping:
        runtime.catalog_alarm = runtime.clock.call_at_utc(
            catalog_due, lambda: runtime._queue_operation("catalog_tick")
        )


slots.NEXT_DUE[20] = slots.Hook(plan_deadline, "commit")
