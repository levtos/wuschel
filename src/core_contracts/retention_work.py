"""Reference-aware retention plans prepared outside the truth writer."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from . import slots
from .model import Model
from .persistence import PostgresStore, State
from .registry import RegistryConfig
from .retention import RetentionBatch, collect_retention_batch, pinned_references

if TYPE_CHECKING:
    from .runtime import Runtime


class PreparedRetention(Model):
    request_id: UUID
    expected_active_revision: int | None
    batch: RetentionBatch
    scheduled: bool = False


async def prepare_retention(
    runtime: Runtime, connection: Any, job_id: UUID, *, scheduled: bool = False
) -> PreparedRetention:
    config = runtime.config
    if config is None or config.history_retention is None:
        raise ValueError("history_retention_not_configured")
    batch = await collect_retention_batch(
        connection,
        runtime.clock.now_utc(),
        config.history_retention,
        pinned_references(runtime.state),
    )
    return PreparedRetention(
        request_id=job_id,
        expected_active_revision=runtime.state.active_revision,
        batch=batch,
        scheduled=scheduled,
    )


async def publish_retention(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: PreparedRetention,
    now: datetime,
    config: RegistryConfig | None,
) -> dict[str, Any]:
    if not isinstance(runtime.store, PostgresStore):
        raise ValueError("history_retention_not_configured")
    key = f"retention:{payload.request_id}"
    policy = config.history_retention if config else None
    if previous := await runtime.store.get("catalog_request", key):
        result: dict[str, Any] = previous["result"]
    else:
        if (
            state.active_revision != payload.expected_active_revision
            or policy is None
            or payload.batch.batch_size != policy.batch_size
        ):
            raise ValueError("retention_plan_conflict")
        result = await runtime.store.apply_prepared_retention(state, key, payload.batch)
    if payload.scheduled and policy is not None:
        runtime.retention_receipt = result
        runtime.next_retention = now + timedelta(
            seconds=60 if result["selected"] == policy.batch_size else policy.interval_s
        )
        runtime._plan_evaluation()
    return result


slots.OPERATIONS["retention_run_prepared"] = slots.Operation(
    publish_retention, state="none", early=False
)
