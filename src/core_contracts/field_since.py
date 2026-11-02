"""Publication-level value intervals; producer-owned clocks retain their meaning."""

from datetime import datetime
from typing import Any

from .numeric_equivalence import equivalent
from .quality import FieldValue


def track_fields(
    fields: dict[str, FieldValue],
    previous: dict[str, Any],
    now: datetime,
    *,
    compatible: bool,
    restoring: bool,
) -> dict[str, FieldValue]:
    result = {}
    for name, field in fields.items():
        if field.since_at is not None:
            result[name] = field
            continue
        old = previous.get(name, {}) if compatible else {}
        same = old.get("status") == field.status and equivalent(old.get("value"), field.value)
        since = None
        if same and old.get("since_at"):
            try:
                candidate = datetime.fromisoformat(old["since_at"])
                if candidate.tzinfo is not None and candidate <= now:
                    since = candidate
            except TypeError, ValueError:
                pass
        # A restore interruption proves neither a fresh value nor the onset of
        # a newly held/unknown interval. A later genuine evaluation starts it.
        if restoring and field.status != "valid":
            if field.status != "held" or not same:
                since = None
        elif since is None and not (
            same
            and old.get("since_at") is None
            and field.status in {"unknown", "held", "unresolved"}
        ):
            since = now
        result[name] = field.model_copy(update={"since_at": since})
    return result
