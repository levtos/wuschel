"""Explicit administrative observations, atomically committed and repeat safe."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from .model import Model, checksum, utc
from .value_origin import ValueOrigin
from .value_origin import normalize as normalize_origin

if TYPE_CHECKING:
    from .persistence import State
    from .runtime import Runtime


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


def affected(config: Any, source_id: str) -> list[str]:
    from .registry import resolve_ref

    reached = {source_id}
    while True:
        extra = {
            c.contract_id
            for c in config.contracts
            if c.enabled and any(resolve_ref(i.ref, config)[0] in reached for i in c.inputs)
        }
        if extra <= reached:
            break
        reached |= extra
    return sorted(reached - {source_id})


async def enter(runtime: Runtime, state: State, payload: dict[str, Any]) -> dict[str, Any]:
    from .evidence import Observation

    request = ManualRequest.model_validate(payload["request"])
    source_id = payload["source_id"]
    identity = str(request.request_id)
    digest = checksum({"source_id": source_id, "request": request.model_dump(mode="json")})
    previous = await runtime.store.get("manual_request", identity)
    if previous:
        if previous["request_hash"] != digest:
            return {"request_id": identity, "status": "rejected", "reason": "request_id_conflict"}
        return dict(previous["result"])
    config = runtime._config(state)
    source = next((s for s in config.sources if s.source_id == source_id), None) if config else None
    binding = (
        next((b for b in config.bindings if b.source_id == source_id), None) if config else None
    )
    now = runtime.clock.now_utc()
    start = request.valid_from or now
    normalized, origin_error = request.value, False
    origin_fields: dict[str, Any] = {}
    if (
        source
        and binding
        and config
        and binding.adapter.kind == "manual"
        and request.action == "set"
    ):
        try:
            normalized, origin_fields = normalize_origin(
                request.value, request.value_origin, binding, config
            )
        except ValueError, TypeError, KeyError:
            origin_error = True
    values = [point.value for point in origin_fields.get("forecast", ())] or [normalized]
    end = request.valid_until
    if source and source.manual_structured_schema and request.action == "set":
        from .wake.operator import local_start
        from .wake.settings import WakeException

        try:
            from datetime import date

            exception = WakeException.model_validate(normalized)
            maximum_end = utc(local_start(date.fromisoformat(exception.date) + timedelta(days=1)))
            if maximum_end <= now or (end is not None and end != maximum_end):
                raise ValueError("exception_expiry")
            end = maximum_end
            normalized = exception.model_dump(mode="json", exclude_none=True)
        except ValueError, TypeError:
            origin_error = True
    reason = (
        "active_revision_conflict"
        if state.active_revision != request.expected_active_revision
        else "manual_source_required"
        if not source
        or not binding
        or binding.adapter.kind != "manual"
        or source.origin != "manual"
        else "source_not_in_use"
        if not affected(config, source_id)
        else "manual_value_type"
        if request.action == "set"
        and (
            origin_error
            or not source.value_schema
            or any(not source.value_schema.accepts(value) for value in values)
        )
        else "manual_value_bounds"
        if request.action == "set"
        and any(
            type(value) in {int, float}
            and (
                source.manual_minimum is not None
                and value < source.manual_minimum
                or source.manual_maximum is not None
                and value > source.manual_maximum
            )
            for value in values
        )
        else "manual_future_not_enabled"
        if start > now and not source.manual_allow_future
        else "manual_begin_in_past"
        if request.valid_from and start < now - timedelta(seconds=5)
        else "manual_validity_interval"
        if end and (end <= start or end <= now)
        else "manual_unbounded_confirmation"
        if request.action == "set"
        and source.freshness.mode == "event_stateful"
        and end is None
        and not request.confirm_unbounded
        else None
    )
    result: dict[str, Any] = {
        "request_id": identity,
        "status": "rejected" if reason else "accepted",
        "reason": reason,
        "source_id": source_id,
    }
    if reason is None:
        assert source and binding and config
        from .registry import fingerprints

        evidence = ManualEvidence(
            request_id=identity,
            source_fingerprint=fingerprints(config, runtime.types)[source_id],
            recorded_at=now,
            valid_from=start,
            valid_until=end,
            actor=payload["_actor"],
            note=request.note,
            revoked=request.action == "retract",
        )
        observation = Observation(
            source_id=source_id,
            binding_id=binding.binding_id,
            adapter="manual",
            origin="manual",
            manual=evidence,
            received_at=now,
            value_raw=request.value if request.action == "set" else None,
            value_normalized=normalized if request.action == "set" else None,
            normalized=True,
            observation_kind="live_change",
            epoch_id=state.epoch_id,
            ingest_seq=state.generation + 1,
            registry_revision=state.active_revision,
            reference_version=2,
            **origin_fields,
        )
        state.tables["source_observation_current"][binding.binding_id] = (
            observation.storage_document()
        )
        await runtime._record_observation(state, observation)
        runtime._evaluate(state, config, origin="input", trigger_source=source_id)
        result.update(reference=observation.reference, recorded_at=now.isoformat())
    state.tables["manual_request"][identity] = {
        "request_hash": digest,
        "result": result,
        "at": now.isoformat(),
        "source_id": source_id,
    }
    await runtime._commit(state, publication=reason is None)
    return result
