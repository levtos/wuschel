"""Optional holiday coverage probe, using the existing bounded read-only channel.

Every request stays within ADR 0013's 31 day/1000 event/1 MiB limits.
Only coverage dates survive; distant appointment contents never enter the fact cache.
"""

from calendar import monthrange
from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .calendar_cache import normalize_events
from .model import checksum
from .quality import FieldValue, Reason, ReasonCode, unknown

PREFIX = "calendar_coverage:"


def add_months(value: datetime, months: int) -> datetime:
    local = value.astimezone(ZoneInfo("Europe/Berlin"))
    year, month = divmod(local.year * 12 + local.month - 1 + months, 12)
    return local.replace(
        year=year, month=month + 1, day=min(local.day, monthrange(year, month + 1)[1])
    )


def identity(binding: Any, months: int) -> str:
    return checksum(
        {
            "binding": binding.model_dump(mode="json", exclude={"display_name"}),
            "months": months,
            "check_version": 2,
        }
    )


def horizon(now: datetime, months: int) -> tuple[datetime, datetime]:
    local = now.astimezone(ZoneInfo("Europe/Berlin"))
    start = datetime.combine(local.date(), time(), local.tzinfo)
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    return start, start.replace(
        year=year, month=month + 1, day=min(start.day, monthrange(year, month + 1)[1])
    )


def plan(state: Any, config: Any, now: datetime) -> None:
    rows, jobs = state.tables["service_state"], state.tables["service_job"]
    active = set()
    for source in config.sources:
        if not source.calendar_coverage_months:
            continue
        binding = next(
            b
            for b in config.bindings
            if b.source_id == source.source_id and b.adapter.kind == "ha_calendar"
        )
        key = PREFIX + binding.binding_id
        active.add(key)
        signature = identity(binding, source.calendar_coverage_months)
        start, end = horizon(now, source.calendar_coverage_months)
        row = rows.get(key, {})
        if row.get("signature") != signature:
            row = rows[key] = {"signature": signature, "binding_id": binding.binding_id}
        # A changed day invalidates an unfinished sweep; successful evidence has
        # its own absolute expiry and cannot be renewed by a restart/reconnect.
        if (
            row.get("scan_day") != start.isoformat()
            or row.get("complete")
            and datetime.fromisoformat(row["next_at"]) <= now
        ):
            row.update(
                scan_day=start.isoformat(),
                cursor=start.isoformat(),
                horizon=end.isoformat(),
                complete=False,
                scan_last_end=None,
                gap=None,
                next_at=now.isoformat(),
            )
        job = jobs.get(row.get("job_id", ""), {})
        if (
            job.get("status") in {"planned", "sent"}
            and job.get("revision") == state.active_revision
            and job.get("scan_day") == row["scan_day"]
        ):
            continue
        if row.get("complete") or datetime.fromisoformat(row["next_at"]) > now:
            continue
        cursor = datetime.fromisoformat(row["cursor"])
        stop = min(cursor + timedelta(days=31), end)
        identifier = str(uuid4())
        jobs[identifier] = {
            "job_id": identifier,
            "request_id": identifier,
            "kind": "calendar",
            "coverage": True,
            "state_key": key,
            "cache_key": signature,
            "scan_day": row["scan_day"],
            "binding_id": binding.binding_id,
            "revision": state.active_revision,
            "operation": "calendar_events",
            "target_ids": [binding.adapter.ha_entity_id],
            "arguments": {"start": cursor.isoformat(), "end": stop.isoformat()},
            "status": "planned",
            "created_at": now.isoformat(),
        }
        row["job_id"] = identifier
    for key in list(rows):
        if key.startswith(PREFIX) and key not in active:
            del rows[key]
    for job in jobs.values():
        if job.get("coverage") and job["status"] in {"planned", "sent"}:
            row = rows.get(job["state_key"], {})
            if job["revision"] != state.active_revision or row.get("job_id") != job["job_id"]:
                job["status"] = "cancelled"


