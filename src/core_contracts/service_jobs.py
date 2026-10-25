"""Persisted intent before network I/O; results belong to their original generation."""

from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .calendar_cache import normalize_events
from .evidence import Observation
from .model import checksum
from .persistence import State
from .registry import Binding, RegistryConfig


def calendar_error_code(value: Any) -> str:
    """Expose only fixed transport codes, never provider text or calendar contents."""
    return (
        value
        if isinstance(value, str)
        and value
        in {
            "invalid_arguments",
            "not_allowed",
            "unsupported_operation",
            "ha_unavailable",
            "request_conflict",
            "busy",
            "timeout",
            "calendar_response_invalid",
        }
        else "calendar_fetch_failed"
    )


def calendar_status(state: State, config: RegistryConfig | None) -> list[dict[str, Any]]:
    """Cache metadata for the source view, deliberately excluding event data."""
    return [
        {
            "binding_id": binding.binding_id,
            "source_id": binding.source_id,
            "fetched_at": row.get("fetched_at"),
            "expires_at": row.get("expires_at"),
            "error_code": calendar_error_code(row.get("error_code"))
            if row.get("failures")
            else None,
        }
        for binding in (config.bindings if config else [])
        if binding.adapter.kind == "ha_calendar"
        for row in [state.tables["service_state"].get(f"calendar:{binding.binding_id}", {})]
    ]


