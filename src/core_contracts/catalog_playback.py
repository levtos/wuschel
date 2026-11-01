"""Nonsemantic catalog observation; never schedules or batches contract evaluation."""

import json
from datetime import datetime, timedelta
from typing import Any

from .evidence import Observation
from .managed_catalog import head_key, identity
from .model import checksum

PREFIX = "catalog_playback:"
STABLE_SECONDS = 2
TRANSITION_SECONDS = 10  # Reported split updates take up to eight seconds, plus stability.


def baseline_pending(state: Any) -> None:
    """A gap/restart cannot turn an unfinished candidate into a new playback."""
    for name, tracker in state.tables["service_state"].items():
        if name.startswith(PREFIX) and (pending := tracker.pop("pending", None)):
            tracker.update(
                last=pending["identity"], last_raw=pending["key"], last_session=pending["session"]
            )


def frame(state: Any, observation: Observation) -> None:
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
    selected_frame = selected.catalog_frame if media == "music" and signal == "title" else None
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
    old = ctx.state.tables["service_state"].get(stream, {})
    key = identity(scope, media, signal, raw)
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
        if ctx.origin in {"snapshot", "restore"}:
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
    if pending.get("identity") == key and pending.get("session") == session:
        pending["expires"] = expires.isoformat() if expires else None
        return
    delay = STABLE_SECONDS
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
        "catalog": head_key(catalog),
        "identity": key,
        "scope": scope,
        "media_type": media,
        "signal_type": signal,
        "key": raw,
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
        if current_frame.get("state") in {"paused", "idle", "off", "unavailable", "unknown"}:
            tracker.pop("pending", None)
            continue
        sighting_key = pending["catalog"] + ":" + pending["identity"]
        previous = state.tables["catalog_sighting"].get(sighting_key, {})
        state.tables["catalog_sighting"][sighting_key] = {
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
        }
        tracker.update(
            last=pending["identity"], last_raw=pending["key"], last_session=pending["session"]
        )
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
