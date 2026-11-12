"""Explicit administrative observations, atomically committed and repeat safe."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from .model import Model, utc
from .value_origin import ValueOrigin


class ManualEvidence(Model):
    request_id: str
    source_fingerprint: str
    recorded_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    actor: dict[str, str]
    note: str = Field(default="", max_length=512)
    revoked: bool = False

    _times = field_validator("recorded_at", "valid_from", "valid_until")(
        lambda value: utc(value) if value else value
    )


class ManualRequest(Model):
    request_id: UUID
    expected_active_revision: int = Field(ge=1, strict=True)
    action: Literal["set", "retract"] = "set"
    value: Any = None
    value_origin: ValueOrigin | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    confirm_unbounded: bool = Field(default=False, strict=True)
    note: str = Field(default="", max_length=512)

    _times = field_validator("valid_from", "valid_until")(
        lambda value: utc(value) if value else value
    )

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value: Any) -> Any:
        from .model import canonical

        def visit(item: Any, depth: int) -> None:
            if depth > 16 or isinstance(item, (dict, list)) and len(item) > 256:
                raise ValueError("manual_value_too_large")
            for child in (
                item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
            ):
                visit(child, depth + 1)

        visit(value, 0)
        if len(canonical(value).encode()) > 65536:
            raise ValueError("manual_value_too_large")
        return value
