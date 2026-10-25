"""Observations preserve source clocks; receipt never becomes measurement."""

from datetime import datetime, timedelta
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from .ha_context import Attribution, HAContext
from .manual_source import ManualEvidence
from .model import Model, utc
from .quality import FieldValue, ReasonCode, unknown
from .value_origin import ForecastPoint, ValueOrigin, forecast_points, interval_bounds


class Freshness(Model):
    mode: Literal["report_heartbeat", "liveness_source", "periodic_ttl", "event_stateful"]
    future_tolerance_s: float = Field(ge=0, strict=True, allow_inf_nan=False)
    interval_s: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False)
    liveness_source: str | None = None

    @model_validator(mode="after")
    def required(self) -> Self:
        if self.mode != "event_stateful" and self.interval_s is None:
            raise ValueError("freshness interval required")
        if self.mode == "liveness_source" and not self.liveness_source:
            raise ValueError("liveness source required")
        return self


class Observation(Model):
    source_id: str
    binding_id: str
    adapter: Literal[
        "ha_state",
        "ha_attribute",
        "ha_calendar",
        "mqtt",
        "scheduler",
        "connectivity",
        "command",
        "manual",
    ]
    source_origin: str = "local"
    origin: Literal["manual"] | None = Field(default=None, exclude_if=lambda value: value is None)
    manual: ManualEvidence | None = Field(default=None, exclude_if=lambda value: value is None)
    value_origin: ValueOrigin | None = Field(default=None, exclude_if=lambda value: value is None)
    forecast: tuple[ForecastPoint, ...] = Field(
        default=(), max_length=512, exclude_if=lambda value: not value
    )
    value_raw: Any = None
    value_normalized: Any = None
    normalized: bool = False
    device_time: datetime | None = None
    ha_last_changed: datetime | None = None
    ha_last_updated: datetime | None = None
    ha_last_reported: datetime | None = None
    ha_time_fired: datetime | None = None
    ha_context_id: str | None = None
    ha_context: HAContext | None = Field(default=None, exclude_if=lambda value: value is None)
    attribution: Attribution | None = Field(default=None, exclude_if=lambda value: value is None)
    mqtt_retained: bool = False
    mqtt_qos: int | None = Field(default=None, ge=0, le=2)
    mqtt_device_proof: str | None = Field(default=None, exclude_if=lambda value: value is None)
    mqtt_revalidated: bool = Field(default=False, exclude_if=lambda value: not value)
    received_at: datetime
    observation_kind: Literal["live_change", "report", "snapshot", "restore"]
    ha_restored: bool = False
    assumed_state: bool = False
    invalid_timestamp: bool = False
    transform_error: Literal["unmapped_value", "invalid_value"] | None = None
    epoch_id: str
    ingest_seq: int = Field(ge=0)
    registry_revision: int | None = None
    reference_version: Literal[1, 2] = 1
    availability: Literal["available", "unavailable", "unknown", "absent"] = "available"

    @model_validator(mode="after")
    def reference_complete(self) -> Self:
        if self.reference_version == 2 and self.registry_revision is None:
            raise ValueError("versioned_evidence_requires_revision")
        if self.forecast:
            forecast_points(self.forecast)
        return self

    @field_validator(
        "device_time",
        "ha_last_changed",
        "ha_last_updated",
        "ha_last_reported",
        "ha_time_fired",
        "received_at",
    )
    @classmethod
    def times(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value is not None else None

    @property
    def measurement(self) -> datetime | None:
        if self.invalid_timestamp:
            return None
        if self.adapter == "manual" and self.manual:
            return self.manual.valid_from
        if self.device_time:
            return self.device_time
        return self.ha_measurement

    def storage_document(self) -> dict[str, Any]:
        """Omit the additive default for documents still using Alpha-3 identities."""
        return self.model_dump(
            mode="json", exclude={"reference_version"} if self.reference_version == 1 else set()
        )

    @property
    def ha_measurement(self) -> datetime | None:
        if self.observation_kind == "report":
            return self.ha_last_reported
        if self.observation_kind == "live_change":
            return self.ha_time_fired or self.ha_last_changed
        # A snapshot can carry a source timestamp; last_updated alone proves no observation.
        return self.ha_last_reported or self.ha_last_changed or self.ha_time_fired

    @property
    def value(self) -> Any:
        return self.value_normalized if self.normalized else self.value_raw

    @property
    def reference(self) -> str:
        if self.reference_version == 2:
            return f"v2:{self.epoch_id}:{self.registry_revision}:{self.adapter}:{self.ingest_seq}:{self.binding_id}"
        return f"{self.epoch_id}:{self.ingest_seq}:{self.binding_id}"


def _ordering_times(
    current: Observation, previous: Observation
) -> tuple[datetime | None, datetime | None]:
    """Device and HA clocks can differ within the allowed future tolerance."""
    if current.device_time is not None and previous.device_time is not None:
        return current.device_time, previous.device_time
    return current.ha_measurement, previous.ha_measurement


def is_older(current: Observation, previous: Observation | None) -> bool:
    """Only a shared measurement clock proves ordering; receipt time never does."""
    if previous is None:
        return False
    stamp, old_stamp = _ordering_times(current, previous)
    return stamp is not None and old_stamp is not None and stamp < old_stamp


def is_new(current: Observation, previous: Observation | None) -> bool:
    if current.ha_restored or current.mqtt_retained or current.observation_kind == "restore":
        return False
    stamp = current.measurement
    if previous is None:
        return stamp is not None
    if is_older(current, previous):
        return False
    if current.availability != previous.availability:
        return True
    if stamp is None:
        return False
    if current.observation_kind == "live_change" and current.value != previous.value:
        return True
    if previous.measurement is None:
        return True
    stamp, old_stamp = _ordering_times(current, previous)
    return stamp is not None and old_stamp is not None and stamp > old_stamp


def assess(
    observation: Observation | None,
    policy: Freshness,
    now: datetime,
    source_id: str,
    liveness: Observation | None = None,
) -> FieldValue:
    if observation is None:
        return unknown(ReasonCode.INPUT_ABSENT, source_id, now)
    code: ReasonCode | None = None
    if observation.transform_error:
        code = ReasonCode(observation.transform_error)
    elif observation.invalid_timestamp:
        code = ReasonCode.INVALID_VALUE
    elif observation.availability != "available":
        code = {
            "absent": ReasonCode.INPUT_ABSENT,
            "unavailable": ReasonCode.INPUT_UNAVAILABLE,
            "unknown": ReasonCode.INPUT_UNKNOWN,
        }[observation.availability]
    elif observation.adapter == "manual":
        entry = observation.manual
        if entry is None or entry.revoked:
            code = ReasonCode.INPUT_ABSENT
        elif now < entry.valid_from:
            code = ReasonCode.INPUT_NOT_YET_VALID
        elif (
            entry.valid_until
            and now >= entry.valid_until
            or policy.mode == "periodic_ttl"
            and (now - entry.valid_from).total_seconds() >= (policy.interval_s or 0)
        ):
            code = ReasonCode.INPUT_STALE
    elif observation.ha_restored or (
        observation.observation_kind == "restore"
        and not (observation.adapter == "mqtt" and observation.mqtt_revalidated)
    ):
        code = ReasonCode.INPUT_RESTORED
    elif observation.assumed_state:
        code = ReasonCode.INPUT_UNKNOWN
    elif observation.mqtt_retained or observation.measurement is None:
        code = ReasonCode.INPUT_STALE
    elif (observation.measurement - now).total_seconds() > policy.future_tolerance_s:
        code = ReasonCode.INVALID_VALUE
    elif policy.mode == "liveness_source":
        if (
            liveness is None
            or liveness.invalid_timestamp
            or liveness.measurement is None
            or liveness.ha_restored
            or liveness.mqtt_retained
            or liveness.availability != "available"
            or (liveness.measurement - now).total_seconds() > policy.future_tolerance_s
            or (now - liveness.measurement).total_seconds() > (policy.interval_s or 0)
        ):
            code = ReasonCode.DEPENDENCY_UNAVAILABLE
    elif policy.mode != "event_stateful" and (now - observation.measurement).total_seconds() > (
        policy.interval_s or 0
    ):
        code = ReasonCode.INPUT_STALE
    if code:
        result = unknown(code, source_id, now)
        if (
            code == ReasonCode.INPUT_STALE
            and not observation.mqtt_retained
            and observation.measurement is not None
            and policy.mode == "report_heartbeat"
        ):
            since = observation.measurement + timedelta(
                seconds=policy.interval_s or 0, microseconds=1
            )
            result = result.model_copy(
                update={
                    "reasons": tuple(
                        r.model_copy(update={"since": min(now, since)}) for r in result.reasons
                    )
                }
            )
        return result.model_copy(
            update={"evidence": (observation.reference,), "freshness": "invalid"}
        )
    if observation.value is None:
        return unknown(ReasonCode.INVALID_VALUE, source_id, now)
    value = observation.value
    value_origin = observation.value_origin
    selected = tuple(point for point in observation.forecast if point.value_origin.includes(now))
    origins = tuple(point.value_origin for point in selected)
    if observation.forecast:
        if not selected:
            return unknown(ReasonCode.VALUE_VALIDITY_GAP, source_id, now)
        value = selected[0].value
        begin, finish = interval_bounds(origins)
        value_origin = selected[0].value_origin.model_copy(
            update={
                "valid_from": begin,
                "valid_until": finish,
                "issued_at": min(origin.issued_at for origin in origins if origin.issued_at),
                "provider": origins[0].provider
                if all(origin.provider == origins[0].provider for origin in origins)
                else "",
                "based_on": tuple(
                    sorted({reference for origin in origins for reference in origin.based_on})
                ),
            }
        )
    if value_origin and (
        not value_origin.includes(now) or value_origin.issued_at and value_origin.issued_at > now
    ):
        return unknown(ReasonCode.VALUE_VALIDITY_GAP, source_id, now)
    if (
        value_origin
        and value_origin.kind == "forecast"
        and value_origin.issued_at
        and policy.interval_s
        and (now - value_origin.issued_at).total_seconds() > policy.interval_s
    ):
        return unknown(ReasonCode.INPUT_STALE, source_id, now)
    begin, finish = interval_bounds((value_origin,) if value_origin else ())
    if observation.manual:
        begin = max(at for at in (begin, observation.manual.valid_from) if at)
        finish = min((at for at in (finish, observation.manual.valid_until) if at), default=None)
    return FieldValue(
        status="valid",
        value=value,
        value_origin=value_origin,
        input_origins=origins if len(origins) > 1 else (),
        valid_from=begin,
        valid_until=finish,
        quality="healthy",
        measured_at=observation.measurement,
        attribution=observation.attribution,
        evidence=(observation.reference,),
        freshness="fresh",
        since_at=observation.ha_last_changed if observation.adapter == "ha_state" else None,
    )