def complete(
    state: Any, config: Any, job: dict[str, Any], response: dict[str, Any], now: datetime
) -> None:
    row = state.tables["service_state"].get(job["state_key"], {})
    binding = next((b for b in config.bindings if b.binding_id == job["binding_id"]), None)
    if (
        binding is None
        or job["revision"] != state.active_revision
        or row.get("job_id") != job["job_id"]
    ):
        return
    source = next(s for s in config.sources if s.source_id == binding.source_id)
    if (
        not source.calendar_coverage_months
        or identity(binding, source.calendar_coverage_months) != job["cache_key"]
        or horizon(now, source.calendar_coverage_months)[0].isoformat() != job["scan_day"]
    ):
        return
    try:
        if response.get("outcome") != "completed":
            raise ValueError("calendar_fetch_failed")
        start, end = (datetime.fromisoformat(job["arguments"][key]) for key in ("start", "end"))
        events = normalize_events(
            response.get("data"), binding.adapter.ha_entity_id, start, end, "Europe/Berlin"
        )
        future = [event for event in events if datetime.fromisoformat(event["end"]) > now]
        previous = (
            datetime.fromisoformat(row["scan_last_end"]) if row.get("scan_last_end") else None
        )
        for event in future:
            event_start, event_end = (
                datetime.fromisoformat(event[key]) for key in ("start", "end")
            )
            if previous and event_start > add_months(previous, 4) and row.get("gap") is None:
                row["gap"] = {"start": previous.isoformat(), "end": event_start.isoformat()}
            previous = max(previous, event_end) if previous else event_end
        row["scan_last_end"] = previous.isoformat() if previous else None
        if end >= datetime.fromisoformat(row["horizon"]):
            if previous and end > add_months(previous, 4) and row.get("gap") is None:
                row["gap"] = {"start": previous.isoformat(), "end": end.isoformat()}
            row.update(
                complete=True,
                covered=previous is not None and row.get("gap") is None,
                evidence_end=previous.isoformat() if previous else None,
                checked_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=binding.adapter.max_age_s)).isoformat(),
                next_at=(now + timedelta(seconds=binding.adapter.refresh_s)).isoformat(),
            )
        else:
            row.update(cursor=end.isoformat(), next_at=now.isoformat())
        row.update(failures=0, error_code=None, failure_warning_emitted=False)
    except ValueError, TypeError, KeyError, OverflowError:
        row["failures"] = min(row.get("failures", 0) + 1, 4)
        row.update(
            error_code="calendar_fetch_failed",
            next_at=(
                now
                + timedelta(seconds=(5, 30, 120, binding.adapter.refresh_s)[row["failures"] - 1])
            ).isoformat(),
        )


def status(state: Any, binding: Any, months: int, now: datetime) -> dict[str, Any]:
    row = state.tables["service_state"].get(PREFIX + binding.binding_id, {})
    valid = (
        row.get("signature") == identity(binding, months)
        and row.get("complete")
        and row.get("expires_at")
        and datetime.fromisoformat(row["expires_at"]) > now
    )
    covered = bool(
        valid
        and row.get("covered")
        and row.get("evidence_end")
        and datetime.fromisoformat(row["evidence_end"]) > now
    )
    expired = bool(valid and row.get("complete") and not row.get("covered"))
    cache = state.tables["service_state"].get("calendar:" + binding.binding_id, {})
    last = cache.get("last_event_end")
    date = (
        (
            datetime.fromisoformat(last).astimezone(ZoneInfo("Europe/Berlin"))
            - timedelta(microseconds=1)
        ).strftime("%d.%m.%Y")
        if last
        else None
    )
    message = (
        (f"Feiertagskalender endet am {date}." if date else "Feiertagskalender: Ende unbekannt.")
        if expired
        else "Feiertagskalender: Abdeckung wird geprüft oder ist nicht mehr belegt."
    )
    if valid and row.get("gap"):
        dates = [
            datetime.fromisoformat(row["gap"][key])
            .astimezone(ZoneInfo("Europe/Berlin"))
            .strftime("%d.%m.%Y")
            for key in ("start", "end")
        ]
        message = f"Feiertagskalender: Lücke vom {dates[0]} bis {dates[1]} (mehr als 4 Monate)."
    return {
        "months": months,
        "covered": covered,
        "complete": expired or covered,
        "last_event_end": last,
        "gap": row.get("gap") if valid else None,
        "message": None if covered else message,
    }


def assess(value: FieldValue, state: Any, binding: Any, months: int, now: datetime) -> FieldValue:
    coverage = status(state, binding, months, now)
    if coverage["covered"]:
        return value
    result = unknown(ReasonCode.CALENDAR_COVERAGE, binding.source_id, now)
    return result.model_copy(
        update={
            "reasons": (
                Reason(
                    code=ReasonCode.CALENDAR_COVERAGE,
                    input=binding.source_id,
                    since=now,
                    detail=coverage["message"],
                ),
            )
        }
    )
