"""Extension registers. The application imports owners; the lane invokes them.

Hook keys are fixed order numbers, independent of owner import order. Phases
identify the existing insertion points within a lifecycle method.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .persistence import State
    from .registry import RegistryConfig
    from .runtime import Runtime


@dataclass(frozen=True)
class Operation:
    handler: Callable[[Runtime, State, str, Any, datetime, RegistryConfig | None], Awaitable[Any]]
    state: Literal["none", "live", "catalog", "full"] = "full"
    early: bool = False
    drain: bool = True
    offline: bool = False
    after_prepare: Callable[[Runtime, Any, float], None] | None = None


@dataclass(frozen=True)
class Hook:
    handler: Callable[..., Any]
    phase: str = ""


OPERATIONS: dict[str, Operation] = {}
OPERATION_PREFIXES: dict[str, Operation] = {}
RESTORE: dict[int, Hook] = {}
BEFORE_COMMIT: dict[int, Hook] = {}
AFTER_EVALUATE: dict[int, Hook] = {}
NEXT_DUE: dict[int, Hook] = {}
OBSERVATION: dict[int, Hook] = {}

# Submission planners and configuration helpers also live above the lane.
# Registers keep their implementations out of Runtime and preserve its public
# methods (including existing subclass overrides and test patch points).
SUBMIT: dict[str, Callable[..., Awaitable[Any]]] = {}
CONFIGURATION: dict[str, Callable[..., Any]] = {}

SUBMIT_PREFIXES: dict[str, Callable[..., Awaitable[Any]]] = {}
