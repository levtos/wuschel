"""Immutable configuration-derived indexes, shared only for one configuration."""

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import slots
from .registry import (
    Binding,
    RegistryConfig,
    Source,
    resolve_ref,
    topological,
)

if TYPE_CHECKING:
    from .runtime import Runtime


from .registry import required_sources


@dataclass
class RevisionPlan:
    config: RegistryConfig
    references: dict[str, tuple[str, str | None]]
    sources: dict[str, Source]
    bindings: dict[str, Binding]
    by_source: dict[str, str]
    ha_by_entity: dict[str, list[Binding]]
    mqtt_by_topic: dict[str, list[Binding]]
    source_bindings: dict[str, list[Binding]]
    dependencies: dict[str, Any]
    required: set[str]
    protected: set[str]
    requires_ha: bool


def build(config: RegistryConfig) -> RevisionPlan:
    sources = {source.source_id: source for source in config.sources}
    bindings = {binding.binding_id: binding for binding in config.bindings}
    by_source = {binding.source_id: binding.binding_id for binding in config.bindings}
    ha_by_entity: defaultdict[str, list[Binding]] = defaultdict(list)
    mqtt_by_topic: defaultdict[str, list[Binding]] = defaultdict(list)
    source_bindings: defaultdict[str, list[Binding]] = defaultdict(list)
    for binding in config.bindings:
        adapter = binding.adapter
        source_bindings[binding.source_id].append(binding)
        if adapter.ha_entity_id and adapter.kind in {"ha_state", "ha_attribute"}:
            ha_by_entity[adapter.ha_entity_id].append(binding)
        if adapter.mqtt_topic and adapter.kind == "mqtt":
            mqtt_by_topic[adapter.mqtt_topic].append(binding)
    dependencies = {item.dependency_id: item for item in config.dependencies}
    references = {
        item.ref: resolve_ref(item.ref, config)
        for contract in config.contracts
        for item in contract.inputs
    }
    required = required_sources(config)
    protected = {
        source.source_id
        for source in config.sources
        if source.source_type == "event" or source.sensor_kind in {"contact", "motion"}
    }
    upstream: dict[str, set[str]] = {key: {key} for key in sources}
    definitions = {(item.type_id, item.type_version): item for item in config.definitions}

    def event_rule(node: Any) -> bool:
        if isinstance(node, dict):
            return (
                node.get("op") == "event_match"
                or node.get("op") == "temporal"
                and node.get("operation") == "edge"
                or any(event_rule(value) for value in node.values())
            )
        return isinstance(node, (list, tuple)) and any(event_rule(value) for value in node)

    for contract in topological(config):
        reached = set().union(
            *(upstream.get(references[item.ref][0], set()) for item in contract.inputs)
        )
        upstream[contract.contract_id] = reached
        definition = definitions.get((contract.type_id, contract.type_version))
        if contract.enabled and (
            contract.type_id
            in {
                "opening.single_contact",
                "opening.dual_contact",
                "communication.call_active",
                "climate.toilet_event",
            }
            or definition is not None
            and (definition.machine is not None or event_rule(definition.model_dump(mode="json")))
        ):
            protected.update(reached)
    # A protected source's liveness/monitor/reference changes are inputs too.
    pending = list(protected)
    while pending:
        source = sources[pending.pop()]
        for ref in (source.freshness.liveness_source, dependencies[source.dependency_id].source_id):
            if ref and ref not in protected:
                protected.add(ref)
                pending.append(ref)
    command_types = {
        (item.type_id, item.type_version)
        for item in config.definitions
        if item.language_version == 6 and item.machine and item.machine.commands
    }
    ha_dependencies = {item.dependency_id for item in config.dependencies if item.kind != "mqtt"}
    requires_ha = (
        any(
            contract.enabled
            and (
                contract.ha_projection
                or contract.refresh_plan
                or (contract.type_id, contract.type_version) in command_types
            )
            for contract in config.contracts
        )
        or bool(
            config.notifications
            and (config.notifications.signal.enabled or config.notifications.configuration.enabled)
        )
        or any(
            (binding.source_id in required or binding.adapter.kind == "ha_calendar")
            and (
                binding.adapter.kind.startswith("ha_")
                or binding.adapter.kind == "connectivity"
                and sources[binding.source_id].dependency_id in ha_dependencies
            )
            for binding in config.bindings
        )
    )
    return RevisionPlan(
        config,
        references,
        sources,
        bindings,
        by_source,
        dict(ha_by_entity),
        dict(mqtt_by_topic),
        dict(source_bindings),
        dependencies,
        required,
        protected,
        requires_ha,
    )


def _structure(runtime: Runtime, config: RegistryConfig) -> Any:
    from .revision_plan import build

    key = id(config)
    if key not in runtime._structures:
        runtime._structures[key] = build(config)
        if len(runtime._structures) > 4:
            runtime._structures.pop(next(iter(runtime._structures)))
    return runtime._structures[key]


slots.CONFIGURATION["_structure"] = _structure
