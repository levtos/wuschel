"""Bounded, process-local reuse of successful checks on immutable control bases."""

from collections import OrderedDict
from threading import Lock

from .registry import RegistryConfig

CHECK_VERSION = 1


class ValidationCache:
    def __init__(self) -> None:
        self.lock = Lock()
        self.items: OrderedDict[str, RegistryConfig] = OrderedDict()

    def get(self, key: str) -> RegistryConfig | None:
        with self.lock:
            value = self.items.get(key)
            if value is not None:
                self.items.move_to_end(key)
            return value

    def put(self, key: str, value: RegistryConfig) -> None:
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > 32:
                self.items.popitem(last=False)
