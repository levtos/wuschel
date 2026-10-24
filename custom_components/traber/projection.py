"""A bounded transport cache, never an evaluator of contract truth."""

import math
import re
from uuid import UUID


class Projection:
    def __init__(self):
        self.rows = {}
        self.installation_id = None
        self.epoch_id = None
        self.sequence = -1
        self.owner = None
        self.connected = False
        self.listeners = set()

    def notify(self):
        for listener in tuple(self.listeners):
            listener()

    def accept(self, owner, installation_id, payload):
        if not installation_id or payload.get("installation_id") != installation_id:
            raise ValueError("projection_installation_mismatch")
        UUID(installation_id)
        epoch = payload.get("epoch_id")
        UUID(epoch)
        sequence = payload.get("publication_seq")
        if type(sequence) is not int or sequence < 0:
            raise ValueError("projection_sequence_invalid")
        if self.connected and self.owner is not owner:
            raise ValueError("projection_connection_conflict")
        rows = payload.get("contracts")
        if not isinstance(rows, list) or len(rows) > 256:
            raise ValueError("projection_selection_invalid")
        checked = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "contract_id",
                "name",
                "value",
                "status",
                "quality",
                "since_at",
                "unit",
            }:
                raise ValueError("projection_row_invalid")
            key = row["contract_id"]
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{1,255}", key)
                or key in checked
            ):
                raise ValueError("projection_identity_invalid")
            if not isinstance(row["name"], str) or len(row["name"]) > 256:
                raise ValueError("projection_name_invalid")
            if row["status"] not in {
                "valid",
                "held",
                "unknown",
                "unresolved",
                "not_applicable",
            } or row["quality"] not in {"healthy", "degraded"}:
                raise ValueError("projection_status_invalid")
            value = row["value"]
            if value is not None and (
                type(value) not in {str, int, float, bool}
                or isinstance(value, float)
                and not math.isfinite(value)
            ):
                raise ValueError("projection_value_invalid")
            if any(
                row[name] is not None and (not isinstance(row[name], str) or len(row[name]) > 256)
                for name in ("since_at", "unit")
            ):
                raise ValueError("projection_attribute_invalid")
            checked[key] = dict(row)
        if (
            self.installation_id == installation_id
            and self.epoch_id == epoch
            and sequence < self.sequence
        ):
            return False
        self.owner = owner
        self.installation_id, self.epoch_id, self.sequence = installation_id, epoch, sequence
        self.rows, self.connected = checked, True
        self.notify()
        return True

    def disconnect(self, owner):
        if self.owner is owner:
            self.connected = False
            self.owner = None
            self.notify()
