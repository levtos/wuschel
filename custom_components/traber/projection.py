"""A bounded transport cache, never an evaluator of contract truth."""

import copy
import json
import math
import re
from time import perf_counter
from uuid import UUID


class Projection:
    def __init__(self, now=perf_counter, schedule=None):
        self.rows = {}
        self.installation_id = None
        self.epoch_id = None
        self.sequence = -1
        self.owner = None
        self.connected = False
        self.listeners = set()
        self.sensor_listeners = {}
        self.now = now
        self.schedule = schedule
        self.disconnected_at = None
        self.expiry = None
        self.grace_seconds = 120

    @property
    def available(self):
        return self.connected or (
            self.disconnected_at is not None
            and self.now() - self.disconnected_at < self.grace_seconds
        )

    def notify(self, changed=None):
        for listener in tuple(self.listeners):
            listener()
        for contract_id, listeners in tuple(self.sensor_listeners.items()):
            if changed is None or contract_id in changed:
                for listener in tuple(listeners):
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
            required = {
                "contract_id",
                "name",
                "value",
                "status",
                "quality",
                "since_at",
                "unit",
            }
            if not isinstance(row, dict) or set(row) not in (
                required,
                required | {"primary_field", "fields"},
            ):
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
            if "fields" in row:
                fields = row["fields"]
                if (
                    not isinstance(fields, dict)
                    or not 1 <= len(fields) <= 64
                    or not isinstance(row["primary_field"], str)
                    or row["primary_field"] not in fields
                ):
                    raise ValueError("projection_fields_invalid")
                for name, field in fields.items():
                    if (
                        not isinstance(name, str)
                        or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,127}", name)
                        or not isinstance(field, dict)
                        or set(field) != {"value", "status", "quality", "since_at", "unit"}
                    ):
                        raise ValueError("projection_fields_invalid")
                    if field["status"] not in {
                        "valid",
                        "held",
                        "unknown",
                        "unresolved",
                        "not_applicable",
                    } or field["quality"] not in {"healthy", "degraded"}:
                        raise ValueError("projection_status_invalid")
                    if field["status"] not in {"valid", "held"} and field["value"] is not None:
                        raise ValueError("projection_value_invalid")
                    if any(
                        field[attr] is not None
                        and (not isinstance(field[attr], str) or len(field[attr]) > 256)
                        for attr in ("since_at", "unit")
                    ):
                        raise ValueError("projection_attribute_invalid")
                if any(
                    row[name] != fields[row["primary_field"]][name]
                    for name in ("value", "status", "quality", "since_at", "unit")
                ):
                    raise ValueError("projection_primary_mismatch")
                try:
                    if len(json.dumps(fields, allow_nan=False).encode()) > 65536:
                        raise ValueError("projection_fields_invalid")
                except TypeError, OverflowError, RecursionError:
                    raise ValueError("projection_fields_invalid") from None
            checked[key] = copy.deepcopy(row)
        if (
            self.installation_id == installation_id
            and self.epoch_id == epoch
            and sequence < self.sequence
        ):
            return False
        changed = None
        if self.connected and self.installation_id == installation_id and self.epoch_id == epoch:
            # JSON comparison preserves scalar types (True and 1 differ in HA).
            changed = {
                key
                for key in self.rows.keys() | checked.keys()
                if json.dumps(self.rows.get(key), sort_keys=True)
                != json.dumps(checked.get(key), sort_keys=True)
            }
        self.owner = owner
        self.installation_id, self.epoch_id, self.sequence = installation_id, epoch, sequence
        self.rows, self.connected = checked, True
        self.disconnected_at = None
        if self.expiry:
            self.expiry.cancel()
            self.expiry = None
        self.notify(changed)
        return True

    def disconnect(self, owner):
        if self.owner is owner:
            self.connected = False
            self.owner = None
            if self.disconnected_at is None:
                self.disconnected_at = self.now()
                if self.schedule:
                    self.expiry = self.schedule(self.grace_seconds, self.notify)
            self.notify()
