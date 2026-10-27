"""Prove observation and context compatibility before revision-local reevaluation."""

from typing import Any

from .registry import RegistryConfig, context_compatibility, fingerprints, observation_fingerprints
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


def preserve(
    state: Any, before: RegistryConfig, after: RegistryConfig, types: TypeRegistry
) -> set[str]:
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

    def migrate(value: Any, previous: str, current: str) -> None:
        if isinstance(value, dict):
            for field, item in value.items():
                if field == "fingerprint" and item == previous:
                    value[field] = current
                elif isinstance(item, (dict, list)):
                    migrate(item, previous, current)
        elif isinstance(value, list):
            for item in value:
                migrate(item, previous, current)

    contract_ids = {contract.contract_id for contract in before.contracts}
    for key in tuple(compatible):
        node = state.tables["node_state"].get(key)
        if key in contract_ids and (not node or node.get("fingerprint") != old_fp.get(key)):
            compatible.discard(key)
            continue
        if node and node.get("fingerprint") == old_fp.get(key):
            migrate(node, old_fp[key], new_fp[key])
        sensor = state.tables["service_state"].get(f"sensor:{key}")
        if sensor:
            migrate(sensor, old_fp[key], new_fp[key])
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