def calendar_key(
    binding: Binding, config: RegistryConfig, now: datetime
) -> tuple[str, datetime, datetime]:
    adapter = binding.adapter
    assert adapter.window
    start, end = adapter.window.bounds(now)
    source = next(source for source in config.sources if source.source_id == binding.source_id)
    return (
        checksum(
            {
                "binding": binding.model_dump(mode="json"),
                "source": source.model_dump(mode="json"),
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        ),
        start,
        end,
    )


def plan_calendars(state: State, config: RegistryConfig, now: datetime) -> None:
    rows, jobs = state.tables["service_state"], state.tables["service_job"]
    active = set()
    for binding in config.bindings:
        adapter = binding.adapter
        if adapter.kind != "ha_calendar":
            continue
        identity = f"calendar:{binding.binding_id}"
        active.add(identity)
        key, start, end = calendar_key(binding, config, now)
        row = rows.get(identity, {})
        if row.get("cache_key") != key:
            zone = ZoneInfo("Europe/Berlin")
            tomorrow = now.astimezone(zone).date() + timedelta(days=1)
            row = rows[identity] = {
                "cache_key": key,
                "binding_id": binding.binding_id,
                "next_at": now.isoformat(),
                "day_change_at": datetime.combine(tomorrow, time(), zone).isoformat(),
                "failures": 0,
                "failure_warning_emitted": row.get("failure_warning_emitted", False),
            }
        job = jobs.get(row.get("job_id", ""), {})
        if (
            job
            and job.get("revision") == state.active_revision
            and job["status"] in {"planned", "sent"}
        ):
            continue
        if datetime.fromisoformat(row["next_at"]) > now:
            continue
        identifier = str(uuid4())
        jobs[identifier] = {
            "job_id": identifier,
            "request_id": identifier,
            "kind": "calendar",
            "state_key": identity,
            "cache_key": key,
            "binding_id": binding.binding_id,
            "revision": state.active_revision,
            "operation": "calendar_events",
            "target_ids": [adapter.ha_entity_id],
            "arguments": {"start": start.isoformat(), "end": end.isoformat()},
            "status": "planned",
            "created_at": now.isoformat(),
        }
        row["job_id"] = identifier
    for identity in list(rows):
        if identity.startswith("calendar:") and identity not in active:
            del rows[identity]
    for job in jobs.values():
        if job["kind"] != "calendar":
            continue
        row = rows.get(job["state_key"], {})
        if job["status"] in {"planned", "sent"} and (
            job["revision"] != state.active_revision or row.get("job_id") != job["job_id"]
        ):
            job["status"] = "cancelled"
    finished = [
        identifier for identifier, job in jobs.items() if job["status"] not in {"planned", "sent"}
    ]
    for identifier in finished[:-256]:
        del jobs[identifier]


def complete_calendar(
    state: State,
    config: RegistryConfig,
    job: dict[str, Any],
    response: dict[str, Any],
    now: datetime,
) -> Observation | None:
    row = state.tables["service_state"].get(job["state_key"], {})
    binding = next(
        (binding for binding in config.bindings if binding.binding_id == job["binding_id"]), None
    )
    if (
        binding is None
        or job["revision"] != state.active_revision
        or row.get("job_id") != job["job_id"]
    ):
        return None
    key, start, end = calendar_key(binding, config, now)
    if key != job["cache_key"]:
        return None
    adapter = binding.adapter
    assert adapter.window and adapter.ha_entity_id and adapter.refresh_s and adapter.max_age_s
    observation = None
    if response.get("outcome") == "completed":
        try:
            events = normalize_events(
                response.get("data"), adapter.ha_entity_id, start, end, adapter.window.timezone
            )
            row.update(
                {
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                    "fetched_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=adapter.max_age_s)).isoformat(),
                    "events": events,
                    "failures": 0,
                    "failure_warning_emitted": False,
                    "next_at": (now + timedelta(seconds=adapter.refresh_s)).isoformat(),
                }
            )
            source = next(
                source for source in config.sources if source.source_id == binding.source_id
            )
            observation = Observation.model_validate(
                {
                    "source_id": source.source_id,
                    "binding_id": binding.binding_id,
                    "adapter": "ha_calendar",
                    "source_origin": source.source_origin,
                    "value_raw": {
                        key: row[key]
                        for key in (
                            "window_start",
                            "window_end",
                            "fetched_at",
                            "expires_at",
                            "events",
                        )
                    },
                    "device_time": now,
                    "received_at": now,
                    "observation_kind": "report",
                    "epoch_id": state.epoch_id,
                    "ingest_seq": state.generation + 1,
                    "reference_version": 2 if config.fingerprint_version >= 2 else 1,
                    "registry_revision": state.active_revision,
                }
            )
        except ValueError, TypeError, KeyError, OverflowError:
            response = {"outcome": "failed", "error_code": "calendar_response_invalid"}
    if observation is None:
        row["failures"] = min(int(row.get("failures", 0)) + 1, 4)
        delay = (5, 30, 120, adapter.refresh_s)[row["failures"] - 1]
        row["next_at"] = (now + timedelta(seconds=delay)).isoformat()
    # Diagnostic only: never store raw errors, calendar contents, or source timestamps from ACKs.
    row["last_outcome"] = response.get("outcome", "indeterminate")
    row["error_code"] = (
        calendar_error_code(response.get("error_code")) if observation is None else None
    )
    return observation


def restore_jobs(state: State, now: datetime) -> None:
    for job in state.tables["service_job"].values():
        if job["kind"] == "refresh" and job["status"] in {"planned", "sent"}:
            job["status"] = "indeterminate" if job["status"] == "sent" else "cancelled"
            continue
        if job["status"] == "sent":
            job["status"] = "indeterminate"
            row = state.tables["service_state"].get(job["state_key"])
            if row and row.get("job_id") == job["job_id"]:
                row["next_at"] = (now + timedelta(seconds=5)).isoformat()
                if job["kind"] == "notice":
                    row["retry_at"] = (now + timedelta(seconds=30)).isoformat()


def due_dates(state: State) -> list[datetime]:
    dates: list[datetime] = []
    for row in state.tables["service_state"].values():
        dates.extend(datetime.fromisoformat(value) for value in row.get("deadlines", []))
        for name in ("next_at", "expires_at", "day_change_at"):
            if row.get(name):
                dates.append(datetime.fromisoformat(row[name]))
    return dates
