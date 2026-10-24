"""One serial authority: stage, evaluate, commit, then make results visible."""

import asyncio
import copy
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator

from .calendar_inputs import project
from .clock import Alarm, Clock
from .declarative import GuardEvaluationError, all_rules
from .declarative_models import FormulaRule
from .engines import FORMULA_SCHEMAS
from .evidence import Observation, assess, is_new, is_older
from .ha_context import ContextIndex
from .lifecycle import EDITORIAL, transition_allowed
from .model import Model, canonical, checksum, semantic_fingerprint, utc
from .persistence import State, Store
from .producer import Context, Producer
from .quality import FieldValue, ReasonCode, unknown
from .registry import (
    Contract,
    RegistryConfig,
    affected_sources,
    diff,
    fingerprints,
    registry_types,
    required_sources,
    resolve_ref,
    storage_document,
    topological,
    validate,
)
from .resolver import Bucket
from .schema import Envelope, TypeRegistry, validate_fields
from .statemachine import MachineState
from .temporal import start_case

LOGGER = logging.getLogger(__name__)


class CommandOrigin(Model):
    kind: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    client_name: str = Field(min_length=1)


class Command(Model):
    command_id: str = Field(min_length=1)
    contract_id: str
    command: str
    args: dict[str, Any]
    origin: CommandOrigin
    issued_at: datetime
    valid_until: datetime
    expected_registry_revision: int | None = None

    _times = field_validator("issued_at", "valid_until")(utc)


@dataclass
class Work:
    operation: str
    payload: Any
    future: asyncio.Future[Any] | None = None


@dataclass
class SnapshotBatch:
    observations: list[Observation]
    registry_revision: int


