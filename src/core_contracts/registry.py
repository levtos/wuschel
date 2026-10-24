"""Stable identities, strict validation, graph ordering and semantic fingerprints."""

import re
from collections.abc import Iterator
from functools import cache, lru_cache
from itertools import islice
from typing import Annotated, Any, Literal, Self

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .adapters import AdapterConfig
from .catalog_models import ManagedCatalog
from .declarative_models import MAX_DEFINITIONS, DeclarativeDefinition
from .evidence import Freshness
from .freshness_templates import FreshnessSelection, FreshnessTemplate, SensorKind
from .ha_context import ContextProducer
from .lifecycle import Lifecycle
from .model import Model, canonical, semantic_fingerprint
from .notifications import Notifications
from .refresh_plan import RefreshCapability, RefreshPlan
from .retention import RetentionPolicy
from .schema import ContractType, FieldSchema, TypeRegistry
from .sensor_profile import SensorProfile
from .value_origin import OriginDeclaration


class Dependency(Model):
    dependency_id: str
    kind: Literal["ha", "mqtt", "other", "clock", "connectivity", "manual"]
    monitor: Literal["internal", "source"]
    source_id: str | None = None


class Source(Model):
    source_id: str = Field(pattern=r"^source\.[a-zA-Z0-9_.-]+$")
    source_type: Literal["state", "attribute", "event", "numeric", "text", "boolean"]
    source_origin: str
    freshness: Freshness
    freshness_selection: FreshnessSelection | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    sensor_kind: SensorKind | None = Field(default=None, exclude_if=lambda v: v is None)
    sensor_kind_origin: Literal["declared", "confirmed"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    dependency_id: str
    physical_source_key: str = Field(min_length=1)
    display_name: str = ""
    value_schema: FieldSchema | None = None
    value_origin: OriginDeclaration | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sensor_profile: SensorProfile | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sensor_parameters: dict[str, Annotated[float, Field(strict=True)]] = Field(
        default_factory=dict, max_length=16, exclude_if=lambda value: not value
    )
    origin: Literal["manual"] | None = Field(default=None, exclude_if=lambda value: value is None)
    manual_allow_future: bool = Field(
        default=False, strict=True, exclude_if=lambda value: not value
    )
    manual_minimum: float | None = Field(default=None, exclude_if=lambda value: value is None)
    manual_maximum: float | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_serializer(mode="wrap")
    def selection_document(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.freshness_selection is not None:
            result.pop("freshness", None)
        return result

    @model_validator(mode="after")
    def manual_declaration(self) -> Self:
        if self.sensor_profile:
            if (
                self.origin == "manual"
                or self.value_schema is None
                or self.value_schema.type != "number"
                or self.value_origin
                and self.value_origin.kind != "measured"
            ):
                raise ValueError("numeric_measured_sensor_required")
            self.sensor_profile.resolve(self.sensor_parameters)
        elif self.sensor_parameters:
            raise ValueError("sensor_profile_required")
        if self.origin == "manual" and (
            self.value_schema is None
            or self.value_schema.type == "enum"
            and not self.value_schema.value_catalog
            or self.freshness.mode not in {"periodic_ttl", "event_stateful"}
            or self.freshness.future_tolerance_s != 0
            or self.freshness.interval_s is not None
            and self.freshness.interval_s > 31_536_000
        ):
            raise ValueError("manual_schema_and_validity_required")
        if self.manual_allow_future and self.origin != "manual":
            raise ValueError("manual_source_required")
        if self.manual_minimum is not None or self.manual_maximum is not None:
            if (
                self.origin != "manual"
                or self.value_schema is None
                or self.value_schema.type != "number"
                or self.manual_minimum is not None
                and self.manual_maximum is not None
                and self.manual_minimum > self.manual_maximum
            ):
                raise ValueError("manual_numeric_bounds_required")
        return self


class Transform(Model):
    kind: Literal["type_cast", "unit_scale", "map_ref"]
    target: Literal["bool", "number", "text"] | None = None
    factor: float | None = None
    offset: float | None = None
    catalog_id: str | None = None

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.kind == "type_cast" and self.target is None:
            raise ValueError("target required")
        if self.kind == "unit_scale" and (self.factor is None or self.offset is None):
            raise ValueError("factor and offset required")
        if self.kind == "map_ref" and not self.catalog_id:
            raise ValueError("catalog required")
        return self


class Binding(Model):
    binding_id: str = Field(min_length=1)
    source_id: str
    adapter: AdapterConfig
    transform: Transform | None = None
    display_name: str = ""
    refresh: RefreshCapability | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def refresh_target(self) -> Self:
        if self.refresh and (
            self.adapter.kind not in {"ha_state", "ha_attribute"}
            or not self.adapter.ha_entity_id
            or self.refresh.profile == "companion_location"
            and not self.adapter.ha_entity_id.startswith("device_tracker.")
        ):
            raise ValueError("refresh_not_supported")
        return self


class Input(Model):
    name: str
    ref: str


class Contract(Model):
    contract_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]+$")
    type_id: str
    type_version: int = Field(ge=1)
    enabled: bool
    inputs: tuple[Input, ...]
    parameters: dict[str, Any]
    display_name: str = ""
    description: str = ""
    refresh_plan: RefreshPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    ha_projection: bool = Field(default=False, strict=True, exclude_if=lambda value: not value)


class Catalog(Model):
    catalog_id: str
    kind: Literal["map"]
    version: int = Field(ge=1)
    entries: dict[str, Any]
    lifecycle: Lifecycle = Field(default="active", exclude_if=lambda value: value == "active")
    reactivation_note: str | None = Field(
        default=None, max_length=256, exclude_if=lambda value: value is None
    )


class RegistryConfig(Model):
    config_schema_version: Literal[1, 2, 3, 4]
    fingerprint_version: Literal[1, 2, 3, 4] = 1
    freshness_templates: tuple[FreshnessTemplate, ...] = Field(
        default=(), max_length=256, exclude_if=lambda v: not v
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_freshness(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        import copy

        document = copy.deepcopy(value)
        modern = document.get("config_schema_version") == 4
        if modern and document.get("fingerprint_version") != 4:
            raise ValueError("registry_fingerprint_4_required")
        if not modern and document.get("fingerprint_version") == 4:
            raise ValueError("registry_schema_4_required")
        template_rows = document.get("freshness_templates", [])
        source_rows = document.get("sources", [])
        if not isinstance(template_rows, (list, tuple)) or not isinstance(
            source_rows, (list, tuple)
        ):
            return document
        parsed_templates = [FreshnessTemplate.model_validate(row) for row in template_rows]
        templates = {row.template_id: row for row in parsed_templates}
        if len(templates) != len(document.get("freshness_templates", [])):
            raise ValueError("duplicate_freshness_template")
        if not modern and templates:
            raise ValueError("registry_schema_4_required")
        for source in source_rows:
            if not isinstance(source, dict):
                continue
            selection = source.get("freshness_selection")
            if modern:
                if not isinstance(selection, dict) or not selection or "freshness" in source:
                    raise ValueError("freshness_selection_exclusive")
                if selection.get("kind") == "shared":
                    template_id = selection.get("template_id")
                    if not isinstance(template_id, str):
                        raise ValueError("freshness_template_missing")
                    template = templates.get(template_id)
                    if template is None:
                        raise ValueError("freshness_template_missing")
                    if source.get("sensor_kind") != template.sensor_kind or source.get(
                        "sensor_kind_origin"
                    ) not in {"confirmed", "declared"}:
                        raise ValueError("sensor_kind_confirmation_required")
                    if source.get("origin") == "manual":
                        raise ValueError("manual_freshness_must_be_own")
                    rule = template.rule.model_dump(mode="json")
                    rule["liveness_source"] = selection.get("liveness_source")
                    if rule["mode"] != "liveness_source" and rule["liveness_source"]:
                        raise ValueError("unexpected_liveness_source")
                    source["freshness"] = rule
                elif selection.get("kind") == "own":
                    comparison = selection.get("comparison_template_id")
                    if comparison is not None and not isinstance(comparison, str):
                        raise ValueError("freshness_template_missing")
                    if comparison and comparison not in templates:
                        raise ValueError("freshness_template_missing")
                    source["freshness"] = selection.get("rule")
                else:
                    raise ValueError("freshness_selection_invalid")
            elif (
                selection is not None
                or source.get("sensor_kind") is not None
                or source.get("sensor_kind_origin") is not None
            ):
                raise ValueError("registry_schema_4_required")
        return document

    history_retention: RetentionPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    dependencies: tuple[Dependency, ...]
    sources: tuple[Source, ...]
    bindings: tuple[Binding, ...]
    contracts: tuple[Contract, ...]
    catalogs: tuple[Annotated[Catalog | ManagedCatalog, Field(discriminator="kind")], ...]
    definitions: tuple[DeclarativeDefinition, ...] = Field(default=(), max_length=MAX_DEFINITIONS)
    notifications: Notifications | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def versioned_capabilities(self) -> Self:
        if sum(contract.ha_projection and contract.enabled for contract in self.contracts) > 256:
            raise ValueError("ha_projection_limit")
        if (
            any(isinstance(c, ManagedCatalog) for c in self.catalogs)
            or any(d.language_version == 4 for d in self.definitions)
        ) and (self.config_schema_version < 3 or self.fingerprint_version < 3):
            raise ValueError("registry_schema_3_required")
        if len({producer.producer_id for producer in self.context_producers}) != len(
            self.context_producers
        ):
            raise ValueError("duplicate_context_producer")
        if self.config_schema_version == 1 and (
            self.notifications is not None
            or any(source.origin == "manual" for source in self.sources)
            or any(source.value_origin for source in self.sources)
            or any(source.sensor_profile for source in self.sources)
            or bool(self.context_producers)
            or any(binding.refresh is not None for binding in self.bindings)
            or any(contract.refresh_plan is not None for contract in self.contracts)
            or any(definition.language_version >= 3 for definition in self.definitions)
            or any(b.adapter.kind in {"ha_calendar", "manual"} for b in self.bindings)
        ):
            raise ValueError("registry_schema_2_required")
        return self

    context_producers: tuple[ContextProducer, ...] = Field(
        default=(), max_length=64, exclude_if=lambda value: not value
    )


def storage_document(config: RegistryConfig) -> dict[str, Any]:
    """Keep released Alpha-3 v1 documents byte-semantically compatible.

    Actual Alpha-4 extensions remain explicit; never silently downgrade them.
    """
    document = config.model_dump(mode="json")
    if config.fingerprint_version == 1:
        document.pop("fingerprint_version")
    if not config.definitions:
        document.pop("definitions")
    for source in document["sources"]:
        if source["value_schema"] is None:
            source.pop("value_schema")
    return document


@lru_cache(maxsize=128)
def _compile_definition(document: str) -> ContractType:
    from .declarative import compile_definition

    return compile_definition(DeclarativeDefinition.model_validate_json(document))


def registry_types(config: RegistryConfig, base: TypeRegistry) -> TypeRegistry:
    """Compile into a revision-local registry; drafts cannot modify live types."""
    if not config.definitions:
        return base
    result = TypeRegistry()
    result.types.update(base.types)
    for definition in config.definitions:
        result.register(_compile_definition(canonical(definition)))
    return result


def safe_document(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("document too deep")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("document keys must be strings")
            if any(
                part in key.casefold()
                for part in (
                    "password",
                    "secret",
                    "token",
                    "credential",
                    "dsn",
                    "passwd",
                    "api_key",
                    "private_key",
                    "authorization",
                    "connection_string",
                    "database_url",
                )
            ):
                raise ValueError("sensitive key")
            safe_document(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            safe_document(child, depth + 1)
    elif isinstance(value, str) and re.search(r"\w+://[^\s/@]+:[^\s/@]+@", value):
        raise ValueError("credential URL")


def resolve_ref(ref: str, config: RegistryConfig) -> tuple[str, str | None]:
    if ref in {s.source_id for s in config.sources}:
        return ref, None
    for contract in sorted(config.contracts, key=lambda c: len(c.contract_id), reverse=True):
        if ref == contract.contract_id:
            return contract.contract_id, "value"
        if ref.startswith(contract.contract_id + "."):
            return contract.contract_id, ref[len(contract.contract_id) + 1 :]
    raise ValueError("unknown reference")


def validate(document: dict[str, Any], types: TypeRegistry) -> RegistryConfig:
    from .validation import validate_document

    return validate_document(document, types)


def required_sources(config: RegistryConfig) -> set[str]:
    """Sources reachable from enabled producers, including source monitors/liveness."""
    needed = {
        resolve_ref(i.ref, config)[0]
        for c in config.contracts
        if c.enabled
        for i in c.inputs
        if resolve_ref(i.ref, config)[1] is None
    }
    sources = {s.source_id: s for s in config.sources}
    dependencies = {d.dependency_id: d for d in config.dependencies}
    pending = list(needed)
    while pending:
        source = sources[pending.pop()]
        for ref in (
            source.freshness.liveness_source,
            dependencies[source.dependency_id].source_id,
            source.sensor_profile.stuck.reference.ref
            if source.sensor_profile and source.sensor_profile.stuck.reference
            else None,
        ):
            if ref and ref not in needed:
                needed.add(ref)
                pending.append(ref)
    return needed


def affected_sources(config: RegistryConfig, changed: set[str]) -> set[str]:
    dependencies = {d.dependency_id: d for d in config.dependencies}
    result = set(changed)
    while True:
        expanded = result | {
            s.source_id
            for s in config.sources
            if s.freshness.liveness_source in result
            or dependencies[s.dependency_id].source_id in result
            or s.sensor_profile
            and s.sensor_profile.stuck.reference
            and s.sensor_profile.stuck.reference.ref in result
        }
        if expanded == result:
            return result
        result = expanded


def topological(config: RegistryConfig) -> list[Contract]:
    contracts = {c.contract_id: c for c in config.contracts}
    visiting: set[str] = set()
    done: set[str] = set()
    ordered: list[Contract] = []

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("cycle")
        if key in done:
            return
        visiting.add(key)
        for item in contracts[key].inputs:
            ref, field = resolve_ref(item.ref, config)
            if field is not None:
                visit(ref)
        visiting.remove(key)
        done.add(key)
        ordered.append(contracts[key])

    for key in contracts:
        visit(key)
    return ordered


def fingerprints(config: RegistryConfig, types: TypeRegistry) -> dict[str, str]:
    if config.fingerprint_version == 4:
        return context_compatibility(config, types)
    types = registry_types(config, types)
    result: dict[str, str] = {}
    for source in config.sources:
        bindings = [
            b.model_dump(mode="json") for b in config.bindings if b.source_id == source.source_id
        ]
        result[source.source_id] = semantic_fingerprint(
            {
                "source": source.model_dump(
                    mode="json", exclude={"value_schema"} if source.value_schema is None else set()
                ),
                "bindings": bindings,
                "catalogs": [
                    c.model_dump(exclude={"lifecycle", "reactivation_note"})
                    for c in config.catalogs
                    if any(
                        b.transform and b.transform.catalog_id == c.catalog_id
                        for b in config.bindings
                        if b.source_id == source.source_id
                    )
                ],
            }
        )
    base_sources = dict(result)
    dependencies = {d.dependency_id: d for d in config.dependencies}
    sources = {s.source_id: s for s in config.sources}

    @cache
    def monitored_fingerprint(key: str) -> str:
        dependency = dependencies[sources[key].dependency_id]
        if config.fingerprint_version >= 2:
            liveness = sources[key].freshness.liveness_source
            sensor_profile = sources[key].sensor_profile
            sensor_reference = sensor_profile.stuck.reference if sensor_profile else None
            return semantic_fingerprint(
                {
                    "fingerprint_version": 2,
                    "source": base_sources[key],
                    "dependency": dependency.model_dump(),
                    "monitor_source": monitored_fingerprint(dependency.source_id)
                    if dependency.source_id
                    else None,
                    "liveness_source": monitored_fingerprint(liveness) if liveness else None,
                    **(
                        {"sensor_reference": monitored_fingerprint(sensor_reference.ref)}
                        if sensor_reference
                        else {}
                    ),
                }
            )
        if dependency.source_id:
            return semantic_fingerprint(
                {
                    "source": base_sources[key],
                    "monitor": dependency.model_dump(),
                    "monitor_source": monitored_fingerprint(dependency.source_id),
                }
            )
        return base_sources[key]

    for key in sources:
        result[key] = monitored_fingerprint(key)
    for contract in topological(config):
        description = types.get(contract.type_id, contract.type_version).description()
        if "definition" in description:
            description["definition"] = {
                k: v
                for k, v in description["definition"].items()
                if k not in {"lifecycle", "reactivation_note"}
            }
        result[contract.contract_id] = semantic_fingerprint(
            {
                **({"fingerprint_version": 2} if config.fingerprint_version >= 2 else {}),
                "definition": description,
                "contract": contract.model_dump(mode="json", exclude={"enabled", "ha_projection"}),
                "inputs": [result[resolve_ref(i.ref, config)[0]] for i in contract.inputs],
            }
        )
    return result


def observation_fingerprints(config: RegistryConfig) -> dict[str, str]:
    """No assessment or authoring provenance may invent a new observation."""
    result = {}
    for source in config.sources:
        document = source.model_dump(mode="json")
        for field in (
            "freshness",
            "freshness_selection",
            "sensor_kind",
            "sensor_kind_origin",
            "display_name",
        ):
            document.pop(field, None)
        bindings = [
            b.model_dump(mode="json", exclude={"display_name"})
            for b in config.bindings
            if b.source_id == source.source_id
        ]
        used = {
            b.transform.catalog_id
            for b in config.bindings
            if b.source_id == source.source_id and b.transform
        }
        result[source.source_id] = semantic_fingerprint(
            {
                "source": document,
                "bindings": bindings,
                "catalogs": [
                    c.model_dump(exclude={"lifecycle", "reactivation_note"})
                    for c in config.catalogs
                    if c.catalog_id in used
                ],
            }
        )
    return result


def assessment_fingerprints(config: RegistryConfig) -> dict[str, str]:
    observations = observation_fingerprints(config)
    return {
        source.source_id: semantic_fingerprint(
            {
                "rule": source.freshness.model_dump(),
                "dependency": next(
                    d.model_dump()
                    for d in config.dependencies
                    if d.dependency_id == source.dependency_id
                ),
                "liveness": observations.get(source.freshness.liveness_source or ""),
            }
        )
        for source in config.sources
    }


def context_compatibility(config: RegistryConfig, types: TypeRegistry) -> dict[str, str]:
    """Keep only interval-neutral, semantically identical contexts; normal gap rules still run."""
    import copy

    from .freshness_templates import upgrade_document

    document = upgrade_document(storage_document(config))
    # Reuse the proven transitive v3 graph and type/parameter compatibility.
    document["config_schema_version"] = 3
    document["fingerprint_version"] = 3
    document.pop("freshness_templates", None)
    for row, source in zip(document["sources"], config.sources, strict=True):
        row.pop("freshness_selection", None)
        row.pop("sensor_kind", None)
        row.pop("sensor_kind_origin", None)
        row.pop("display_name", None)
        rule = copy.deepcopy(source.freshness.model_dump(mode="json"))
        if rule["mode"] in {"report_heartbeat", "periodic_ttl"}:
            rule["interval_s"] = 1.0
        row["freshness"] = rule
    return fingerprints(RegistryConfig.model_validate(document), types)


def changed_paths(before: Any, after: Any, path: str = "") -> Iterator[str]:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                yield child
            else:
                yield from changed_paths(before[key], after[key], child)
    elif isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        for index in range(max(len(before), len(after))):
            if index >= len(before) or index >= len(after):
                yield path + f"/{index}"
            else:
                yield from changed_paths(before[index], after[index], path + f"/{index}")
    else:
        yield path or "/"


def diff(
    before: RegistryConfig | None, after: RegistryConfig, types: TypeRegistry | None = None
) -> dict[str, Any]:
    old = {c.contract_id: c for c in before.contracts} if before else {}
    new = {c.contract_id: c for c in after.contracts}
    result: dict[str, Any] = {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(k for k in new.keys() & old.keys() if old[k] != new[k]),
    }
    objects: dict[str, Any] = {}
    changed_sources: set[str] = set()
    for group, identity in (
        ("dependencies", "dependency_id"),
        ("sources", "source_id"),
        ("bindings", "binding_id"),
        ("catalogs", "catalog_id"),
        ("contracts", "contract_id"),
        ("definitions", "type_id"),
        ("freshness_templates", "template_id"),
    ):

        def entries(config: RegistryConfig | None) -> dict[str, Any]:
            return (
                {
                    (
                        f"{item.type_id}@{item.type_version}"
                        if group == "definitions"
                        else getattr(item, identity)
                    ): item.model_dump(mode="json")
                    for item in getattr(config, group)
                }
                if config
                else {}
            )

        previous, current = entries(before), entries(after)
        changed = sorted(k for k in previous.keys() & current.keys() if previous[k] != current[k])
        objects[group] = {
            "added": sorted(current.keys() - previous.keys()),
            "removed": sorted(previous.keys() - current.keys()),
            "changed": changed,
            "fields": {
                key: sorted(
                    name
                    for name in previous[key].keys() | current[key].keys()
                    if previous[key].get(name) != current[key].get(name)
                )
                for key in changed
            },
        }
        paths = {
            key: list(islice(changed_paths(previous[key], current[key]), 1025)) for key in changed
        }
        objects[group]["field_paths"] = {key: values[:1024] for key, values in paths.items()}
        objects[group]["field_paths_truncated"] = sorted(
            key for key, values in paths.items() if len(values) > 1024
        )
        if group == "sources":
            changed_sources.update(objects[group]["added"] + objects[group]["removed"] + changed)
        if group == "bindings":
            for key in objects[group]["added"] + objects[group]["removed"] + changed:
                changed_sources.update(
                    item[key]["source_id"] for item in (previous, current) if key in item
                )
    old_fp = context_compatibility(before, types) if before and types else {}
    new_fp = context_compatibility(after, types) if types else {}
    fingerprint_changed = sorted(
        k for k in old_fp.keys() | new_fp.keys() if old_fp.get(k) != new_fp.get(k)
    )
    source_ids = {s.source_id for s in after.sources} | (
        {s.source_id for s in before.sources} if before else set()
    )
    changed_sources.update(key for key in fingerprint_changed if key in source_ids)
    effective_changes: list[str] = []
    if after.config_schema_version in (1, 2, 3, 4):
        old_observations = observation_fingerprints(before) if before else {}
        new_observations = observation_fingerprints(after)
        old_assessments = assessment_fingerprints(before) if before else {}
        new_assessments = assessment_fingerprints(after)
        effective_changes = sorted(
            key for key in source_ids if old_assessments.get(key) != new_assessments.get(key)
        )
        changed_sources = {
            key
            for key in source_ids
            if old_observations.get(key) != new_observations.get(key) or key in effective_changes
        }
    direct = set(result["added"] + result["changed"] + result["removed"])
    for config in (before, after):
        if not config:
            continue
        for contract in config.contracts:
            if any(resolve_ref(item.ref, config)[0] in changed_sources for item in contract.inputs):
                direct.add(contract.contract_id)
            definition_key = f"{contract.type_id}@{contract.type_version}"
            if any(
                definition_key in objects["definitions"][kind]
                for kind in ("added", "removed", "changed")
            ):
                direct.add(contract.contract_id)
    affected = set(direct)
    affected.update(k for k in fingerprint_changed if k in old or k in new)
    while True:
        expanded = set(affected)
        for config in (before, after):
            if config:
                expanded.update(
                    c.contract_id
                    for c in config.contracts
                    if any(resolve_ref(i.ref, config)[0] in affected for i in c.inputs)
                )
        if expanded == affected:
            break
        affected = expanded
    stateful = []
    after_types = registry_types(after, types) if types else None
    before_types = registry_types(before, types) if types and before else None
    if types:
        assert after_types is not None
        stateful = sorted(
            c.contract_id
            for c in after.contracts
            if c.contract_id in fingerprint_changed
            and after_types.get(c.type_id, c.type_version).restore
        )
    previous_sources = {s.source_id: s for s in before.sources} if before else {}
    current_sources = {s.source_id: s for s in after.sources}
    identity_changes = set(previous_sources.keys() ^ current_sources.keys())
    identity_changes.update(
        key
        for key in previous_sources.keys() & current_sources.keys()
        if (previous_sources[key].physical_source_key, previous_sources[key].source_origin)
        != (current_sources[key].physical_source_key, current_sources[key].source_origin)
    )
    previous_bindings = {b.binding_id: b for b in before.bindings} if before else {}
    current_bindings = {b.binding_id: b for b in after.bindings}
    binding_identities = sorted(
        key
        for key in previous_bindings.keys() | current_bindings.keys()
        if key not in previous_bindings
        or key not in current_bindings
        or (previous_bindings[key].source_id, previous_bindings[key].adapter)
        != (current_bindings[key].source_id, current_bindings[key].adapter)
    )
    for key in binding_identities:
        identity_changes.update(
            bindings[key].source_id
            for bindings in (previous_bindings, current_bindings)
            if key in bindings
        )
    result.update(
        objects=objects,
        directly_affected_contracts=sorted(direct),
        transitively_affected_contracts=sorted(affected - direct),
        affected_contracts=sorted(affected),
        affected_fields={
            key: sorted(
                set(
                    after_types.get(new[key].type_id, new[key].type_version).schema.fields
                    if after_types and key in new
                    else ()
                )
                | set(
                    before_types.get(old[key].type_id, old[key].type_version).schema.fields
                    if before_types and key in old
                    else ()
                )
            )
            for key in sorted(affected)
        },
        fingerprint_version={
            "before": before.fingerprint_version if before else None,
            "after": after.fingerprint_version,
            "context_migration": bool(
                before and before.fingerprint_version != after.fingerprint_version
            ),
        },
        fingerprint_changes=fingerprint_changed,
        source_identity_changes=sorted(identity_changes),
        binding_identity_changes=binding_identities,
        changed_source_fingerprints=sorted(key for key in fingerprint_changed if key in source_ids),
        effective_freshness_changes=effective_changes,
        freshness_effects=[
            {
                "source_id": key,
                "before": previous_sources[key].freshness.model_dump(mode="json")
                if key in previous_sources
                else None,
                "after": current_sources[key].freshness.model_dump(mode="json")
                if key in current_sources
                else None,
            }
            for key in effective_changes
        ],
        possibly_invalidated_contexts=stateful,
        restore={
            "reinitialize_contracts": stateful,
            "retains_absolute_deadlines_if_compatible": True,
        },
        snapshot={"revision_changes": True, "affected_contracts": sorted(affected)},
        analysis="static",
        predicts_values=False,
    )
    return result
