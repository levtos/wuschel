"""Declared value origin and half-open validity, independent of transport freshness."""

from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from .model import Model, canonical, utc


class OriginDeclaration(Model):
    kind: Literal["measured", "replacement", "model", "forecast"]
    provider: str = Field(default="", max_length=128)


class ValueOrigin(OriginDeclaration):
    issued_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    based_on: tuple[str, ...] = Field(default=(), max_length=64)

    _times = field_validator("issued_at", "valid_from", "valid_until")(
        lambda value: utc(value) if value else value
    )

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.valid_until and self.valid_from and self.valid_until <= self.valid_from:
            raise ValueError("invalid_value_validity")
        if self.kind == "forecast" and not all((self.issued_at, self.valid_from, self.valid_until)):
            raise ValueError("forecast_interval_required")
        return self

    def includes(self, now: datetime) -> bool:
        return (self.valid_from is None or self.valid_from <= now) and (
            self.valid_until is None or now < self.valid_until
        )


class ForecastPoint(Model):
    value: Any
    value_origin: ValueOrigin

    @model_validator(mode="after")
    def forecast(self) -> Self:
        if (
            self.value_origin.kind != "forecast"
            or self.value is None
            or len(canonical(self.value).encode()) > 65536
        ):
            raise ValueError("invalid_forecast_point")
        return self


def forecast_points(raw: Any) -> tuple[ForecastPoint, ...]:
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 512:
        raise ValueError("invalid_forecast_series")
    if (
        len(
            canonical(
                [
                    point.model_dump(mode="json") if isinstance(point, ForecastPoint) else point
                    for point in raw
                ]
            ).encode()
        )
        > 1_000_000
    ):
        raise ValueError("invalid_forecast_series")
    points = tuple(
        sorted(
            (
                point if isinstance(point, ForecastPoint) else ForecastPoint.model_validate(point)
                for point in raw
            ),
            key=lambda point: point.value_origin.valid_from or datetime.min.replace(tzinfo=UTC),
        )
    )
    for index, left in enumerate(points):
        assert left.value_origin.valid_until
        for right in points[index + 1 :]:
            assert right.value_origin.valid_from
            if right.value_origin.valid_from >= left.value_origin.valid_until:
                break
            if canonical(left.value) != canonical(right.value):
                raise ValueError("overlapping_forecast_values")
    return points


def contributions(values: Any) -> tuple[ValueOrigin, ...]:
    result = {}
    for value in values:
        for origin in ([value.value_origin] if value.value_origin else []) + list(
            value.input_origins
        ):
            result[canonical(origin)] = origin
    return tuple(result.values())[:64]


def interval_bounds(origins: tuple[ValueOrigin, ...]) -> tuple[datetime | None, datetime | None]:
    begins = [origin.valid_from for origin in origins if origin.valid_from]
    ends = [origin.valid_until for origin in origins if origin.valid_until]
    return max(begins, default=None), min(ends, default=None)
