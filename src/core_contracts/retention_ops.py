"""Writer operations owned by retention_ops."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import slots
from .persistence import State
from .registry import (
    RegistryConfig,
)

if TYPE_CHECKING:
    from .runtime import Runtime


async def operation_retention_preview(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    if runtime.config is None or runtime.config.history_retention is None:
        raise ValueError("history_retention_not_configured")
    return await runtime._maintain_history(
        runtime.state,
        runtime.clock.now_utc(),
        runtime.config.history_retention,
        dry_run=operation == "retention_preview",
    )


slots.OPERATIONS["retention_preview"] = slots.Operation(
    operation_retention_preview, state="none", early=False, heavy=True
)
slots.OPERATIONS["retention_run"] = slots.Operation(
    operation_retention_preview, state="none", early=False, heavy=True
)
