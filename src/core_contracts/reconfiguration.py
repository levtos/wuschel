"""Prove observation and context compatibility before revision-local reevaluation."""

from typing import TYPE_CHECKING, Any

from . import slots
from .declarative import all_rules
from .declarative_models import FormulaRule
from .engines import FORMULA_SCHEMAS
from .lifecycle import EDITORIAL, transition_allowed
from .model import checksum, semantic_fingerprint
from .numeric_equivalence import preserve_config, preserve_numbers
from .operation_timing import timed_phase
from .persistence import State
from .registry import (
    RegistryConfig,
    fingerprints,
    registry_types,
    resolve_ref,
    storage_document,
)
from .schema import TypeRegistry
from .validation import validate_document as validate
from .validation_cache import CHECK_VERSION

if TYPE_CHECKING:
    from .runtime import Runtime


from .registry import (
    context_compatibility,
    legacy_context_compatibility,
    observation_fingerprints,
)


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


@timed_phase
async def _restore_numbers(
    runtime: Runtime, document: dict[str, Any], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    # Validate the submitted types first; preservation must not repair invalid input.
    checked = validate(document, runtime.types)
    result = preserve_config(document, previous or {})
    active = runtime._config(runtime.state)
    if active:
        result = preserve_config(result, storage_document(active))
    for group, identity, version, getter in (
        ("definitions", "type_id", "type_version", runtime.store.definition),
        ("catalogs", "catalog_id", "version", runtime.store.catalog),
    ):
        for index, row in enumerate(result.get(group, [])):
            declaration = getattr(checked, group)[index]
            stored = await getter(getattr(declaration, identity), getattr(declaration, version))
            if stored:
                restored = preserve_numbers(row, stored)
                row.clear()
                row.update(restored)
    for index, row in enumerate(result.get("sources", [])):
        profile = row.get("sensor_profile")
        if profile:
            declaration = checked.sources[index].sensor_profile
            assert declaration is not None
            stored = await runtime.store.sensor_profile(declaration.id, declaration.version)
            if stored:
                row["sensor_profile"] = preserve_numbers(profile, stored)
    return result


slots.CONFIGURATION["_restore_numbers"] = _restore_numbers


@timed_phase
async def _definition_versions(runtime: Runtime, config: RegistryConfig) -> None:
    from .validation import RegistryValidationError, ValidationIssue

    issues = []
    for index, source in enumerate(config.sources):
        profile = source.sensor_profile
        if profile:
            previous = await runtime.store.sensor_profile(profile.id, profile.version)
            if previous and semantic_fingerprint(previous) != semantic_fingerprint(
                preserve_numbers(profile.model_dump(mode="json"), previous)
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
        ("definitions", config.definitions, "type_id", "type_version", runtime.store.definition),
        ("catalogs", config.catalogs, "catalog_id", "version", runtime.store.catalog),
    ):
        for index, item in enumerate(objects):
            document = item.model_dump(mode="json")
            identity = document[identity_key]
            previous = await getter(identity, document[version_key])
            if previous:
                document = preserve_numbers(document, previous)
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
    old = {c.contract_id: c for c in runtime.config.contracts} if runtime.config else {}
    retired = {(d.type_id, d.type_version) for d in config.definitions if d.lifecycle == "retired"}
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
                or (prior.type_id, prior.type_version) != (contract.type_id, contract.type_version)
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
    old_bindings = {b.binding_id: b for b in runtime.config.bindings} if runtime.config else {}
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


slots.CONFIGURATION["_definition_versions"] = _definition_versions


@timed_phase
async def _source_types(runtime: Runtime, config: RegistryConfig) -> None:
    from .registry import Source
    from .validation import RegistryValidationError, ValidationIssue

    issues = []
    revisions: dict[int, dict[str, Source]] = {}
    active_sources = {s.source_id: s for s in runtime.config.sources} if runtime.config else {}
    for index, source in enumerate(config.sources):
        if source == active_sources.get(source.source_id):
            continue
        for document in await runtime.store.source_history(source.source_id, include_revision=True):
            previous_revision = document["_registry_revision"]
            if previous_revision not in revisions:
                historical = await runtime.store.get("registry_revision", str(previous_revision))
                revisions[previous_revision] = (
                    {
                        s.source_id: s
                        for s in RegistryConfig.model_validate(historical["config"]).sources
                    }
                    if historical and historical["config"].get("config_schema_version") == 4
                    else {}
                )
            if source.source_id in revisions[previous_revision]:
                old = revisions[previous_revision][source.source_id]
            else:
                old = Source.model_validate(
                    {k: v for k, v in document.items() if k != "_registry_revision"}
                )
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


slots.CONFIGURATION["_source_types"] = _source_types


async def _checked_config(
    runtime: Runtime, document: dict[str, Any], *, restore: bool = True
) -> RegistryConfig:
    key = checksum(
        [
            document,
            runtime.state.active_revision,
            runtime.state.tables["registry_revision"]
            .get(str(runtime.state.active_revision), {})
            .get("checksum"),
            [
                (key, declaration.description())
                for key, declaration in sorted(runtime.types.types.items())
            ],
            [
                (key, row.get("revision"), row.get("definition_digest"))
                for key, row in sorted(runtime.state.tables["catalog_head"].items())
            ],
            CHECK_VERSION,
            restore,
        ]
    )
    cached = runtime.validation_cache.get(key)
    if cached is not None:
        return cached
    candidate = validate(
        await runtime._restore_numbers(document) if restore else document, runtime.types
    )
    await runtime._definition_versions(candidate)
    await runtime._source_types(candidate)
    runtime.validation_cache.put(key, candidate)
    return candidate


slots.CONFIGURATION["_checked_config"] = _checked_config


async def restore_contexts(runtime: Runtime, state: State, context: Any) -> None:
    restored_config = runtime._config(state)
    if restored_config is not None:
        migrate_legacy_contexts(state, restored_config, runtime.types)


slots.RESTORE[40] = slots.Hook(restore_contexts, "migration")
