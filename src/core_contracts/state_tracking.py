"""JSON-compatible mutation tracking and lazy ownership for private state forks."""

import copy
from collections.abc import ItemsView, Iterator, Mapping, ValuesView
from dataclasses import dataclass, field
from typing import Any, Self, SupportsIndex

PUBLICATIONS = "__publications__"
_MISSING = object()


@dataclass
class Changes:
    changed: dict[str, set[Any]] = field(default_factory=dict)
    deleted: dict[str, set[Any]] = field(default_factory=dict)

    def write(self, table: str, key: Any) -> None:
        self.changed.setdefault(table, set()).add(key)
        self.deleted.setdefault(table, set()).discard(key)

    def delete(self, table: str, key: Any) -> None:
        self.changed.setdefault(table, set()).discard(key)
        self.deleted.setdefault(table, set()).add(key)

    def clear(self) -> None:
        self.changed.clear()
        self.deleted.clear()


@dataclass
class Owner:
    changes: Changes
    table: str
    key: Any
    shared: bool = False

    def touch(self) -> None:
        if self.shared:
            raise RuntimeError("shared_state_row_requires_detach")
        self.changes.write(self.table, self.key)


class MutationDict(dict[Any, Any]):
    def get(self, key: Any, default: Any = None) -> Any:
        return self[key] if key in self else default

    def items(self) -> Any:
        return ItemsView(self)

    def values(self) -> Any:
        return ValuesView(self)

    def update(self, *args: Any, **kwargs: Any) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key: Any, default: Any = _MISSING) -> Any:
        if key not in self:
            if default is _MISSING:
                raise KeyError(key)
            return default
        value = self[key]
        del self[key]
        return value

    def popitem(self) -> tuple[Any, Any]:
        if not self:
            raise KeyError("popitem(): dictionary is empty")
        key = next(reversed(self))
        return key, self.pop(key)

    def clear(self) -> None:
        for key in list(self):
            del self[key]

    def __ior__(self, other: Any) -> Self:
        self.update(other)
        return self

    def __or__(self, other: Any) -> dict[Any, Any]:
        return dict.__or__(self, other)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        memo[id(self)] = result
        for key, value in dict.items(self):
            result[copy.deepcopy(key, memo)] = copy.deepcopy(value, memo)
        return result


def child(value: Any, owner: Owner) -> Any:
    # Committed children are read-only. A fork adopts only the path it reads,
    # without copying the rest of a large catalog or marking a read as a write.
    if owner.shared:
        return value
    if isinstance(value, RowDict) and value.owner is not owner:
        return RowDict(value, owner)
    if isinstance(value, RowList) and value.owner is not owner:
        return RowList(value, owner)
    if isinstance(value, tuple):
        return tuple(child(item, owner) for item in value)
    return value


class RowDict(MutationDict):
    def __init__(self, data: Mapping[Any, Any], owner: Owner) -> None:
        dict.__init__(self, dict.items(data) if isinstance(data, dict) else data.items())
        self.owner = owner

    def __getitem__(self, key: Any) -> Any:
        value = dict.__getitem__(self, key)
        owned = child(value, self.owner)
        if owned is not value:
            dict.__setitem__(self, key, owned)
        return owned

    def __setitem__(self, key: Any, value: Any) -> None:
        self.owner.touch()
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        self.owner.touch()
        dict.__delitem__(self, key)


