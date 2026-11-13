"""Extension registers. The application imports owners; the lane invokes them.

Hook keys are fixed order numbers, independent of owner import order. Phases
identify the existing insertion points within a lifecycle method.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from .quality import FieldValue
from .schema import FieldSchema

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
    timing: str | None = None


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


@dataclass(frozen=True)
class Rule:
    check: Callable[..., FieldSchema] | None = None
    evaluate: Callable[..., FieldValue] | None = None
    children: Callable[..., list[tuple[str, Any]]] | None = None
    due: Callable[..., None] | None = None
    validate: Callable[..., None] | None = None
    minimum_language: int = 0
    manual_schema: str | None = None
    dependencies: dict[str, Callable[..., Any]] = field(default_factory=dict)
    manual: Callable[..., tuple[Any, datetime]] | None = None


@dataclass(frozen=True)
class SourceProfile:
    read: Callable[..., Any]
    metadata: Callable[..., dict[str, Any]]


RULES: dict[type[BaseModel], Rule] = {}
PARAMETER_MODELS: dict[str, type[BaseModel]] = {}
SOURCE_PROFILES: dict[str, SourceProfile] = {}
ROUTES: list[Callable[..., None]] = []
PORTABLE: dict[str, Callable[..., Any]] = {}
VALIDATION: dict[str, Callable[..., Any]] = {}
AUTHORING: dict[str, Callable[..., Any]] = {}
