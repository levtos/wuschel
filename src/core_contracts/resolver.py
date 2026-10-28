"""Code-defined resolver lifecycle and generic operators."""

import logging
import operator
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .quality import FieldValue, Reason, ReasonCode, derive, unknown

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Context:
    now: datetime
    revision: int
    origin: str


class Resolver(Protocol):
    def init(self) -> None: ...
    def evaluate(self, inputs: dict[str, FieldValue], ctx: Context) -> FieldValue: ...
    def dispose(self) -> None: ...


def evaluate(resolver: Resolver, inputs: dict[str, FieldValue], ctx: Context) -> FieldValue:
    try:
        return FieldValue.model_validate(resolver.evaluate(inputs, ctx).model_dump())
    except Exception as error:
        LOGGER.error("resolver_failed", extra={"error_type": type(error).__name__})
        return unknown(ReasonCode.EVALUATION_ERROR, "resolver", ctx.now)


def compare(left: FieldValue, right: FieldValue, op: str, now: datetime) -> FieldValue:
    operations: dict[str, Callable[[Any, Any], bool]] = {
        "eq": operator.eq,
        "ne": operator.ne,
        "lt": operator.lt,
        "le": operator.le,
        "gt": operator.gt,
        "ge": operator.ge,
    }
    if not left.usable(now) or not right.usable(now):
        return derive(None, [left, right], now)
    try:
        return derive(operations[op](left.value, right.value), [left, right], now)
    except KeyError, TypeError, ValueError:
        return unknown(ReasonCode.INVALID_VALUE, "compare", now)


def boolean(op: str, inputs: list[FieldValue], now: datetime) -> FieldValue:
    if not inputs or op not in {"and", "or", "not"} or (op == "not" and len(inputs) != 1):
        return unknown(ReasonCode.CONFIG_INCOMPLETE, "boolean", now)
    valid = [item for item in inputs if item.usable(now) and type(item.value) is bool]
    if op == "not":
        return (
            derive(not valid[0].value, valid, now)
            if valid
            else derive(None, inputs, now)
            if any(not item.usable(now) for item in inputs)
            else unknown(ReasonCode.INVALID_VALUE, "boolean", now)
        )
    decisive = [item for item in valid if item.value is (op == "or")]
    if decisive:
        # Prefer valid evidence when it alone determines the result.
        chosen = next((i for i in decisive if i.status == "valid"), decisive[0])
        return derive(op == "or", [chosen], now)
    if len(valid) != len(inputs):
        return (
            derive(None, inputs, now)
            if any(not item.usable(now) for item in inputs)
            else unknown(ReasonCode.INVALID_VALUE, "boolean", now)
        )
    return derive(op == "and", valid, now)


def first_match(cases: list[tuple[FieldValue, FieldValue]], now: datetime) -> FieldValue:
    for condition, result in cases:
        if not condition.usable(now) or type(condition.value) is not bool:
            return FieldValue(
                status="unresolved",
                quality="degraded",
                reasons=(Reason(code=ReasonCode.SELECTION_BLOCKED, input="condition", since=now),)
                + condition.reasons,
            )
        if condition.value:
            # A positively selected non-applicable result is a complete answer,
            # not missing evidence (e.g. no title when there is no session).
            if (
                result.status == "not_applicable"
                and condition.status == "valid"
                and condition.quality == "healthy"
            ):
                # A value-bearing view lets derive merge all existing bounds,
                # origins and hints without treating positive absence as missing.
                merged = derive(
                    True,
                    [condition, result.model_copy(update={"status": "valid", "value": True})],
                    now,
                )
                if merged.status == "valid" and merged.quality == "healthy":
                    return merged.model_copy(
                        update={
                            "status": "not_applicable",
                            "value": None,
                            "positively_not_applicable": True,
                        }
                    )
                return merged
            return derive(result.value, [condition, result], now)
    return unknown(ReasonCode.NO_MATCH, "cases", now)


def map_value(value: FieldValue, mapping: dict[str, Any], now: datetime) -> FieldValue:
    if not value.usable(now):
        return value
    if str(value.value) not in mapping:
        return unknown(ReasonCode.UNMAPPED_VALUE, "map", now)
    return derive(mapping[str(value.value)], [value], now)


@dataclass
class Bucket:
    initial: bool
    lower: float
    upper: float
    latch: bool | None = None

    def evaluate(self, value: FieldValue, now: datetime) -> FieldValue:
        if self.lower > self.upper:
            raise ValueError("invalid hysteresis bounds")
        if not value.usable(now) or type(value.value) not in {int, float}:
            self.latch = self.initial
            return unknown(ReasonCode.INPUT_UNKNOWN, "bucket", now)
        if self.latch is None:
            self.latch = self.initial
        if value.value >= self.upper:
            self.latch = True
        elif value.value <= self.lower:
            self.latch = False
        return derive(self.latch, [value], now)


class FormulaCatalog:
    def __init__(self) -> None:
        self._items: dict[tuple[str, int], Callable[[list[Any]], Any]] = {}

    def register(self, name: str, version: int, formula: Callable[[list[Any]], Any]) -> None:
        if (name, version) in self._items:
            raise ValueError("immutable formula version")
        self._items[name, version] = formula

    def evaluate(
        self,
        name: str,
        version: int,
        inputs: list[FieldValue],
        now: datetime,
        *,
        raise_errors: bool = False,
        names: tuple[str, ...] | None = None,
    ) -> FieldValue:
        if any(not i.usable(now) for i in inputs):
            return derive(None, inputs, now)
        try:
            values: Any = (
                dict(zip(names, (i.value for i in inputs), strict=True))
                if names is not None
                else [i.value for i in inputs]
            )
            value = self._items[name, version](values)
            return derive(value, inputs, now)
        except KeyError, TypeError, ValueError, ArithmeticError:
            if raise_errors:
                raise
            return unknown(ReasonCode.EVALUATION_ERROR, "formula", now)