class Runtime:
    def __init__(
        self,
        store: Store,
        clock: Clock,
        types: TypeRegistry,
        *,
        queue_size: int = 256,
        require_history_retention: bool = False,
    ) -> None:
        self.store, self.clock, self.types = store, clock, types
        self.require_history_retention = require_history_retention
        self.installation_id: str | None = None
        self.ingress_slug: str | None = None
        self.context_index: ContextIndex | None = None
        self.state = State()
        self._config_cache: tuple[int, str, RegistryConfig | None] | None = None
        self._plans: dict[tuple[Any, ...], tuple[dict[str, str], TypeRegistry, list[Contract]]] = {}
        self.reuse_unaffected = True
        self.evaluation_stats = {"evaluated": 0, "reused": 0}
        self.queue: asyncio.Queue[Work] = asyncio.Queue(queue_size)
        self.task: asyncio.Task[None] | None = None
        self.confirmed = False
        self.persistence = False
        self.configuration_error = False
        self.first_snapshot = False
        self.bridge = "not_needed"
        self.bridge_version: str | None = None
        self.bridge_protocol_selected: int | None = None
        self.bridge_features: list[str] = []
        self.bridge_subscriptions = 0
        self.dependency_connections: dict[str, bool] = {}
        self.ha = "not_needed"
        self.ha_changed_at = self.clock.now_utc()
        self.next_retention = self.clock.now_utc()
        self.retention_receipt: dict[str, Any] | None = None
        self.mqtt = "disabled"
        self.mqtt_mode: str | None = None
        self.mqtt_external_settings_present: bool | None = None
        self.latest: dict[str, Observation] = {}
        self.overflow = False
        self.snapshot_pending = False
        self.snapshot_revision: int | None = None
        self.overflow_bindings: set[str] = set()
        self.overflow_serial = 0
        self.next_evaluation: datetime | None = None
        self.evaluation_alarm: Alarm | None = None
        self.last_clock = clock.now_utc()
        self.resubscribe = asyncio.Event()
        self.revision_changed = asyncio.Event()
        self.mqtt_revision_changed = asyncio.Event()
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.latches: dict[str, Bucket] = {}
        self.pending_latches: dict[str, Bucket] | None = None
        self.stopping = False
        self.commit_duration = 0.0
        self.last_heartbeat = clock.monotonic()
        self.refresh_requests: asyncio.Queue[tuple[int, str]] = asyncio.Queue(256)
        self.pending_refresh: set[str] = set()
        self.refresh_overflow = 0
        self.entity_discovery: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None
        self.discovered_areas: list[dict[str, Any]] | None = None
        self.service_jobs_changed = asyncio.Event()

    @property
    def config(self) -> RegistryConfig | None:
        revision = self.state.tables["registry_revision"].get(str(self.state.active_revision), {})
        key = (self.state.active_revision, str(revision.get("checksum", "")))
        if self._config_cache is None or self._config_cache[:2] != key:
            self._config_cache = (*key, self._config(self.state))
        return self._config_cache[2]

    def _evaluation_plan(
        self, state: State, config: RegistryConfig
    ) -> tuple[dict[str, str], TypeRegistry, list[Contract]]:
        revision = state.tables["registry_revision"].get(str(state.active_revision), {})
        key = (
            state.active_revision,
            revision.get("checksum"),
            tuple((key, id(value)) for key, value in self.types.types.items()),
        )
        if key not in self._plans:
            self._plans[key] = (
                fingerprints(config, self.types),
                registry_types(config, self.types),
                topological(config),
            )
            if len(self._plans) > 4:
                self._plans.pop(next(iter(self._plans)))
        return self._plans[key]

    @staticmethod
    def _config(state: State) -> RegistryConfig | None:
        revision = state.tables["registry_revision"].get(str(state.active_revision))
        return RegistryConfig.model_validate(revision["config"]) if revision else None

    def service_state(self) -> dict[str, Any]:
        return {
            "type": "service_state",
            "ready": self.ready,
            "publication_confirmed": self.confirmed,
            "persistence": "available" if self.persistence else "persistence_unavailable",
            "ha": self.ha if self.requires_ha_snapshot else "not_needed",
            "bridge": self.bridge if self.requires_ha_snapshot else "not_needed",
            "mqtt": self.mqtt,
            "mqtt_mode": self.mqtt_mode,
            "mqtt_external_settings_present": self.mqtt_external_settings_present,
            "reasons": self.readiness_reasons,
            "conditions": self.readiness_conditions,
            "ha_disconnected_since": self.ha_changed_at.isoformat()
            if self.bridge == "unavailable"
            else None,
            "ha_unbounded_values": self._unbounded_ha_values(),
        }

    def _unbounded_ha_values(self) -> int:
        config = self.config
        if config is None or self.bridge != "unavailable":
            return 0
        bindings = {b.source_id: b for b in config.bindings if b.adapter.kind.startswith("ha_")}
        return sum(
            1
            for source in config.sources
            if source.source_id in bindings
            and source.freshness.mode == "event_stateful"
            and assess(
                Observation.model_validate(raw)
                if (
                    raw := self.state.tables["source_observation_current"].get(
                        bindings[source.source_id].binding_id
                    )
                )
                else None,
                source.freshness,
                self.clock.now_utc(),
                source.source_id,
            ).status
            == "valid"
        )

    @property
    def readiness_reasons(self) -> list[str]:
        reasons = []
        if self.configuration_error:
            reasons.append("invalid_supervisor_options")
        if not self.persistence:
            reasons.append("persistence_unavailable")
        if self.state.active_revision == 0:
            reasons.append("no_active_registry")
        if self.require_history_retention and (
            self.config is None or self.config.history_retention is None
        ):
            reasons.append("history_retention_required")
        if self.requires_ha_snapshot and not self.first_snapshot:
            reasons.append("first_bridge_snapshot_pending")
        return reasons

    @property
    def readiness_conditions(self) -> list[dict[str, Any]]:
        config = self.config
        known = self.persistence
        return [
            {"code": "supervisor_options", "satisfied": not bool(self.configuration_error)},
            {"code": "database", "satisfied": known},
            {
                "code": "active_configuration",
                "satisfied": self.state.active_revision > 0 if known else None,
            },
            {
                "code": "history_retention",
                "satisfied": (
                    not self.require_history_retention or bool(config and config.history_retention)
                )
                if known
                else None,
            },
            {
                "code": "first_ha_state",
                "satisfied": (not self.requires_ha_snapshot or self.first_snapshot)
                if known and config
                else None,
            },
            {
                "code": "ha_connection",
                "satisfied": (
                    not self.requires_ha_snapshot or self.ha == self.bridge == "connected"
                )
                if known and config
                else None,
            },
            {"code": "publication", "satisfied": self.confirmed if known and config else None},
            {"code": "input_capacity", "satisfied": not self.queue.full()},
        ]

    def _audit(self, state: State, operation: str, payload: dict[str, Any], **details: Any) -> None:
        state.tables["diagnostic_event"][str(uuid4())] = {
            "kind": "administrative",
            "operation": operation,
            "at": self.clock.now_utc().isoformat(),
            "epoch_id": state.epoch_id,
            "registry_revision": state.active_revision,
            "actor": payload.get("_actor", {"kind": "internal", "role": "system"}),
            **details,
        }

    async def _definition_versions(self, config: RegistryConfig) -> None:
        from .validation import RegistryValidationError, ValidationIssue

        issues = []
        for index, source in enumerate(config.sources):
            profile = source.sensor_profile
            if profile:
                previous = await self.store.sensor_profile(profile.id, profile.version)
                if previous and semantic_fingerprint(previous) != semantic_fingerprint(
                    profile.model_dump(mode="json")
                ):
                    issues.append(
                        ValidationIssue(
                            code="immutable_definition_version",
                            path=f"/sources/{index}/sensor_profile/version",
                            object_id=profile.id,
                            message="immutable sensor profile version",
                        )
                    )
        for kind, objects, identity_key, version_key, getter in (
            ("definitions", config.definitions, "type_id", "type_version", self.store.definition),
            ("catalogs", config.catalogs, "catalog_id", "version", self.store.catalog),
        ):
            for index, item in enumerate(objects):
                document = item.model_dump(mode="json")
                identity = document[identity_key]
                previous = await getter(identity, document[version_key])
                if previous and checksum(
                    {k: v for k, v in previous.items() if k not in EDITORIAL}
                ) != checksum({k: v for k, v in document.items() if k not in EDITORIAL}):
                    issues.append(
                        ValidationIssue(
                            code="immutable_definition_version",
                            path=f"/{kind}/{index}/{version_key}",
                            object_id=identity,
                            message="immutable definition version"
                            if kind == "definitions"
                            else "immutable catalog version",
                        )
                    )
                if previous and not transition_allowed(
                    previous.get("lifecycle", "active"),
                    document.get("lifecycle", "active"),
                    document.get("reactivation_note"),
                    previous.get("reactivation_note"),
                ):
                    issues.append(
                        ValidationIssue(
                            code="lifecycle_transition",
                            path=f"/{kind}/{index}/lifecycle",
                            object_id=identity,
                            message="explicit lifecycle transition or new reactivation note required",
                        )
                    )
        old = {c.contract_id: c for c in self.config.contracts} if self.config else {}
        retired = {
            (d.type_id, d.type_version) for d in config.definitions if d.lifecycle == "retired"
        }
        for index, contract in enumerate(config.contracts):
            prior = old.get(contract.contract_id)
            definition = next(
                (
                    d
                    for d in config.definitions
                    if (d.type_id, d.type_version) == (contract.type_id, contract.type_version)
                ),
                None,
            )
            formula_retired = definition is not None and any(
                isinstance(rule, FormulaRule)
                and FORMULA_SCHEMAS[rule.name, rule.version].lifecycle == "retired"
                for rule in all_rules(definition)
            )
            if (
                contract.enabled
                and ((contract.type_id, contract.type_version) in retired or formula_retired)
                and (
                    prior is None
                    or not prior.enabled
                    or (prior.type_id, prior.type_version)
                    != (contract.type_id, contract.type_version)
                )
            ):
                issues.append(
                    ValidationIssue(
                        code="version_retired",
                        path=f"/contracts/{index}/type_version",
                        object_id=contract.contract_id,
                        message="retired version cannot be newly bound",
                    )
                )
        old_bindings = {b.binding_id: b for b in self.config.bindings} if self.config else {}
        retired_catalogs = {c.catalog_id for c in config.catalogs if c.lifecycle == "retired"}
        for index, binding in enumerate(config.bindings):
            if (
                binding.transform
                and binding.transform.catalog_id in retired_catalogs
                and (
                    binding.binding_id not in old_bindings
                    or old_bindings[binding.binding_id].source_id != binding.source_id
                    or old_bindings[binding.binding_id].transform != binding.transform
                )
            ):
                issues.append(
                    ValidationIssue(
                        code="version_retired",
                        path=f"/bindings/{index}/transform",
                        object_id=binding.binding_id,
                        message="retired catalog cannot be newly bound",
                    )
                )
        if issues:
            raise RegistryValidationError(issues)

    async def _source_types(self, config: RegistryConfig) -> None:
        from .registry import Source
        from .validation import RegistryValidationError, ValidationIssue

        issues = []
        for index, source in enumerate(config.sources):
            for document in await self.store.source_history(
                source.source_id, include_revision=True
            ):
                previous_revision = document.pop("_registry_revision")
                historical = await self.store.get("registry_revision", str(previous_revision))
                if historical and historical["config"].get("config_schema_version") == 4:
                    old = next(
                        s
                        for s in RegistryConfig.model_validate(historical["config"]).sources
                        if s.source_id == source.source_id
                    )
                else:
                    old = Source.model_validate(document)
                schema, previous = source.value_schema, old.value_schema
                inferred = {"boolean": "bool", "numeric": "number", "text": "text"}
                checks = (
                    ("source_type", source.source_type != old.source_type),
                    (
                        "value_schema/type",
                        (schema.type if schema else inferred.get(source.source_type))
                        != (previous.type if previous else inferred.get(old.source_type)),
                    ),
                    (
                        "value_schema/unit",
                        (schema.unit if schema else None) != (previous.unit if previous else None),
                    ),
                    (
                        "value_schema/value_catalog",
                        bool(
                            previous
                            and not set(previous.value_catalog).issubset(
                                schema.value_catalog if schema else ()
                            )
                        ),
                    ),
                )
                for field, changed in checks:
                    if changed:
                        old_value = (
                            old.source_type
                            if field == "source_type"
                            else {
                                "value_schema/type": previous.type
                                if previous
                                else inferred.get(old.source_type),
                                "value_schema/unit": previous.unit if previous else None,
                                "value_schema/value_catalog": list(previous.value_catalog)
                                if previous
                                else [],
                            }[field]
                        )
                        new_value = (
                            source.source_type
                            if field == "source_type"
                            else {
                                "value_schema/type": schema.type
                                if schema
                                else inferred.get(source.source_type),
                                "value_schema/unit": schema.unit if schema else None,
                                "value_schema/value_catalog": list(schema.value_catalog)
                                if schema
                                else [],
                            }[field]
                        )
                        old_text = str(old_value) if old_value is not None else None
                        new_text = str(new_value) if new_value is not None else None
                        issues.append(
                            ValidationIssue(
                                code="immutable_source_catalog"
                                if field.endswith("value_catalog")
                                else "immutable_source_type",
                                path=f"/sources/{index}/{field}",
                                object_id=source.source_id,
                                message=f"{field}: {old_text!r} aus Version {previous_revision} → {new_text!r}. Übernommene Quellen behalten Typ und Einheit; ihre Werteliste darf nur ergänzt werden.",
                                parameters={
                                    "source_name": source.display_name
                                    or old.display_name
                                    or source.source_id,
                                    "field": field,
                                    "oldValue": old_text,
                                    "newValue": new_text,
                                    "revision": previous_revision,
                                },
                            )
                        )
        if issues:
            raise RegistryValidationError(list({(i.code, i.path): i for i in issues}.values()))

    @property
    def live(self) -> bool:
        return (
            self.task is not None
            and not self.task.done()
            and self.clock.monotonic() - self.last_heartbeat < 5
        )

    @property
    def ready(self) -> bool:
        return not self.readiness_reasons

    @property
    def requires_ha_snapshot(self) -> bool:
        config = self.config
        if config is None:
            return False
        needed = required_sources(config)
        if any(
            contract.enabled and (contract.refresh_plan or contract.ha_projection)
            for contract in config.contracts
        ):
            return True
        if any(
            key.startswith("notice:") and (row.get("acknowledged") or row.get("desired"))
            for key, row in self.state.tables["service_state"].items()
        ) or any(
            job.get("kind") == "notice" and job["status"] in {"planned", "sent"}
            for job in self.state.tables["service_job"].values()
        ):
            return True
        if config.notifications and (
            config.notifications.signal.enabled or config.notifications.configuration.enabled
        ):
            return True
        return any(
            (b.source_id in needed or b.adapter.kind == "ha_calendar")
            and (b.adapter.kind.startswith("ha_") or b.adapter.kind == "connectivity")
            for b in config.bindings
        )

    async def start(self) -> None:
        self.state = await self.store.load()
        self.persistence = True
        await self._restore("process_restart")
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="processor")

    def _gap(self, state: State, reason: str) -> None:
        for sensor_key in list(state.tables["service_state"]):
            if sensor_key.startswith("sensor:"):
                del state.tables["service_state"][sensor_key]
        key = str(uuid4())
        state.tables["history_gap"][key] = {
            "reason": reason,
            "at": self.clock.now_utc().isoformat(),
            "epoch_id": state.epoch_id,
        }
        LOGGER.warning("history_gap", extra={"reason": reason})

    async def _restore(self, reason: str) -> None:
        from .managed_catalog import validate_heads, verify_stored_heads

        validate_heads(self.state, self.config)
        await verify_stored_heads(self.store, self.state, self.config)
        self.first_snapshot = False
        if self.context_index:
            self.context_index.clear_registered()
        state = self.state.clone()
        from .authoring import migrate

        await migrate(self, state, self.clock.now_utc())
        old_epoch = state.epoch_id
        state.epoch_id = str(uuid4())
        if old_epoch:
            self._gap(state, reason)
        state.tables["runtime_epoch"][state.epoch_id] = {
            "started_at": self.clock.now_utc().isoformat(),
            "ended_at": None,
        }
        for observation_data in state.tables["source_observation_current"].values():
            await self._record_observation(state, Observation.model_validate(observation_data))
        from .service_jobs import restore_jobs

        restore_jobs(state, self.clock.now_utc())
        if reason == "process_restart":
            from .mqtt_restore import restore as restore_mqtt

            for binding_id, observation in list(state.tables["source_observation_current"].items()):
                if observation["adapter"] == "mqtt" and self.config:
                    restored = restore_mqtt(
                        Observation.model_validate(observation),
                        self.config,
                        self.types,
                        self.clock.now_utc(),
                    )
                    state.tables["source_observation_current"][binding_id] = (
                        restored.storage_document()
                    )
                elif observation["adapter"] not in {"ha_calendar", "manual"}:
                    observation["observation_kind"] = "restore"
        for node in state.tables["node_state"].values():
            for context in self._temporal_contexts(node):
                if "temporal" in context:
                    context["restore_pending"] = copy.deepcopy(context["temporal"])
        config = (
            self.config
            if state.active_revision == self.state.active_revision
            else self._config(state)
        )
        if config:
            self._evaluate(state, config, origin="restore", initial_latches={})
        else:
            self.pending_latches = {}
        await self._commit(state, publication=bool(config), resync=True)
        self._broadcast(self.service_state())

    async def submit(self, operation: str, payload: Any = None) -> Any:
        if self.stopping:
            raise RuntimeError("going_away")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self.queue.put(Work(operation, payload, future))
        return await asyncio.shield(future)

    def ingest(self, observation: Observation) -> None:
        if self.stopping:
            return
        if observation.registry_revision not in {None, self.state.active_revision}:
            return
        if not self.persistence:
            self.latest[observation.binding_id] = observation
            return
        try:
            self.queue.put_nowait(Work("observation", observation))
        except asyncio.QueueFull:
            self.latest[observation.binding_id] = observation
            self.overflow = True
            self.overflow_serial += 1
            self.resubscribe.set()

    def tick(self) -> None:
        self.last_heartbeat = self.clock.monotonic()
        if not self.queue.full() and not self.stopping:
            self.queue.put_nowait(Work("tick", None))

    def ingest_snapshot(self, observations: list[Observation], revision: int | None = None) -> None:
        if self.stopping:
            return
        if revision is not None and revision != self.state.active_revision:
            return
        try:
            self.queue.put_nowait(
                Work(
                    "snapshot",
                    SnapshotBatch(
                        observations, self.state.active_revision if revision is None else revision
                    ),
                )
            )
        except asyncio.QueueFull:
            self.latest.update({o.binding_id: o for o in observations})
            self.overflow = True
            self.overflow_serial += 1
            self.snapshot_pending = True
            self.snapshot_revision = self.state.active_revision if revision is None else revision

    async def run(self) -> None:
        while True:
            work = await self.queue.get()
            try:
                result = await self.process(work.operation, work.payload)
                if work.future and not work.future.done():
                    work.future.set_result(result)
            except Exception as error:
                from .authoring import AuthoringError
                from .validation import RegistryValidationError

                expected_error = isinstance(error, (RegistryValidationError, AuthoringError)) or (
                    type(error) is ValueError
                    and str(error)
                    in {
                        "draft_limit",
                        "draft_identity_conflict",
                        "draft_version_conflict",
                        "active_revision_conflict",
                        "sensitive key",
                        "credential URL",
                        "document too large",
                        "document too deep",
                        "configuration object required",
                        "invalid_draft_id",
                        "invalid_draft_version",
                        "invalid_draft_display_name",
                        "invalid_expected_revision",
                        "revision_not_found",
                    }
                )
                LOGGER.log(
                    logging.INFO if expected_error else logging.ERROR,
                    "processor_request_failed",
                    extra={"operation": work.operation, "error_type": type(error).__name__},
                )
                if work.future and not work.future.done():
                    work.future.set_exception(error)
            finally:
                self.queue.task_done()

    async def process(self, operation: str, payload: Any) -> Any:
        if operation == "snapshot" and isinstance(payload, SnapshotBatch):
            if payload.registry_revision != self.state.active_revision:
                return None
            payload = payload.observations
        if operation == "observation" and payload.registry_revision not in {
            None,
            self.state.active_revision,
        }:
            return None
        if operation == "recover":
            from .editorial import keep_newer

            current_editorial = copy.deepcopy(self.state.tables["editorial_current"])
            last_sequence = self.state.publication_seq
            self.state = await self.store.load()
            keep_newer(current_editorial, self.state.tables["editorial_current"])
            rollback = self.state.publication_seq < last_sequence
            self.state.publication_seq = max(last_sequence, self.state.publication_seq)
            self.persistence = True
            self.first_snapshot = False
            for observation in self.state.tables["source_observation_current"].values():
                if observation["adapter"] not in {"ha_calendar", "manual"}:
                    observation["observation_kind"] = "restore"
            # Coalesced outage input is snapshot evidence, never replayed as events.
            buffered = dict(self.latest)
            for obs in buffered.values():
                if obs.registry_revision not in {None, self.state.active_revision}:
                    continue
                if self._older_than_stored(self.state, obs):
                    continue
                self.state.tables["source_observation_current"][obs.binding_id] = obs.model_copy(
                    update={"observation_kind": "snapshot"}
                ).storage_document()
            await self._restore("database_rollback" if rollback else "db_outage")
            for key, value in buffered.items():
                if self.latest.get(key) is value:
                    self.latest.pop(key)
            self.overflow = bool(self.latest)
            self.resubscribe.set()
            self.mqtt_revision_changed.set()
            return None
        if operation == "bridge_state":
            previous_bridge = self.bridge
            previous_state = (self.bridge, self.ha)
            self.bridge, self.ha = payload
            if previous_state != payload:
                self.ha_changed_at = self.clock.now_utc()
            self.dependency_connections["ha"] = self.bridge == "connected"
            self._broadcast(self.service_state())
            if previous_state != payload and self.persistence and self.config:
                staged = self.state.clone()
                if self.bridge == "connected":
                    for key, calendar_cache in staged.tables["service_state"].items():
                        if key.startswith("calendar:"):
                            calendar_cache["next_at"] = self.clock.now_utc().isoformat()
                bindings = {
                    b.binding_id for b in self.config.bindings if b.adapter.kind.startswith("ha_")
                }
                if self.bridge == "unavailable" and previous_bridge != "unavailable":
                    self._gap(staged, "ha_disconnect")
                    self._invalidate(staged, self.config, bindings)
                self._evaluate(staged, self.config, origin="revision")
                try:
                    await self._commit(staged, publication=True)
                    self._broadcast(self.service_state())
                except Exception as error:
                    LOGGER.warning(
                        "transport_status_uncommitted", extra={"error_type": type(error).__name__}
                    )
            return None
        if operation == "mqtt_state":
            previous_mqtt = self.mqtt
            self.mqtt = str(payload)
            self.dependency_connections["mqtt"] = self.mqtt == "connected"
            self._broadcast(self.service_state())
            if (
                self.mqtt == "disconnected"
                and previous_mqtt == "connected"
                and self.persistence
                and self.config
            ):
                staged = self.state.clone()
                bindings = {b.binding_id for b in self.config.bindings if b.adapter.kind == "mqtt"}
                self._gap(staged, "mqtt_disconnect")
                self._invalidate(staged, self.config, bindings)
                for binding_id in bindings:
                    if binding_id in staged.tables["source_observation_current"]:
                        staged.tables["source_observation_current"][binding_id]["availability"] = (
                            "unavailable"
                        )
                self._evaluate(staged, self.config, origin="revision")
                try:
                    await self._commit(staged, publication=True)
                except Exception as error:
                    LOGGER.warning(
                        "transport_status_uncommitted", extra={"error_type": type(error).__name__}
                    )
            return None
        if operation == "snapshot_complete":
            self.first_snapshot = True
            self._broadcast(self.service_state())
            return None
        if not self.persistence:
            if operation in {"tick", "gap"}:
                return None
            if operation == "observation":
                self.latest[payload.binding_id] = payload
                return None
            if operation == "snapshot":
                self.latest.update({o.binding_id: o for o in payload})
                self.snapshot_pending = True
                self.snapshot_revision = self.state.active_revision
                return None
            raise RuntimeError("persistence_unavailable")
        # Overflow recovery is its own transaction. A following validation, OCC
        # conflict, duplicate event or failed command cannot discard the buffer.
        if self.overflow:
            await self._drain_overflow()
        if operation in {"retention_preview", "retention_run"}:
            if self.config is None or self.config.history_retention is None:
                raise ValueError("history_retention_not_configured")
            return await self.store.maintain_history(
                self.state,
                self.clock.now_utc(),
                self.config.history_retention,
                dry_run=operation == "retention_preview",
            )
        if operation == "tick":
            try:
                await self.store.probe()
            except Exception:
                self._unavailable()
                raise
            now = self.clock.now_utc()
            if (
                self.config
                and (policy := self.config.history_retention)
                and now >= self.next_retention
            ):
                self.retention_receipt = await self.store.maintain_history(self.state, now, policy)
                self.next_retention = now + timedelta(
                    seconds=60
                    if self.retention_receipt["selected"] == policy.batch_size
                    else policy.interval_s
                )
                self._plan_evaluation()
            clock_went_back = now < self.last_clock
            self.last_clock = now
            if not clock_went_back and (self.next_evaluation is None or now < self.next_evaluation):
                return None
        state = self.state.clone()
        now = self.clock.now_utc()
        config = self._config(state)
        if operation == "portable_export":
            from .portable import export

            return export(self, state, payload)
        if operation == "portable_preview":
            from .portable import preview as portable_preview

            return portable_preview(self, state, payload)
        if operation == "portable_stage":
            from uuid import UUID

            from .portable import stage

            import_record = "stage:" + str(UUID(payload["request_id"]))
            import_digest = checksum(payload)
            import_previous = await self.store.get("catalog_import", import_record)
            if import_previous:
                if import_previous["digest"] != import_digest:
                    raise ValueError("import_plan_conflict")
                return import_previous["result"]
            result = stage(self, state, payload, payload.get("_actor"), now)
            state.tables["catalog_import"][import_record] = {
                "digest": import_digest,
                "result": result,
            }
            await self._commit(state)
            return result
        if operation == "catalog_revision":
            from .managed_catalog import definition, historical_entries

            catalog = definition(config, payload["catalog_id"], payload["definition_version"])
            entries = await historical_entries(self.store, catalog, payload["revision"])
            selected = [
                (key, row) for key, row in sorted(entries.items()) if key > payload.get("after", "")
            ]
            return {
                "items": [{"identity": key, **row} for key, row in selected[:100]],
                "total": len(entries),
                "next_cursor": selected[99][0] if len(selected) > 100 else None,
            }
        if operation in {"catalog_write", "catalog_preview"}:
            from .catalog_models import CatalogRestore, CatalogWrite, EntryChange
            from .managed_catalog import (
                affected_contracts,
                definition,
                head,
                historical_entries,
                invalidate,
                prepare,
            )
            from .registry import safe_document

            restoring = "restore_revision" in payload["request"]
            request = (CatalogRestore if restoring else CatalogWrite).model_validate(
                payload["request"]
            )
            safe_document(payload["request"])
            digest = checksum(request)
            catalog_previous = (
                await self.store.get("catalog_request", request.request_id)
                if operation == "catalog_write"
                else None
            )
            if catalog_previous:
                if catalog_previous["digest"] != digest:
                    raise ValueError("catalog_request_conflict")
                return catalog_previous["result"]
            if isinstance(request, CatalogRestore):
                catalog = definition(config, request.catalog_id, request.definition_version)
                past = await historical_entries(self.store, catalog, request.restore_revision)
                current_entries = head(state, catalog)["entries"]
                changes = []
                for key in past.keys() | current_entries.keys():
                    row = past.get(key) or {**current_entries[key], "tombstone": True}
                    changes.append(
                        EntryChange.model_validate(
                            {
                                k: v
                                for k, v in row.items()
                                if k not in {"normalized_key", "master_identity"}
                            }
                        )
                    )
                request = request.model_copy(update={"changes": tuple(changes)})
            result = prepare(
                state,
                config,
                request,
                payload.get("_actor"),
                now,
                expanded_limit=100_000 if restoring else 1000,
            )
            if operation == "catalog_preview":
                affected = affected_contracts(self.state, result)
                if config:
                    for contract in topological(config):
                        if any(
                            resolve_ref(item.ref, config)[0] in affected for item in contract.inputs
                        ):
                            affected.add(contract.contract_id)
                return {
                    "affected_keys": len(result["affected_keys"]),
                    "affected_contracts": len(affected),
                }
            if result["affected_keys"]:
                assert config is not None
                direct_catalog_contracts = affected_contracts(state, result)
                latches = invalidate(state, result, self.latches)
                failures = self._evaluate(
                    state,
                    config,
                    origin="revision",
                    initial_latches=latches,
                    catalog_affected=direct_catalog_contracts,
                )
                if failures:
                    self.pending_latches = None
                    self.pending_refresh.clear()
                    raise ValueError("catalog_evaluation_failed")
            result["publication_seq"] = state.publication_seq
            state.tables["catalog_request"][request.request_id] = {
                "digest": digest,
                "result": result,
            }
            await self._commit(state, publication=bool(result["affected_keys"]))
            return result
        if operation == "editorial_put":
            from .editorial import EditorialWrite, put

            result = put(
                self, state, EditorialWrite.model_validate(payload["request"]), payload["_actor"]
            )
            await self._commit(state)
            return result
        if operation == "editorial_area_refresh":
            from .editorial import refresh_areas

            if refresh_areas(self, state, payload):
                await self._commit(state)
            return None
        if operation == "context_register":
            from .ha_context import ContextRegistration

            registration = ContextRegistration.model_validate(payload["request"])
            index = self.context_index
            if (
                config is None
                or index is None
                or self.bridge_protocol_selected != 2
                or self.bridge != "connected"
            ):
                raise ValueError("ha_context_unavailable")
            if registration.producer_id not in {
                producer.producer_id for producer in config.context_producers
            }:
                raise ValueError("context_producer_not_allowed")
            if registration.ha_epoch_id != index.epoch or registration.context.id is None:
                raise ValueError("ha_context_epoch_conflict")
            self._audit(
                state,
                "context_register_attempt",
                {"_actor": payload.get("_actor")},
                producer_id=registration.producer_id,
                context_digest=checksum(registration.context.id),
            )
            await self._commit(state)
            index.register(registration.context, now, lifetime_s=registration.lifetime_s)
            return {
                "registered": True,
                "expires_at": index.proofs[registration.context.id][1].isoformat(),
            }
        if operation == "legacy_refresh_prepare":
            from .refresh_plan import prepare_legacy_refresh

            if config is None or payload["revision"] != state.active_revision:
                return None
            job = prepare_legacy_refresh(state, config, payload["source_id"], now)
            if job:
                await self._commit(state)
            return copy.deepcopy(job)
        if operation == "service_claim":
            if self.bridge != "connected" or self.bridge_protocol_selected != 2:
                return None
            job = next(
                (
                    job
                    for job in state.tables["service_job"].values()
                    if job["status"] == "planned"
                    and (
                        job["kind"] != "notice"
                        or state.tables["service_state"]
                        .get(job["state_key"], {})
                        .get("eligible_epoch")
                        == state.epoch_id
                    )
                ),
                None,
            )
            if job is None:
                return None
            job["status"] = "sent"
            job["sent_at"] = now.isoformat()
            await self._commit(state)
            return copy.deepcopy(job)
        if operation == "service_complete":
            from .service_jobs import complete_calendar

            job = state.tables["service_job"].get(payload["job_id"])
            if job is None or job["status"] != "sent":
                return None
            job["status"] = "finished"
            job["completed_at"] = now.isoformat()
            if job["kind"] == "notice":
                from .notifications import complete_notice

                complete_notice(state, job, payload["response"], now)
            if job["kind"] == "refresh":
                from .refresh_plan import complete_refresh

                complete_refresh(state, job, payload["response"])
            service_observation = (
                complete_calendar(state, config, job, payload["response"], now)
                if config and job["kind"] == "calendar"
                else None
            )
            if service_observation is not None:
                state.tables["source_observation_current"][service_observation.binding_id] = (
                    service_observation.storage_document()
                )
                await self._record_observation(state, service_observation)
            if config:
                self._evaluate(
                    state,
                    config,
                    origin="input" if service_observation else "timer",
                    trigger_source=service_observation.source_id if service_observation else None,
                )
            await self._commit(state, publication=True)
            return None
        if operation in {
            "draft_preview",
            "draft_complete",
            "draft_reconcile",
        } or operation.startswith("continuation_"):
            from . import authoring

            if operation == "draft_reconcile":
                return await authoring.reconcile(self, state, payload)
            if operation.startswith("continuation_"):
                result = await authoring.continuation(self, state, operation, payload, now)
                await self._commit(state)
                return result
            if operation == "draft_complete":
                prior_activation = await authoring.prior_receipt(self, state, payload)
                if prior_activation is not None:
                    return prior_activation
            draft = state.tables["registry_draft"][payload["draft_id"]]
            result = authoring.comparison(self, state, draft)
            if operation == "draft_preview":
                checked_config = validate(draft["config"], self.types)
                await self._definition_versions(checked_config)
                await self._source_types(checked_config)
                return result
            authoring.prepare_activation(self, state, draft, payload)
            if not result["no_effect"]:
                raise authoring.AuthoringError("draft_has_effects")
            revision_row = state.tables["registry_revision"][str(state.active_revision)]
            result = authoring.complete(
                state, draft, payload, revision_row, now, kind="already_active"
            )
            self._audit(state, "draft_completed", payload, draft_id=draft["draft_id"])
            await self._commit(state)
            return result
        if operation in {"draft_create", "draft_update"}:
            document = payload["config"]
            # Drafts can be incomplete, but cannot carry credentials or invalid JSON.
            from .registry import safe_document

            if not isinstance(document, dict):
                raise ValueError("configuration object required")
            safe_document(document)
            if len(canonical(document).encode()) > 2_000_000:
                raise ValueError("document too large")
            key = payload.get("draft_id", str(uuid4()))
            if not isinstance(key, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", key
            ):
                raise ValueError("invalid_draft_id")
            if operation == "draft_update" and (
                type(payload.get("draft_version")) is not int or payload["draft_version"] < 1
            ):
                raise ValueError("invalid_draft_version")
            old = state.tables["registry_draft"].get(key)
            if operation == "draft_create" and old is not None:
                raise ValueError("draft_identity_conflict")
            if operation == "draft_create" and await self.store.get(
                "registry_draft_version", f"{key}:1"
            ):
                raise ValueError("draft_identity_conflict")
            from .authoring import base_fields, guard, open_count

            if old:
                guard(state, old, current=not payload.get("preservation_save", False))
            if (
                operation == "draft_create"
                and payload.get("expected_active_revision", state.active_revision)
                != state.active_revision
            ):
                raise ValueError("active_revision_conflict")
            if operation == "draft_create" and open_count(state) >= 100:
                raise ValueError("draft_limit")
            if operation == "draft_update" and (
                old is None or payload["draft_version"] != old["draft_version"]
            ):
                raise ValueError("draft_version_conflict")
            result = {
                **(old or base_fields(state)),
                "draft_id": key,
                "draft_version": old["draft_version"] + 1 if old else 1,
                "config": document,
                "updated_at": now.isoformat(),
                "created_at": old.get("created_at", old["updated_at"]) if old else now.isoformat(),
                "base_revision": old.get("base_revision", state.active_revision)
                if old
                else state.active_revision,
                "display_name": payload.get(
                    "display_name", old.get("display_name", "") if old else ""
                ),
                **({"portable": old["portable"]} if old and "portable" in old else {}),
            }
            if not isinstance(result["display_name"], str) or len(result["display_name"]) > 256:
                raise ValueError("invalid_draft_display_name")
            state.tables["registry_draft"][key] = result
            if result.get("portable"):
                from .authoring import bind_plan

                bind_plan(result)
            from .editorial import EditorialWrite, carry_draft, put

            carry_draft(state, result, self)
            editorial_entries = payload.get("editorial", [])
            if not isinstance(editorial_entries, list) or len(editorial_entries) > 128:
                raise ValueError("invalid_editorial_batch")
            for item in editorial_entries:
                put(
                    self,
                    state,
                    EditorialWrite.model_validate(
                        {**item, "draft_id": key, "draft_version": result["draft_version"]}
                    ),
                    payload.get("_actor", {"kind": "internal", "role": "system"}),
                )
            self._audit(
                state, operation, payload, draft_id=key, draft_version=result["draft_version"]
            )
            await self._commit(state)
            return result
        if operation == "draft_delete":
            key = payload["draft_id"]
            if type(payload.get("draft_version")) is not int:
                raise ValueError("invalid_draft_version")
            old = state.tables["registry_draft"].get(key)
            if old is None or payload["draft_version"] != old["draft_version"]:
                raise ValueError("draft_version_conflict")
            from .authoring import guard

            guard(state, old, current=False)
            del state.tables["registry_draft"][key]
            for editorial_key, row in list(state.tables["editorial_draft"].items()):
                if row["draft_id"] == key:
                    del state.tables["editorial_draft"][editorial_key]
            self._audit(state, operation, payload, draft_id=key, draft_version=old["draft_version"])
            await self._commit(state)
            return {"deleted": True, "draft_id": key}
        if operation == "audit":
            self._audit(state, payload["operation"], payload, **payload.get("details", {}))
            await self._commit(state)
            return None
        if operation == "validate":
            candidate = validate(payload, self.types)
            await self._definition_versions(candidate)
            await self._source_types(candidate)
            from .capabilities import machine_description

            types = registry_types(candidate, self.types)
            machines = {}
            for contract in candidate.contracts:
                declaration = types.get(contract.type_id, contract.type_version)
                if declaration.machine_factory:
                    machines[contract.contract_id] = machine_description(
                        declaration.machine_factory(contract.parameters)
                    )
            return {
                "valid": True,
                "issues": [],
                "warnings": [
                    {
                        "code": "machine_never_recovers",
                        "path": f"/definitions/{index}/machine/reinitialize_when",
                        "object_id": definition.type_id,
                        "message": "Dieser Ablauf kann sich nach einer Änderung nie selbst erholen.",
                    }
                    for index, definition in enumerate(candidate.definitions)
                    if definition.machine
                    and definition.machine.reinitialize_when.op == "literal"
                    and getattr(definition.machine.reinitialize_when, "value", None) is False
                ],
                "diff": diff(config, candidate, self.types),
                "machine_definitions": machines,
            }
        if operation in {"activate", "rollback"}:
            from . import authoring

            if operation == "activate":
                # Every caller supplies a durable identity. Test/bootstrap helpers use
                # the same preview contract as HTTP clients.
                prior_activation = await authoring.prior_receipt(self, state, payload)
                if prior_activation is not None:
                    return prior_activation
            note = payload.get("note", "")
            if not isinstance(note, str) or len(note) > 256:
                raise ValueError("invalid_revision_note")
            note = note.strip()
            if type(payload.get("expected_active_revision")) is not int:
                raise ValueError("invalid_expected_revision")
            if payload["expected_active_revision"] != state.active_revision:
                raise ValueError("active_revision_conflict")
            if operation == "activate":
                draft = state.tables["registry_draft"][payload["draft_id"]]
                if payload["draft_version"] != draft["draft_version"]:
                    raise ValueError("draft_version_conflict")
                authoring.prepare_activation(self, state, draft, payload)
                document = draft["config"]
            else:
                historical = await self.store.get("registry_revision", str(payload["revision"]))
                if historical is None:
                    raise ValueError("revision_not_found")
                document = historical["config"]
            new_config = validate(document, self.types)
            await self._definition_versions(new_config)
            await self._source_types(new_config)
            if operation == "activate":
                from .editorial import apply_draft

                apply_draft(state, draft, self)
                from .portable import apply

                imported_latches = apply(self, state, draft, new_config, now)
            revision = max((int(r) for r in state.tables["registry_revision"]), default=0) + 1
            state.active_revision = revision
            state.tables["registry_revision"][str(revision)] = {
                "revision": revision,
                "config": storage_document(new_config),
                "checksum": checksum(storage_document(new_config)),
                "created_at": now.isoformat(),
                "actor": payload.get("_actor", {"kind": "internal", "role": "system"}),
                **({"note": note} if note else {}),
                **(
                    {
                        "draft_id": draft["draft_id"],
                        "draft_version": draft["draft_version"],
                        "activation_request_id": payload["request_id"],
                    }
                    if operation == "activate"
                    else {}
                ),
            }
            state.tables["registry_activation"][str(revision)] = {
                "revision": revision,
                "at": now.isoformat(),
                "operation": operation,
                **(
                    {
                        "draft_id": draft["draft_id"],
                        "draft_version": draft["draft_version"],
                        "activation_request_id": payload["request_id"],
                    }
                    if operation == "activate"
                    else {}
                ),
            }
            self._audit(
                state, operation, payload, revision=revision, **({"note": note} if note else {})
            )
            next_latches = imported_latches if operation == "activate" else self.latches
            if config:
                from .reconfiguration import preserve

                compatible = preserve(state, config, new_config, self.types)
                next_latches = {
                    k: v for k, v in next_latches.items() if k.partition(":")[0] in compatible
                }
            activation_result = (
                authoring.complete(
                    state, draft, payload, state.tables["registry_revision"][str(revision)], now
                )
                if operation == "activate"
                else state.tables["registry_revision"][str(revision)]
            )
            self._evaluate(
                state,
                new_config,
                origin="revision",
                reassessment=config is not None,
                initial_latches=next_latches,
            )
            await self._commit(state, publication=True)
            old_ha = (
                {b.model_dump_json() for b in config.bindings if b.adapter.kind.startswith("ha_")}
                if config
                else set()
            )
            new_ha = {
                b.model_dump_json() for b in new_config.bindings if b.adapter.kind.startswith("ha_")
            }
            if new_ha - old_ha:
                self.first_snapshot = False
            self._broadcast(self.service_state())
            LOGGER.info(
                "registry_rollback" if operation == "rollback" else "registry_activated",
                extra={"revision": revision},
            )
            self.revision_changed.set()
            self.mqtt_revision_changed.set()
            return activation_result
        if operation == "gap":
            self._gap(state, str(payload))
            if config:
                bindings = {
                    b.binding_id
                    for b in config.bindings
                    if not str(payload).startswith("mqtt") or b.adapter.kind == "mqtt"
                }
                self._invalidate(state, config, bindings)
            try:
                await self._commit(state)
            except Exception as error:
                LOGGER.warning("gap_uncommitted", extra={"error_type": type(error).__name__})
            return None
        if operation == "command":
            return await self._command(state, Command.model_validate(payload))
        if operation == "manual_restart_preview":
            from .manual_restart import preview

            return preview(self, state, payload)
        if operation == "manual_restart":
            from .manual_restart import execute

            return await execute(self, state, payload)
        if operation == "manual_source_enter":
            from .manual_source import enter

            return await enter(self, state, payload)
        if operation == "snapshot":
            for obs in payload:
                if (
                    config
                    and obs.binding_id in {b.binding_id for b in config.bindings}
                    and not self._older_than_stored(state, obs)
                ):
                    state.tables["source_observation_current"][obs.binding_id] = (
                        obs.storage_document()
                    )
                    await self._record_observation(state, obs)
        elif operation == "observation":
            obs = payload
            if config is None or obs.binding_id not in {b.binding_id for b in config.bindings}:
                return None
            stored = state.tables["source_observation_current"].get(obs.binding_id)
            previous = Observation.model_validate(stored) if stored else None
            from .mqtt_restore import retained_replay

            if retained_replay(obs, previous, config, self.types, now):
                return None
            # Overflow drains the newest coalesced observation before older queue
            # entries. Quality changes must obey measurement order too. Without
            # comparable source times, keep the conservative negative-evidence
            # path below; receipt time must never manufacture a measurement.
            if is_older(obs, previous):
                return None
            if (
                not is_new(obs, previous)
                and previous is not None
                and obs.observation_kind != "snapshot"
                and not (
                    (
                        obs.availability != "available"
                        or obs.ha_restored
                        or obs.mqtt_retained
                        or obs.assumed_state
                        or obs.invalid_timestamp
                        or obs.transform_error
                    )
                    and (
                        obs.availability,
                        obs.ha_restored,
                        obs.mqtt_retained,
                        obs.assumed_state,
                        obs.invalid_timestamp,
                        obs.transform_error,
                    )
                    != (
                        previous.availability,
                        previous.ha_restored,
                        previous.mqtt_retained,
                        previous.assumed_state,
                        previous.invalid_timestamp,
                        previous.transform_error,
                    )
                )
            ):
                return None
            from .mqtt_restore import remember as remember_mqtt

            obs = remember_mqtt(obs, config, self.types, self.clock.now_utc())
            state.tables["source_observation_current"][obs.binding_id] = obs.storage_document()
            await self._record_observation(state, obs)
        elif operation != "tick":
            raise ValueError("unknown runtime operation")
        if config:
            origin = "snapshot" if operation == "snapshot" else "timer"
            if operation == "observation":
                origin = (
                    "input" if payload.observation_kind in {"live_change", "report"} else "revision"
                )
            self._evaluate(
                state,
                config,
                origin=origin,
                trigger_source=payload.source_id if operation == "observation" else None,
            )
            # Timer evaluation is bounded by current state; persist only actual
            # context/evidence/quality changes, including due temporal operations.
            if (
                state.tables != self.state.tables
                or state.publication_seq != self.state.publication_seq
            ):
                await self._commit(state, publication=True)
            else:
                self.pending_latches = None
                self._plan_evaluation()
        elif operation != "tick":
            await self._commit(state)
        if operation == "snapshot":
            self.first_snapshot = True
            if self.installation_id and config and config.notifications:
                await self._commit(self.state.clone())
            self._broadcast(self.service_state())
        return None

    @staticmethod
    def _older_than_stored(state: State, observation: Observation) -> bool:
        stored = state.tables["source_observation_current"].get(observation.binding_id)
        return is_older(observation, Observation.model_validate(stored) if stored else None)

    async def _record_observation(self, state: State, observation: Observation) -> None:
        if observation.reference not in state.tables["source_observation_history"] and (
            await self.store.get("source_observation_history", observation.reference) is None
        ):
            state.tables["source_observation_history"][observation.reference] = (
                observation.storage_document()
            )

    def _invalidate(self, state: State, config: RegistryConfig, bindings: set[str]) -> None:
        affected = {b.source_id for b in config.bindings if b.binding_id in bindings}
        affected = affected_sources(config, affected)
        for contract in topological(config):
            if not any(resolve_ref(i.ref, config)[0] in affected for i in contract.inputs):
                continue
            affected.add(contract.contract_id)
            node = state.tables["node_state"].get(contract.contract_id, {})
            for context in self._temporal_contexts(node):
                if "temporal" in context:
                    context["restore_pending"] = copy.deepcopy(context["temporal"])
                    context["restore_pending"]["grace_armed"] = False
                    context["temporal"].update(
                        since_at=None,
                        grace_armed=False,
                        gap_at=self.clock.now_utc().isoformat(),
                    )

    @staticmethod
    def _temporal_contexts(node: dict[str, Any]) -> list[dict[str, Any]]:
        rules = node.get("rules", {})
        return [
            node,
            *(
                context
                for context in (rules.values() if isinstance(rules, dict) else ())
                if isinstance(context, dict)
            ),
        ]

    async def _drain_overflow(self) -> None:
        serial = self.overflow_serial
        buffered = dict(self.latest)
        state = self.state.clone()
        config = self._config(state)
        self._gap(state, "ingest_overflow")
        if config:
            self._invalidate(state, config, set(buffered) | self.overflow_bindings)
            for binding_id in self.overflow_bindings:
                if binding_id in state.tables["source_observation_current"]:
                    state.tables["source_observation_current"][binding_id]["availability"] = (
                        "unavailable"
                    )
        for obs in buffered.values():
            if obs.registry_revision not in {None, state.active_revision}:
                continue
            if self._older_than_stored(state, obs):
                continue
            state.tables["source_observation_current"][obs.binding_id] = obs.model_copy(
                update={"observation_kind": "snapshot"}
            ).storage_document()
            await self._record_observation(
                state, obs.model_copy(update={"observation_kind": "snapshot"})
            )
        if config:
            self._evaluate(state, config, origin="revision")
        await self._commit(state, publication=True)
        for key, value in buffered.items():
            if self.latest.get(key) is value:
                self.latest.pop(key)
        newer_overflow = self.overflow_serial != serial
        self.overflow = bool(self.latest) or newer_overflow
        if not newer_overflow:
            self.overflow_bindings.clear()
        if self.snapshot_pending and not self.overflow:
            if self.snapshot_revision in {None, state.active_revision}:
                self.first_snapshot = True
            self.snapshot_pending = False
            self._broadcast(self.service_state())

    def _inputs(
        self,
        contract: Contract,
        config: RegistryConfig,
        state: State,
        *,
        trigger_source: str | None = None,
    ) -> dict[str, FieldValue]:
        now = self.clock.now_utc()
        by_source = {b.source_id: b.binding_id for b in config.bindings}
        sources = {s.source_id: s for s in config.sources}
        observations = {
            k: Observation.model_validate(v)
            for k, v in state.tables["source_observation_current"].items()
        }
        dependencies = {d.dependency_id: d for d in config.dependencies}

        def source_value(ref: str, visited: set[str]) -> FieldValue:
            if ref in visited:
                return unknown(ReasonCode.DEPENDENCY_UNAVAILABLE, ref, now)
            source = sources[ref]
            manual_observation = observations.get(by_source.get(ref, ""))
            if source.value_origin and manual_observation:
                recorded_origins = tuple(
                    point.value_origin for point in manual_observation.forecast
                ) or ((manual_observation.value_origin,) if manual_observation.value_origin else ())
                if not recorded_origins or any(
                    (item.kind, item.provider)
                    != (source.value_origin.kind, source.value_origin.provider)
                    for item in recorded_origins
                ):
                    return unknown(ReasonCode.RESTORE_INCOMPATIBLE, ref, now)
            if (
                source.origin == "manual"
                and manual_observation
                and manual_observation.manual
                and manual_observation.manual.source_fingerprint
                != self._evaluation_plan(state, config)[0][ref]
            ):
                return unknown(ReasonCode.RESTORE_INCOMPATIBLE, ref, now)
            calendar_binding = next(
                (
                    binding
                    for binding in config.bindings
                    if binding.source_id == ref and binding.adapter.kind == "ha_calendar"
                ),
                None,
            )
            if calendar_binding:
                cache = state.tables["service_state"].get(
                    f"calendar:{calendar_binding.binding_id}", {}
                )
                if (
                    not cache.get("expires_at")
                    or datetime.fromisoformat(cache["expires_at"]) <= now
                ):
                    return unknown(ReasonCode.INPUT_STALE, ref, now)
            dependency = dependencies[source.dependency_id]
            available = self.dependency_connections.get(dependency.kind, True)
            if dependency.kind == "ha" and dependency.monitor == "internal":
                available = True  # Transport loss is not sensor evidence.
            if dependency.monitor == "source":
                monitor = source_value(dependency.source_id or "", visited | {ref})
                available = (
                    monitor.usable(now) and monitor.quality == "healthy" and monitor.value is True
                )
            if not available:
                return unknown(ReasonCode.DEPENDENCY_UNAVAILABLE, source.dependency_id, now)
            liveness = observations.get(by_source.get(source.freshness.liveness_source or "", ""))
            observation = observations.get(by_source.get(ref, ""))
            if (
                observation
                and observation.mqtt_revalidated
                and observation.mqtt_device_proof != self._evaluation_plan(state, config)[0][ref]
            ):
                observation = observation.model_copy(update={"mqtt_revalidated": False})
            value = assess(observation, source.freshness, now, ref, liveness)
            if source.sensor_profile:
                from .sensor_profile import assess_sensor

                reference_spec = source.sensor_profile.stuck.reference
                reference_value = (
                    source_value(reference_spec.ref, visited | {ref}) if reference_spec else None
                )
                peer_observation = (
                    observations.get(by_source.get(reference_spec.ref, ""))
                    if reference_spec
                    else None
                )
                if peer_observation and peer_observation.observation_kind not in {
                    "report",
                    "live_change",
                }:
                    reference_value = None
                value = assess_sensor(
                    value,
                    observations.get(by_source.get(ref, "")),
                    source,
                    reference_value,
                    state.tables["service_state"],
                    self._evaluation_plan(state, config)[0][ref],
                    now,
                    new_report=trigger_source == ref,
                )
            return value

        result: dict[str, FieldValue] = {}
        for item in contract.inputs:
            ref, field = resolve_ref(item.ref, config)
            if field is not None:
                envelope = state.tables["contract_state_current"].get(ref)
                result[item.name] = (
                    FieldValue.model_validate(envelope["fields"][field])
                    if envelope
                    else unknown(ReasonCode.DEPENDENCY_UNAVAILABLE, ref, now)
                )
            else:
                result[item.name] = source_value(ref, set())
        return result

    def _evaluate(
        self,
        state: State,
        config: RegistryConfig,
        *,
        origin: str,
        machine_override: dict[str, MachineState] | None = None,
        trigger_source: str | None = None,
        initial_latches: dict[str, Bucket] | None = None,
        catalog_affected: set[str] | None = None,
        reassessment: bool = False,
    ) -> set[str]:
        """Return transaction-local failures after discarding their staged producer effects."""
        evaluation_errors: set[str] = set()
        from .service_jobs import plan_calendars

        plan_calendars(state, config, self.clock.now_utc())
        calendar_changed = self._calendar_observations(state, config, origin)
        now, seq = self.clock.now_utc(), state.publication_seq + 1
        self.pending_latches = copy.deepcopy(
            self.latches if initial_latches is None else initial_latches
        )
        fps, types, order = self._evaluation_plan(state, config)
        profiled_sources = {
            f"sensor:{source.source_id}" for source in config.sources if source.sensor_profile
        }
        for sensor_key in list(state.tables["service_state"]):
            if sensor_key.startswith("sensor:") and sensor_key not in profiled_sources:
                del state.tables["service_state"][sensor_key]
        self.evaluation_stats = {"evaluated": 0, "reused": 0}
        can_reuse = (
            self.reuse_unaffected
            and origin == "input"
            and trigger_source is not None
            and machine_override is None
            and state.active_revision == self.state.active_revision
            and now >= self.last_clock
            and (self.next_evaluation is None or self.next_evaluation > now)
        )
        if catalog_affected is not None:
            can_reuse = True
        self.pending_refresh = set()
        previous_config = self.config
        if previous_config:
            new_contracts = {c.contract_id: c for c in config.contracts}
            for old in previous_config.contracts:
                replacement = new_contracts.get(old.contract_id)
                old_node = state.tables["node_state"].get(old.contract_id, {})
                if old.enabled and (
                    replacement is None
                    or not replacement.enabled
                    or old_node.get("fingerprint") != fps.get(old.contract_id)
                ):
                    self._dispose(old, old_node, state)
                    old_node.pop("trace", None)
        affected = affected_sources(
            config, ({trigger_source} if trigger_source else set()) | calendar_changed
        )
        previous_contracts = state.tables["contract_state_current"]
        state.tables["contract_state_current"] = {}
        for contract in order:
            key = contract.contract_id
            lifecycle = state.tables["contract_lifecycle"].get(key)
            if not contract.enabled:
                state.tables["contract_lifecycle"][key] = {
                    "ever_active": bool(lifecycle and lifecycle.get("ever_active")),
                    "enabled": False,
                }
                continue
            inputs = self._inputs(
                contract,
                config,
                state,
                trigger_source=trigger_source if origin == "input" else None,
            )
            triggered = (catalog_affected is not None and key in catalog_affected) or any(
                resolve_ref(item.ref, config)[0] in affected for item in contract.inputs
            )
            if triggered:
                affected.add(key)
            if (
                can_reuse
                and not triggered
                and key in previous_contracts
                and state.tables["node_state"].get(key, {}).get("fingerprint") == fps[key]
            ):
                state.tables["contract_state_current"][key] = previous_contracts[key]
                self.evaluation_stats["reused"] += 1
                continue
            self.evaluation_stats["evaluated"] += 1
            contract_origin = "revision" if origin == "input" and not triggered else origin
            if origin == "tick" and triggered:
                contract_origin = "input"
            if origin == "tick" and any(
                datetime.fromisoformat(at) <= now
                for at in state.tables["node_state"].get(key, {}).get("clock_due", [])
            ):
                contract_origin = "input"
            node = state.tables["node_state"].get(key)
            case = start_case(
                ever_active=bool(lifecycle and lifecycle.get("ever_active")),
                context=node,
                fingerprint=fps[key],
                stale=bool(lifecycle and not lifecycle.get("enabled")),
            )
            if node is None or node.get("fingerprint") != fps[key] or case == "stale":
                node = {"fingerprint": fps[key]}
            machine_state: MachineState | None = None
            declaration = types.get(contract.type_id, contract.type_version)
            original_node = copy.deepcopy(node)
            original_latches = copy.deepcopy(self.pending_latches)
            refresh: set[str] = set()
            try:
                result, node, machine_state = declaration.evaluate(
                    self.clock,
                    self.pending_latches,
                    contract,
                    inputs,
                    node,
                    state,
                    contract_origin,
                    case,
                    (machine_override or {}).get(key),
                    **(
                        {"reassessment": reassessment}
                        if isinstance(declaration.evaluate, Producer)
                        else {}
                    ),
                )
                for input_name in node.pop("refresh_requests", []):
                    if input_name not in declaration.refresh_inputs:
                        raise ValueError("undeclared refresh capability")
                    ref = next(i.ref for i in contract.inputs if i.name == input_name)
                    source_id, field = resolve_ref(ref, config)
                    if field is not None:
                        raise ValueError("refresh requires source binding")
                    refresh.add(source_id)
                fields = validate_fields(
                    declaration.schema,
                    {"value": result} if isinstance(result, FieldValue) else result,
                    now,
                    inputs,
                    declaration.grace_outputs,
                )
                self.pending_refresh.update(refresh)
            except Exception as error:
                evaluation_errors.add(key)
                LOGGER.error(
                    "evaluation_error",
                    extra={"contract_id": key, "error_type": type(error).__name__},
                )
                node, self.pending_latches, machine_state = original_node, original_latches, None
                fields = {
                    name: unknown(ReasonCode.EVALUATION_ERROR, key, now)
                    for name in declaration.schema.fields
                }
            required = [fields[k] for k, v in declaration.schema.fields.items() if v.required]
            healthy = all(v.quality == "healthy" for v in required)
            available = all(
                fields[k].quality == "healthy" for k in declaration.schema.available_projection
            )
            envelope = Envelope(
                contract_id=key,
                type={"id": contract.type_id, "version": contract.type_version},
                contract_health="healthy" if healthy else "degraded",
                fields=fields,
                available=available,
                state_machine=machine_state.model_dump(mode="json") if machine_state else None,
                computed_at=now,
                published_at=now,
                publication_seq=seq,
                registry_revision=state.active_revision,
                epoch_id=state.epoch_id,
                fixture=declaration.schema.fixture,
            )
            state.tables["node_state"][key] = node
            state.tables["contract_lifecycle"][key] = {
                "ever_active": True,
                "enabled": True,
                "start_case": case,
            }
            data_envelope = envelope.model_dump(mode="json")
            previous_envelope = previous_contracts.get(key)
            # Reasons describe an interval; an unchanged reason retains its onset.
            if previous_envelope:
                for name, field in data_envelope["fields"].items():
                    old_reasons = previous_envelope["fields"].get(name, {}).get("reasons", [])
                    for reason in field["reasons"]:
                        old_reason = next(
                            (
                                r
                                for r in old_reasons
                                if all(
                                    r.get(k) == reason.get(k)
                                    for k in ("code", "input", "source_id", "detail")
                                )
                            ),
                            None,
                        )
                        if old_reason:
                            reason["since"] = old_reason["since"]
            volatile = {"computed_at", "published_at", "publication_seq"}
            same = previous_envelope is not None and {
                k: v for k, v in data_envelope.items() if k not in volatile
            } == {k: v for k, v in previous_envelope.items() if k not in volatile}
            state.tables["contract_state_current"][key] = (
                previous_envelope if same and previous_envelope is not None else data_envelope
            )
            if not same:
                state.tables["contract_state_history"][f"{seq}:{key}"] = data_envelope
            if machine_state:
                previous_sm = state.tables["sm_instance"].get(key)
                data = machine_state.model_dump(mode="json")
                state.tables["sm_instance"][key] = data
                if previous_sm != data:
                    state.tables["sm_episode"][machine_state.episode_id] = {
                        "contract_id": key,
                        **data,
                    }
                    state.tables["sm_transition"][str(uuid4())] = {"contract_id": key, **data}
                if machine_state.deadline and previous_sm != data:
                    state.tables["deadline"][machine_state.deadline.deadline_id] = {
                        "contract_id": key,
                        **machine_state.deadline.model_dump(mode="json"),
                    }
            elif declaration.machine_factory and not node.get("machine"):
                state.tables["sm_instance"].pop(key, None)
        if (
            state.tables["contract_state_history"]
            or previous_contracts.keys() != state.tables["contract_state_current"].keys()
            or state.active_revision != self.state.active_revision
            or state.epoch_id != self.state.epoch_id
        ):
            state.publication_seq = seq
            state.publications[seq] = {
                "created_at": now.isoformat(),
                "epoch_id": state.epoch_id,
                "registry_revision": state.active_revision,
            }
        if self.installation_id and self.first_snapshot and origin not in {"restore", "revision"}:
            from .notifications import plan_notices

            plan_notices(state, config, now, self.installation_id, self.ingress_slug)
        from .refresh_plan import plan_refreshes

        plan_refreshes(
            state,
            config,
            now,
            origin,
            self.bridge == "connected" and self.bridge_protocol_selected == 2,
            fps,
        )
        return evaluation_errors

    def _dispose(self, contract: Contract, node: dict[str, Any], state: State) -> None:
        config = self.config
        types = registry_types(config, self.types) if config else self.types
        producer = types.get(contract.type_id, contract.type_version).evaluate
        if isinstance(producer, Producer) and node.get("producer_epoch") == state.epoch_id:
            try:
                producer.dispose(
                    Context(
                        self.clock,
                        self.pending_latches or {},
                        contract,
                        {},
                        node,
                        state,
                        "dispose",
                        "valid",
                        None,
                    )
                )
            except Exception as error:
                LOGGER.error(
                    "producer_dispose_failed",
                    extra={"contract_id": contract.contract_id, "error_type": type(error).__name__},
                )
            node.pop("producer_epoch", None)

    async def _command(self, state: State, command: Command) -> dict[str, Any]:
        digest, now = checksum(command), self.clock.now_utc()
        old = await self.store.get("command_log", command.command_id)
        if old:
            return old["result"] if old["digest"] == digest else {"status": "command_id_conflict"}
        config = self._config(state)
        contract = (
            next((c for c in config.contracts if c.contract_id == command.contract_id), None)
            if config
            else None
        )
        result: dict[str, Any] = {"command_id": command.command_id, "status": "not_supported"}
        declaration = (
            registry_types(config, self.types).get(contract.type_id, contract.type_version)
            if config and contract
            else None
        )
        override: dict[str, MachineState] = {}
        if command.valid_until <= now:
            result["status"] = "expired"
        elif command.issued_at > now or command.valid_until <= command.issued_at:
            result.update(status="rejected", reason="invalid_value")
        elif (
            command.expected_registry_revision is not None
            and command.expected_registry_revision != state.active_revision
        ):
            result.update(status="rejected", reason="active_revision_conflict")
        elif (
            contract and contract.enabled and config and declaration and declaration.machine_factory
        ):
            factory = declaration.machine_factory
            assert factory is not None
            machine = factory(contract.parameters)
            if declaration.definition:
                from .declarative import Evaluation, compile_machine
                from .declarative_models import DeclarativeDefinition

                definition = DeclarativeDefinition.model_validate(declaration.definition)
                inputs = self._inputs(contract, config, state)
                # Guards read the transaction's catalog head. A rejected command
                # must not mutate a live temporal slot or its dependency trace.
                context = Context(
                    self.clock,
                    copy.deepcopy(self.latches),
                    contract,
                    inputs,
                    copy.deepcopy(state.tables["node_state"].get(contract.contract_id, {})),
                    state,
                    "command",
                    "valid",
                    None,
                )
                machine = compile_machine(
                    definition, evaluation=Evaluation(definition, inputs, now, context)
                )
            data = state.tables["node_state"].get(contract.contract_id, {}).get("machine")
            args_model = declaration.commands.get(command.command)
            args = command.args
            invalid_args = bool(args) and args_model is None
            if args_model:
                try:
                    args = args_model.model_validate(args, strict=True).model_dump()
                except ValidationError:
                    invalid_args = True
            if command.command not in machine.definition.commands:
                result["status"] = "not_supported"
            elif invalid_args:
                result.update(status="rejected", reason="invalid_value")
            elif not data:
                result.update(status="rejected", reason="state_unknown")
            else:
                try:
                    updated, status = machine.step(
                        MachineState.model_validate(data),
                        command.command,
                        self._inputs(contract, config, state),
                        now,
                        state.active_revision,
                        manual=True,
                        args=args,
                    )
                except GuardEvaluationError:
                    result.update(status="rejected", reason="evaluation_error")
                else:
                    result["status"] = (
                        "rejected" if status in {"guard_rejected", "guard_unknown"} else status
                    )
                    if status in {"guard_rejected", "guard_unknown"}:
                        result["reason"] = status
                    if status == "accepted":
                        override[contract.contract_id] = updated
        if override and config:
            evaluation_errors = self._evaluate(
                state, config, origin="command", machine_override=override
            )
            if command.contract_id in evaluation_errors:
                result.update(status="rejected", reason="evaluation_error")
        state.tables["command_log"][command.command_id] = {
            "digest": digest,
            "result": result,
            "at": now.isoformat(),
            "command": command.model_dump(mode="json"),
        }
        await self._commit(state, publication=bool(override))
        if result["status"] != "accepted":
            LOGGER.info(
                "command_rejected",
                extra={
                    "contract_id": command.contract_id,
                    "reason": result.get("reason", result["status"]),
                },
            )
        return result

    def _unavailable(self) -> None:
        self.persistence = False
        self.confirmed = False
        self._broadcast(self.service_state())

    async def _commit(
        self, state: State, *, publication: bool = False, resync: bool = False
    ) -> None:
        started = self.clock.monotonic()
        from .authoring import archive_versions

        archive_versions(self.state, state)
        config = self._config(state)
        if config and self.installation_id:
            from .notifications import plan_notices

            if self.first_snapshot or not self.requires_ha_snapshot:
                plan_notices(
                    state, config, self.clock.now_utc(), self.installation_id, self.ingress_slug
                )
        try:
            await self.store.commit(state)
        except Exception:
            self.pending_latches = None
            self.pending_refresh.clear()
            self._unavailable()
            raise
        previous_seq = self.state.publication_seq
        previous_producers = self.config.context_producers if self.config else ()
        if self.context_index and previous_producers != (
            config.context_producers if config else ()
        ):
            self.context_index.clear_registered()
        previous_contracts = self.state.tables["contract_state_current"]
        previously_needed_ha = self.requires_ha_snapshot
        self.state = state
        if not self.requires_ha_snapshot:
            self.ha = self.bridge = "not_needed"
            self.bridge_subscriptions = 0
            self.first_snapshot = False
            self.dependency_connections.pop("ha", None)
        elif not previously_needed_ha:
            self.ha = self.bridge = "disconnected"
            self.first_snapshot = False
        if self.pending_latches is not None:
            self.latches = self.pending_latches
            self.pending_latches = None
        self.persistence = True
        self.confirmed = True
        for source_id in self.pending_refresh:
            try:
                self.refresh_requests.put_nowait((state.active_revision, source_id))
            except asyncio.QueueFull:
                self.refresh_overflow += 1
                LOGGER.warning("refresh_queue_overflow")
        self.pending_refresh.clear()
        self.commit_duration = self.clock.monotonic() - started
        self._plan_evaluation()
        self.service_jobs_changed.set()
        for key, envelope in state.tables["contract_state_current"].items():
            previous_fields = previous_contracts.get(key, {}).get("fields", {})
            for name, field in envelope["fields"].items():
                old = previous_fields.get(name, {}).get("status")
                if old != field["status"]:
                    LOGGER.info(
                        "quality_transition",
                        extra={
                            "contract_id": key,
                            "field": name,
                            "previous_status": old,
                            "status": field["status"],
                        },
                    )
        if resync:
            self._broadcast({"type": "resync_required", "reason": "epoch"})
        elif publication and state.publication_seq != previous_seq:
            self._broadcast(
                {
                    "type": "delta",
                    "publication_seq": state.publication_seq,
                    "prev_seq": previous_seq,
                    "epoch_id": state.epoch_id,
                    "registry_revision": state.active_revision,
                    "contracts": state.tables["contract_state_current"],
                }
            )

    def _calendar_observations(self, state: State, config: RegistryConfig, origin: str) -> set[str]:
        changed: set[str] = set()
        now = self.clock.now_utc()
        for binding in config.bindings:
            calendar = binding.adapter.calendar
            if calendar is None and binding.adapter.kind != "connectivity":
                continue
            if calendar is not None:
                value, _ = project(calendar, now)
            else:
                value = (
                    "ready"
                    if self.bridge == "connected" and self.ha == "connected"
                    else "starting"
                    if self.ha in {"starting", "stopping"}
                    else "unavailable"
                    if self.bridge == "unavailable"
                    else None
                )
            old = state.tables["source_observation_current"].get(binding.binding_id)
            if old and old.get("value_raw") == value and old.get("observation_kind") != "restore":
                continue
            observation = Observation(
                source_id=binding.source_id,
                binding_id=binding.binding_id,
                adapter="scheduler" if calendar is not None else "connectivity",
                value_raw=value,
                device_time=now,
                received_at=now,
                observation_kind="snapshot" if origin in {"restore", "revision"} else "live_change",
                epoch_id=state.epoch_id,
                ingest_seq=int(old["ingest_seq"]) + 1 if old else 0,
                reference_version=2,
                registry_revision=state.active_revision,
                availability="available" if value is not None else "unknown",
            )
            state.tables["source_observation_current"][binding.binding_id] = (
                observation.storage_document()
            )
            state.tables["source_observation_history"][observation.reference] = (
                observation.storage_document()
            )
            changed.add(binding.source_id)
        return changed

    def _plan_evaluation(self) -> None:
        now = self.clock.now_utc()
        due: list[datetime] = []
        from .service_jobs import due_dates

        due.extend(due_dates(self.state))
        config = self.config
        if config:
            if config.history_retention:
                due.append(self.next_retention)
            due.extend(
                project(binding.adapter.calendar, now)[1]
                for binding in config.bindings
                if binding.adapter.calendar is not None
            )
            _, types, _ = self._evaluation_plan(self.state, config)
            bindings = {b.source_id: b.binding_id for b in config.bindings}
            observations = self.state.tables["source_observation_current"]
            for source in config.sources:
                for measured_source in {
                    source.source_id,
                    source.freshness.liveness_source or source.source_id,
                }:
                    raw = observations.get(bindings.get(measured_source, ""))
                    if raw:
                        item = Observation.model_validate(raw)
                        for metadata in ((item.value_origin,) if item.value_origin else ()) + tuple(
                            point.value_origin for point in item.forecast
                        ):
                            due.extend(
                                at for at in (metadata.valid_from, metadata.valid_until) if at
                            )
                            if metadata.issued_at and source.freshness.interval_s:
                                due.append(
                                    metadata.issued_at
                                    + timedelta(seconds=source.freshness.interval_s, microseconds=1)
                                )
                        stamp = item.measurement
                        if stamp:
                            due.append(
                                stamp - timedelta(seconds=source.freshness.future_tolerance_s)
                            )
                            if manual := Observation.model_validate(raw).manual:
                                due.append(manual.valid_from)
                                if manual.valid_until:
                                    due.append(manual.valid_until)
                            if source.freshness.interval_s is not None:
                                due.append(
                                    stamp
                                    + timedelta(
                                        seconds=source.freshness.interval_s,
                                        microseconds=0 if raw.get("adapter") == "manual" else 1,
                                    )
                                )
            for contract in config.contracts:
                callback = types.get(contract.type_id, contract.type_version).next_due
                if callback and contract.enabled:
                    try:
                        at = callback(
                            contract.parameters,
                            self.state.tables["node_state"].get(contract.contract_id, {}),
                            now,
                        )
                    except ValueError, TypeError, KeyError, AttributeError:
                        # Invalid persisted context is already degraded by evaluation;
                        # a scheduling hint must never undo a committed publication.
                        at = None
                    if at:
                        due.append(at)
        self.next_evaluation = min((at for at in due if at > now), default=None)
        if self.evaluation_alarm:
            self.evaluation_alarm.cancel()
        if (
            self.next_evaluation
            and config
            and (
                any(b.adapter.calendar or b.adapter.kind == "manual" for b in config.bindings)
                or any(d.machine and d.machine.unknown_deadline for d in config.definitions)
                or any(source.value_origin for source in config.sources)
                or bool(self.state.tables["service_state"])
                or any(node.get("clock_due") for node in self.state.tables["node_state"].values())
                or any(
                    c.enabled
                    and c.type_id
                    in {"opening.dual_contact", "opening.single_contact", "opening_zone.facts"}
                    for c in config.contracts
                )
            )
        ):
            self.evaluation_alarm = self.clock.call_at_utc(self.next_evaluation, self.tick)

    def snapshot(self, contracts: list[str] | None = None) -> dict[str, Any]:
        items = self.state.tables["contract_state_current"]
        return {
            "type": "snapshot",
            "publication_seq": self.state.publication_seq,
            "epoch_id": self.state.epoch_id,
            "registry_revision": self.state.active_revision,
            "publication_confirmed": self.confirmed,
            "contracts": {k: v for k, v in items.items() if contracts is None or k in contracts},
        }

    def subscribe(self, max_queue: int = 32) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(max_queue)
        self.subscribers.add(queue)
        return queue

    def _broadcast(self, event: dict[str, Any]) -> None:
        for queue in self.subscribers:
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "resync_required", "reason": "slow_subscriber"})
            else:
                queue.put_nowait(event)

    async def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        await self.queue.join()
        self._broadcast({"type": "going_away"})
        if self.persistence and self.state.epoch_id in self.state.tables["runtime_epoch"]:
            state = self.state.clone()
            if self.config:
                for contract in self.config.contracts:
                    self._dispose(
                        contract, state.tables["node_state"].get(contract.contract_id, {}), state
                    )
            state.tables["runtime_epoch"][state.epoch_id]["ended_at"] = (
                self.clock.now_utc().isoformat()
            )
            await self._commit(state)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
