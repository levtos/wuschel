"""Compare JSON values without changing canonical hashes or stored identities."""

import copy
import math
from typing import Any

OBJECT_KEYS = {
    "dependencies": ("dependency_id",),
    "sources": ("source_id",),
    "bindings": ("binding_id",),
    "contracts": ("contract_id",),
    "catalogs": ("catalog_id", "version"),
    "definitions": ("type_id", "type_version"),
    "context_producers": ("producer_id",),
    "freshness_templates": ("template_id",),
}


def equivalent(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return bool(
            (type(left) is not float or math.isfinite(left))
            and (type(right) is not float or math.isfinite(right))
            and left == right
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            equivalent(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            equivalent(a, b) for a, b in zip(left, right, strict=True)
        )
    return type(left) is type(right) and bool(left == right)


def preserve_numbers(incoming: Any, stored: Any) -> Any:
    """Keep stored representations, including within an otherwise edited object."""
    if equivalent(incoming, stored):
        return copy.deepcopy(stored)
    if isinstance(incoming, dict) and isinstance(stored, dict):
        return {
            key: preserve_numbers(value, stored[key]) if key in stored else copy.deepcopy(value)
            for key, value in incoming.items()
        }
    if isinstance(incoming, list) and isinstance(stored, (list, tuple)):
        return [
            preserve_numbers(value, stored[index]) if index < len(stored) else copy.deepcopy(value)
            for index, value in enumerate(incoming)
        ]
    return copy.deepcopy(incoming)


def preserve_config(document: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """Match registry objects by identity, never by their position in an array."""
    result: dict[str, Any] = preserve_numbers(
        document, {key: value for key, value in stored.items() if key not in OBJECT_KEYS}
    )
    for group, fields in OBJECT_KEYS.items():
        rows, old_rows = document.get(group), stored.get(group)
        if not isinstance(rows, list) or not isinstance(old_rows, list):
            continue

        def identity(row: Any) -> tuple[Any, ...] | None:
            if not isinstance(row, dict) or any(
                type(row.get(field)) not in (str, int) for field in fields
            ):
                return None
            return tuple((type(row[field]), row[field]) for field in fields)

        previous = {identity(row): row for row in old_rows if identity(row) is not None}
        result[group] = [
            preserve_numbers(row, previous[identity(row)])
            if identity(row) in previous
            else copy.deepcopy(row)
            for row in rows
        ]
    return result