class RowList(list[Any]):
    def __init__(self, data: list[Any], owner: Owner) -> None:
        list.__init__(self, list.__iter__(data))
        self.owner = owner

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        value = list.__getitem__(self, index)
        owned = child(value, self.owner)
        if owned is not value:
            list.__setitem__(self, index, owned)
        return owned

    def __iter__(self) -> Iterator[Any]:
        index = 0
        while index < len(self):
            yield self[index]
            index += 1

    def __reversed__(self) -> Iterator[Any]:
        for index in range(len(self) - 1, -1, -1):
            yield self[index]

    def __setitem__(self, index: Any, value: Any) -> None:
        self.owner.touch()
        list.__setitem__(self, index, value)

    def __delitem__(self, index: Any) -> None:
        self.owner.touch()
        list.__delitem__(self, index)

    def append(self, value: Any) -> None:
        self.owner.touch()
        list.append(self, value)

    def extend(self, values: Any) -> None:
        self.owner.touch()
        list.extend(self, values)

    def insert(self, index: SupportsIndex, value: Any) -> None:
        self.owner.touch()
        list.insert(self, index, value)

    def pop(self, index: SupportsIndex = -1) -> Any:
        self.owner.touch()
        value = list.pop(self, index)
        return child(value, self.owner)

    def remove(self, value: Any) -> None:
        self.owner.touch()
        list.remove(self, value)

    def clear(self) -> None:
        self.owner.touch()
        list.clear(self)

    def reverse(self) -> None:
        self.owner.touch()
        list.reverse(self)

    def sort(self, *args: Any, **kwargs: Any) -> None:
        self.owner.touch()
        list.sort(self, *args, **kwargs)

    def __iadd__(self, values: Any) -> Self:
        self.extend(values)
        return self

    def __add__(self, values: Any) -> list[Any]:
        return list.__add__(self, values)

    def __imul__(self, count: SupportsIndex) -> Self:
        self.owner.touch()
        list.__imul__(self, count)
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        result: list[Any] = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(value, memo) for value in list.__iter__(self))
        return result


def detached(value: Any, owner: Owner, *, share_frozen: bool = False) -> Any:
    """Own changed containers; only explicitly read-only children may be shared."""
    if share_frozen and isinstance(value, (RowDict, RowList)) and value.owner.shared:
        return value
    if isinstance(value, dict):
        result = RowDict({}, owner)
        for key, item in dict.items(value):
            dict.__setitem__(result, key, detached(item, owner, share_frozen=True))
        return result
    if isinstance(value, list):
        return RowList(
            [detached(item, owner, share_frozen=True) for item in list.__iter__(value)], owner
        )
    if isinstance(value, tuple):
        return tuple(detached(item, owner, share_frozen=True) for item in value)
    return value


class Rows(MutationDict):
    def __init__(
        self, changes: Changes, name: str, data: Mapping[Any, Any], *, readonly: bool = False
    ) -> None:
        dict.__init__(self, dict.items(data) if isinstance(data, dict) else data.items())
        self.changes, self.name = changes, name
        self.accessed: set[Any] = set()
        self.readonly = readonly

    def __getitem__(self, key: Any) -> Any:
        value = dict.__getitem__(self, key)
        self.accessed.add(key)
        if (
            not self.readonly
            and isinstance(value, RowDict)
            and (value.owner.changes is not self.changes or value.owner.shared)
        ):
            value = RowDict(value, Owner(self.changes, self.name, key))
            dict.__setitem__(self, key, value)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        # Keep a newly assigned row's local aliases until commit; the row is
        # already dirty. Sealing detaches it before publication/further forks.
        dict.__setitem__(self, key, value)
        self.changes.write(self.name, key)

    def __delitem__(self, key: Any) -> None:
        dict.__delitem__(self, key)
        self.accessed.discard(key)
        self.changes.delete(self.name, key)

    def seal(self, keys: Any) -> None:
        for key in keys:
            if key in self:
                self.install(key, dict.__getitem__(self, key))

    def install(self, key: Any, value: Any) -> None:
        self.accessed.discard(key)
        dict.__setitem__(
            self, key, detached(value, Owner(self.changes, self.name, key, shared=True))
        )


class Tables(MutationDict):
    def __init__(
        self, changes: Changes, data: Mapping[Any, Any], *, readonly: set[str] | None = None
    ) -> None:
        dict.__init__(self)
        self.changes = changes
        for name, rows in data.items():
            shared = (
                name in readonly
                if readonly is not None
                else isinstance(rows, Rows) and rows.readonly
            )
            dict.__setitem__(self, name, Rows(changes, name, rows, readonly=shared))

    def __setitem__(self, name: Any, value: Any) -> None:
        old = self.get(name, {})
        for key in old.keys() - value.keys():
            self.changes.delete(name, key)
        for key in value:
            self.changes.write(name, key)
        dict.__setitem__(self, name, Rows(self.changes, name, value))
