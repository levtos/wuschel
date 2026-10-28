"""Prove observation and context compatibility before revision-local reevaluation."""

from typing import Any

from .registry import (
    RegistryConfig,
    context_compatibility,
    fingerprints,
    legacy_context_compatibility,
    observation_fingerprints,
    registry_types,
    resolve_ref,
)
from .schema import TypeRegistry


def ha_subscription(config: RegistryConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Only the actual HA entity/report subscription requires a new socket."""
    entities = tuple(
        sorted({b.adapter.ha_entity_id for b in config.bindings if b.adapter.ha_entity_id})
    )
    live_sources = {s.freshness.liveness_source for s in config.sources}
    reports = {
        s.source_id
        for s in config.sources
        if s.freshness.mode != "event_stateful" or s.source_id in live_sources
    }
    report_entities = tuple(
        sorted(
            {
                b.adapter.ha_entity_id
                for b in config.bindings
                if b.adapter.ha_entity_id and b.source_id in reports
            }
        )
    )
    return entities, report_entities


def same_ha_subscription(before: RegistryConfig | None, after: RegistryConfig | None) -> bool:
    return (
        before is not None
        and after is not None
        and ha_subscription(before) == ha_subscription(after)
    )


def same_mqtt_bindings(before: RegistryConfig | None, after: RegistryConfig | None) -> bool:
    if before is None or after is None:
        return False
    return {
        b.model_dump_json(exclude={"display_name"})
        for b in before.bindings
        if b.adapter.kind == "mqtt"
    } == {
        b.model_dump_json(exclude={"display_name"})
        for b in after.bindings
        if b.adapter.kind == "mqtt"
    }


def assessment_only(before: RegistryConfig | None, after: RegistryConfig | None) -> bool:
    """An interval/template edit needs reevaluation, not fresh adapter snapshots/replays."""
    if before is None or after is None:
        return False
    if any(
        getattr(before, name) != getattr(after, name)
        for name in (
            "bindings",
            "dependencies",
            "contracts",
            "definitions",
            "catalogs",
            "notifications",
            "context_producers",
        )
    ):
        return False
    if observation_fingerprints(before) != observation_fingerprints(after):
        return False
    old = {source.source_id: source for source in before.sources}
    return all(
        (old[source.source_id].freshness.mode, old[source.source_id].freshness.liveness_source)
        == (source.freshness.mode, source.freshness.liveness_source)
        for source in after.sources
    )


def migrate_fingerprint(value: Any, previous: str, current: str) -> None:
    if isinstance(value, dict):
        for field, item in value.items():
            if field == "fingerprint" and item == previous:
                value[field] = current
            elif isinstance(item, (dict, list)):
                migrate_fingerprint(item, previous, current)
    elif isinstance(value, list):
        for item in value:
            migrate_fingerprint(item, previous, current)


def migrate_legacy_contexts(state: Any, config: RegistryConfig, types: TypeRegistry) -> None:
    """Prove b15 identity against its own active revision before CP-15 restore."""
    if config.fingerprint_version != 4:
        return
    previous = legacy_context_compatibility(config, types)
    current = fingerprints(config, types)
    for contract in config.contracts:
        key = contract.contract_id
        node = state.tables["node_state"].get(key)
        if node and node.get("fingerprint") == previous[key]:
            migrate_fingerprint(node, previous[key], current[key])


def context_effects(
    state: Any,
    before: RegistryConfig,
    after: RegistryConfig,
    types: TypeRegistry,
    latches: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """One read-only plan used by both preview and activation, including volatile latches."""
    old, new = context_compatibility(before, types), context_compatibility(after, types)
    old_lineage = legacy_context_compatibility(before, types)
    new_lineage = legacy_context_compatibility(after, types)
    resolved = registry_types(before, types)
    stored_fingerprints = fingerprints(before, types)
    effects: list[dict[str, str]] = []
    for contract in before.contracts:
        key = contract.contract_id
        node = state.tables["node_state"].get(key, {})
        unproven = node.get("fingerprint") != stored_fingerprints[key]
        own_changed = old[key] != new.get(key) or unproven
        changed = {
            item.name
            for item in contract.inputs
            if old_lineage.get(resolve_ref(item.ref, before)[0])
            != new_lineage.get(resolve_ref(item.ref, before)[0])
        }
        declaration = resolved.get(contract.type_id, contract.type_version)
        definition = declaration.definition or {}

        def inputs(value: Any, visited: frozenset[str] = frozenset()) -> set[str]:
            if isinstance(value, list):
                return set().union(*(inputs(v, visited) for v in value))
            if not isinstance(value, dict):
                return set()
            if value.get("op") == "input":
                return {value["name"]}
            if value.get("op") == "ref":
                name = value["name"]
                return (
                    inputs(definition.get("rules", {}).get(name), visited | {name})
                    if name not in visited
                    else set()
                )
            return set().union(*(inputs(v, visited) for v in value.values()))

        def add(storage: str, slot: str, dependencies: set[str]) -> None:
            if own_changed or changed.intersection(dependencies):
                effects.append(
                    {
                        "contract_id": key,
                        "storage": storage,
                        "slot": slot,
                        "case": "c" if own_changed else "b",
                        "reason": "stored_context_unproven"
                        if unproven
                        else "definition_or_interface_changed"
                        if own_changed
                        else "input_derivation_changed",
                    }
                )

        if own_changed and any(
            name in node for name in ("machine", "temporal", "rules", "events", "age_input")
        ):
            add("node", "", set())
        elif node and not definition:
            if "temporal" in node or "age_input" in node:
                add("temporal", "", {i.name for i in contract.inputs})

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, dict):
                op, slot = value.get("op"), value.get("id", "")
                if op in {"temporal", "event_match", "bucket"}:
                    storage = {"temporal": "rules", "event_match": "events", "bucket": "latch"}[op]
                    exists = (
                        (f"{key}:{slot}" in (latches or {}))
                        if op == "bucket"
                        else slot in node.get(storage, {})
                    )
                    if exists and (not own_changed or op == "bucket"):
                        add(storage, slot, inputs(value))
                for child in value.values():
                    visit(child)

        visit(definition)
        if not definition:
            for latch_key in latches or {}:
                if latch_key == key or latch_key.startswith(key + ":"):
                    add("latch", latch_key.partition(":")[2], {i.name for i in contract.inputs})
    # Shared named expressions may be reached from more than one output.
    return [dict(items) for items in sorted({tuple(sorted(e.items())) for e in effects})]


def preserve(
    state: Any,
    before: RegistryConfig,
    after: RegistryConfig,
    types: TypeRegistry,
    latches: dict[str, Any] | None = None,
) -> set[str]:
    effects = context_effects(state, before, after, types, latches)
    for effect in effects:
        key, slot, storage = effect["contract_id"], effect["slot"], effect["storage"]
        node = state.tables["node_state"].get(key, {})
        if storage == "latch" and latches is not None:
            latches.pop(f"{key}:{slot}" if slot else key, None)
        elif storage in {"rules", "events"}:
            node.get(storage, {}).pop(slot, None)
        elif storage == "temporal":
            for field in ("temporal", "restore_pending", "age_input", "age_value", "edge_active"):
                node.pop(field, None)
    old_observations, new_observations = (
        observation_fingerprints(before),
        observation_fingerprints(after),
    )
    old_context, new_context = (
        context_compatibility(before, types),
        context_compatibility(after, types),
    )
    old_fp, new_fp = fingerprints(before, types), fingerprints(after, types)
    compatible = {
        key
        for key in old_context.keys() & new_context.keys()
        if old_context[key] == new_context[key]
    }

    contract_ids = {contract.contract_id for contract in before.contracts}
    for key in tuple(compatible):
        node = state.tables["node_state"].get(key)
        if key in contract_ids and (not node or node.get("fingerprint") != old_fp.get(key)):
            compatible.discard(key)
            continue
        if node and node.get("fingerprint") == old_fp.get(key):
            migrate_fingerprint(node, old_fp[key], new_fp[key])
        sensor = state.tables["service_state"].get(f"sensor:{key}")
        if sensor:
            migrate_fingerprint(sensor, old_fp[key], new_fp[key])
    for binding in after.bindings:
        key = binding.source_id
        observation = state.tables["source_observation_current"].get(binding.binding_id)
        if old_observations.get(key) != new_observations[key]:
            state.tables["source_observation_current"].pop(binding.binding_id, None)
        elif (
            observation
            and key in compatible
            and observation.get("mqtt_device_proof") == old_fp.get(key)
        ):
            observation["mqtt_device_proof"] = new_fp[key]
    for binding in before.bindings:
        if old_observations.get(binding.source_id) != new_observations.get(binding.source_id):
            state.tables["source_observation_current"].pop(binding.binding_id, None)
    return compatible
