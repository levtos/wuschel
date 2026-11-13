"""Pure wake exception format and local clock parsing."""

import re
from datetime import date, time
from typing import Any, Self

from pydantic import Field, model_validator

from ..model import Model


def parse_time(value: Any) -> time | None:
    """Parse a local ``HH:MM`` value without accepting invalid clock times."""

    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except TypeError, ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def time_text(value: str) -> str:
    parsed = parse_time(value)
    if parsed is None or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("wake_time_invalid")
    return value


class WakeException(Model):
    date: str
    wake_time: str | None = None
    skip: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        date.fromisoformat(self.date)
        if self.skip == (self.wake_time is not None):
            raise ValueError("exception_time_or_skip")
        if self.wake_time is not None:
            time_text(self.wake_time)
        return self
