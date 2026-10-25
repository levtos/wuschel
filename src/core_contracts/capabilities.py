"""Public editor contract. Capability metadata never changes legacy fingerprints."""

from typing import Any

from .declarative_models import MAX_DEPTH, MAX_FIELDS, MAX_INPUTS, MAX_NODES, DeclarativeDefinition
from .engines import FORMULA_SCHEMAS, FUSION_INPUTS
from .history import HISTORY_TABLES, HistoryQuery
from .quality import ReasonCode
from .registry import RegistryConfig, registry_types
from .schema import TypeRegistry
from .statemachine import Machine

SEMANTIC_RULES = {
    "document_object_required": "configuration is a JSON object",
    "unsafe_document": "no secrets; finite JSON; document size and depth limits",
    "fingerprint_upgrade_required": "declarative definitions require fingerprint version 2",
    "immutable_definition_version": "published definition semantics are immutable",
    "definition_invalid": "definition resource limits and reserved package identities",
    "duplicate_identity": "identities are unique within each registry object kind",
    "ambiguous_identity": "source and contract identities are disjoint",
    "source_dependency": "sources have a known dependency and local origin",
    "liveness_reference": "liveness sources exist",
    "monitor_required": "source-monitored dependencies reference a source",
    "monitor_unsupported": "internal monitors support only known adapter kinds",
    "dependency_cycle": "dependency monitor graph is acyclic",
    "source_dependency_cycle": "source and liveness graph is acyclic and at most depth 64 in v2",
    "adapter_unsupported": "adapter kind and declared calendar projection must be supported",
    "duplicate_adapter": "an adapter evidence path is bound once",
    "binding_source": "bindings target distinct existing sources",
    "duplicate_evidence": "physical evidence paths are not counted twice",
    "catalog_reference": "map bindings reference an existing catalog",
    "binding_type_mismatch": "binding transform output matches source value_schema",
    "catalog_type_mismatch": "all map catalog values match source value_schema",
    "monitor_unbound": "dependency monitor sources have a binding",
    "type_reference": "contract type and version are registered",
    "grace_duration": "grace duration is positive",
    "contract_inputs": "inputs are distinct, declared and include every required input",
    "input_reference": "input references resolve unambiguously",
    "refresh_binding": "refresh inputs refer to an HA source binding",
    "source_unbound": "referenced source has a binding",
    "field_reference": "referenced contract field exists",
    "disabled_dependency": "enabled contracts cannot require disabled contracts",
    "input_type_mismatch": "input type, catalog and unit are compatible",
    "registry_evaluation_limit": "enabled definitions total at most 8192 estimated steps",
    "contract_cycle": "contract graph is acyclic",
    "contract_graph_depth": "contract graph depth is at most 64 in v2",
    **{
        "definition." + code: constraint
        for code, constraint in {
            "rule_type_mismatch": "operator inputs and output schemas are compatible",
            "unit_mismatch": "combined numeric inputs have equal units",
            "internal_cycle": "internal rule references are acyclic",
            "unknown_internal_reference": "internal references name a declared rule",
            "unknown_rule_input": "input expressions name a declared input",
            "duplicate_stateful_id": "stateful node ids are unique",
            "definition_resource_limit": "expanded depth and physical node count obey limits",
            "stateful_guard_requires_contract_input": "guards and reinitialization expressions are stateless",
            "comparison_type_mismatch": "comparison operands have compatible types",
            "unordered_comparison": "ordering applies to number, text and timestamp only",
            "map_requires_scalar": "map inputs cannot have object type",
            "invalid_map_key": "map keys are canonical JSON scalars matching the input type and catalog",
            "unknown_formula_version": "formula name/version is registered",
            "unknown_fusion_strategy": "fusion strategy is registered",
            "machine_definition_required": "machine expressions require a definition",
            "duplicate_transition_event": "each machine source/event pair occurs at most once",
            "unreachable_machine": "a declared machine is reachable from an output through rules",
        }.items()
    },
}

REASONS = {
    ReasonCode.INPUT_UNAVAILABLE: "The source reports unavailable.",
    ReasonCode.INPUT_UNKNOWN: "Decisive input has no usable value.",
    ReasonCode.INPUT_STALE: "The measurement exceeds its configured freshness interval.",
    ReasonCode.INPUT_NOT_YET_VALID: "The manual input is not valid yet.",
    ReasonCode.VALUE_VALIDITY_GAP: "No value is valid at the requested instant.",
    ReasonCode.SENSOR_STUCK: "Explicit sensor profile suspects an unchanged numeric value.",
    ReasonCode.INPUT_RESTORED: "Restored source data is not new measurement evidence.",
    ReasonCode.INPUT_ABSENT: "No observation exists for the required input.",
    ReasonCode.UNMAPPED_VALUE: "The map has no entry for this input.",
    ReasonCode.INVALID_VALUE: "The input does not satisfy its declared value type.",
    ReasonCode.NO_MATCH: "No first-match condition selected a result.",
    ReasonCode.CONFLICT: "Equally decisive candidates disagree.",
    ReasonCode.PARTIAL_EVIDENCE: "Usable candidates determine the result despite missing evidence.",
    ReasonCode.GRACE: "A previously valid value is held until its original grace deadline.",
    ReasonCode.SELECTION_BLOCKED: "An unknown earlier condition prevents deterministic selection.",
    ReasonCode.DEPENDENCY_UNAVAILABLE: "A declared runtime dependency is unavailable.",
    ReasonCode.CONFIG_INCOMPLETE: "The contract configuration is incomplete.",
    ReasonCode.EVALUATION_ERROR: "Producer evaluation failed; staged producer changes were discarded.",
    ReasonCode.RESTORE_MISSING: "Required persisted context is missing or inconsistent.",
    ReasonCode.RESTORE_INCOMPATIBLE: "The semantic fingerprint does not match the persisted context.",
    ReasonCode.RESTORE_STALE: "A disabled or removed instance requires reinitialization.",
    ReasonCode.HISTORY_GAP: "Observation continuity was interrupted.",
    ReasonCode.DEADLINE_OVERDUE: "A due transition could not be decided from current evidence.",
    ReasonCode.GUARD_REJECTED: "The transition guard rejected the request.",
    ReasonCode.MANUAL_RESTART: "An administrator asserted the initial state; valid data has not confirmed it yet.",
}


def reasons() -> dict[str, Any]:
    return {
        "version": 1,
        "codes": [
            {"code": code.value, "description": REASONS[code], "since_version": 1}
            for code in ReasonCode
        ],
    }


def machine_description(machine: Machine) -> dict[str, Any]:
    return {
        "states": machine.definition.states,
        "initial": machine.definition.initial,
        "commands": machine.definition.commands,
        "deadline_seconds": machine.definition.deadline_seconds,
        "sessions": machine.definition.sessions,
        "allow_manual_restart": machine.definition.allow_manual_restart,
        **(
            {
                "deadline_by_state": machine.definition.deadline_by_state,
                "absolute_transition": machine.definition.absolute_transition,
                "unknown_deadline": "keep_and_block_transition",
            }
            if machine.definition.deadline_v2
            else {}
        ),
        "transitions": [
            {
                "source": t.source,
                "target": t.target,
                "event": t.event,
                "reason": t.reason,
                "new_episode": t.new_episode,
                "ends_episode": t.ends_episode,
                "argument_guard": t.argument_guard is not None,
            }
            for t in machine.definition.transitions
        ],
    }


def describe_types(config: RegistryConfig | None, base: TypeRegistry) -> list[dict[str, Any]]:
    types = registry_types(config, base) if config else base
    result = []
    for declaration in types.types.values():
        data = declaration.description()
        if declaration.type_id == "test.boolean":
            # Add editor conditions to API metadata only. The original package
            # schema remains byte-compatible in historical fingerprints.
            data["parameter_schema"]["allOf"] = [
                {
                    "if": {"properties": {"operator": {"const": operation}}},
                    "then": {
                        "required": fields,
                        "properties": {field: {"not": {"type": "null"}} for field in fields},
                    },
                }
                for operation, fields in (
                    ("compare", ["comparison"]),
                    ("map", ["mapping"]),
                    ("bucket", ["initial", "lower", "upper"]),
                )
            ]
        result.append(data)
    return result


def configuration_schema() -> dict[str, Any]:
    schema = RegistryConfig.model_json_schema()
    schema["allOf"] = [
        {
            "if": {"required": ["definitions"], "properties": {"definitions": {"minItems": 1}}},
            "then": {
                "required": ["fingerprint_version"],
                "properties": {"fingerprint_version": {"enum": [2, 3, 4]}},
            },
        }
    ]
    definitions = schema["$defs"]
    # Runtime resolution injects a concrete rule; the wire schema accepts exactly one representation.
    source = definitions["Source"]
    source["required"] = [key for key in source["required"] if key != "freshness"]
    source["oneOf"] = [
        {"required": ["freshness"], "not": {"required": ["freshness_selection"]}},
        {
            "required": ["freshness_selection"],
            "properties": {"freshness_selection": {"not": {"type": "null"}}},
            "not": {"required": ["freshness"]},
        },
    ]
    schema["allOf"].append(
        {
            "if": {
                "required": ["config_schema_version"],
                "properties": {"config_schema_version": {"const": 4}},
            },
            "then": {
                "required": ["fingerprint_version"],
                "properties": {
                    "fingerprint_version": {"const": 4},
                    "sources": {
                        "items": {
                            "required": ["freshness_selection"],
                            "not": {"required": ["freshness"]},
                        }
                    },
                },
            },
            "else": {
                "properties": {
                    "fingerprint_version": {"enum": [1, 2, 3]},
                    "sources": {
                        "items": {
                            "required": ["freshness"],
                            "not": {"required": ["freshness_selection"]},
                        }
                    },
                }
            },
        }
    )
    conditions = {
        "Transform": [
            ("kind", "type_cast", ["target"]),
            ("kind", "unit_scale", ["factor", "offset"]),
            ("kind", "map_ref", ["catalog_id"]),
        ],
        "Freshness": [
            ("mode", "report_heartbeat", ["interval_s"]),
            ("mode", "periodic_ttl", ["interval_s"]),
            ("mode", "liveness_source", ["interval_s", "liveness_source"]),
        ],
        "Dependency": [("monitor", "source", ["source_id"])],
        "AdapterConfig": [
            ("kind", "ha_state", ["ha_entity_id"]),
            ("kind", "ha_attribute", ["ha_entity_id", "ha_attribute"]),
            ("kind", "mqtt", ["mqtt_topic"]),
        ],
    }
    for name, rules in conditions.items():
        if name in definitions:
            definitions[name]["allOf"] = [
                {
                    "if": {"properties": {key: {"const": value}}, "required": [key]},
                    "then": {
                        "required": fields,
                        "properties": {field: {"not": {"type": "null"}} for field in fields},
                    },
                }
                for key, value, fields in rules
            ]
    return enrich_definition_schema(schema)


def definition_schema() -> dict[str, Any]:
    return enrich_definition_schema(DeclarativeDefinition.model_json_schema())


def enrich_definition_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema["$defs"]["FusionRule"]["properties"]["strategy"]["enum"] = sorted(FUSION_INPUTS)
    schema["$defs"]["FormulaRule"]["allOf"] = [
        {
            "if": {"properties": {"name": {"const": name}}},
            "then": {
                "properties": {
                    "version": {"enum": sorted(v for n, v in FORMULA_SCHEMAS if n == name)}
                }
            },
        }
        for name in sorted({name for name, _ in FORMULA_SCHEMAS})
    ]
    schema["$defs"]["FormulaRule"]["properties"]["name"]["enum"] = sorted(
        {name for name, _ in FORMULA_SCHEMAS}
    )
    definitions = schema["$defs"]
    definitions["Schema"]["properties"]["grace_capable"]["const"] = False
    definitions["Schema"]["properties"]["grace_parameter"]["const"] = None
    definitions["FieldSchema"]["allOf"] = [
        {
            "if": {"properties": {"type": {"const": "enum"}}},
            "then": {
                "required": ["value_catalog"],
                "properties": {"value_catalog": {"minItems": 1}},
            },
        }
    ]
    definitions["TemporalRule"]["allOf"] = [
        {
            "if": {"properties": {"operation": {"const": "delta"}}},
            "then": {"properties": {"args": {"minItems": 2, "maxItems": 2}}},
            "else": {"properties": {"args": {"minItems": 1, "maxItems": 1}}},
        }
    ]
    definitions["BooleanRule"]["allOf"] = [
        {
            "if": {"properties": {"op": {"const": "not"}}},
            "then": {"properties": {"args": {"minItems": 1, "maxItems": 1}}},
        }
    ]
    schema["x-semantic-validation"] = {
        "endpoint": "/api/v1/registry/validate",
        "rules": [
            {"code": code, "constraint": constraint} for code, constraint in SEMANTIC_RULES.items()
        ],
        "structural_code_families": ["schema.*", "parameter.*"],
        "model_constraints": {
            "BucketRule": ["lower <= upper"],
            "DeclarativeDefinition": [
                "distinct inputs",
                "outputs equal schema.fields",
                "refresh inputs declared",
                "grace uses temporal rule duration",
            ],
            "MachineDefinition": [
                "distinct states",
                "initial/source/target belong to states",
                "declared events and commands",
                "deadline event requires deadline_s",
                "reserved input/deadline events",
                "typed argument_equals",
                "no model-reserved argument names",
            ],
            "LiteralRule": ["value matches schema"],
            "MapRule": ["all entry values match result schema"],
            "FieldSchema": ["enum requires a value catalog where used"],
            "Schema": [
                "nonempty fields",
                "available_projection refers to fields",
                "grace parameter matches capability",
            ],
        },
    }
    return schema


def capabilities(config: RegistryConfig | None, base: TypeRegistry) -> dict[str, Any]:
    return {
        "capability_version": 1,
        "authoring_protocol": 2,
        "draft_lifecycle": True,
        "continuation": True,
        "activation_receipts": True,
        "language_versions": [1, 2, 3, 4],
        "machine_deadline_v2": True,
        "api_protocol": 1,
        "config_schema_version": 1,
        "config_schema_versions": [1, 2, 3, 4],
        "bridge_protocol_versions": [1, 2],
        "fingerprint_versions": [1, 2, 3, 4],
        "freshness_templates": True,
        "portable_format_versions": [1, 2],
        "managed_catalog": {
            "language_version": 4,
            "fingerprint_version": 3,
            "write_limit": 100,
            "page_limit": 100,
        },
        "evidence_reference_versions": [1, 2],
        "metadata_access": "authenticated_consumer_or_admin",
        "history": {
            "kinds": list(HISTORY_TABLES),
            "endpoint": "/api/v1/history/query/{kind}",
            "role": "admin",
            "order": "time_desc_key_desc",
            "time_comparison": "utc_instant",
            "cursor": "scope_bound_keyset",
            "limit": 200,
        },
        "draft_id": {"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", "generated": "uuid4"},
        "map_keys": {
            "format": "canonical_json_scalar",
            "numbers": "decimal_without_exponent_or_trailing_fraction_zeros",
            "strings": "json_quoted_exact",
            "legacy_catalogs": "unchanged_python_str",
        },
        "declarative_fingerprint_version": 2,
        "compatibility": {
            "alpha3_documents": True,
            "alpha3_type_fingerprints": "unchanged",
            "definition_versions": "immutable_semantics",
            "activation": "explicit_atomic_occ",
        },
        "types": describe_types(config, base),
        "schemas": {
            "registry": configuration_schema(),
            "definition": definition_schema(),
            "history_query": HistoryQuery.model_json_schema(),
        },
        "operators": [
            {
                "op": name,
                "stateful": name in {"bucket", "temporal", "machine"},
                "schema_ref": f"#/$defs/{model}",
            }
            for name, model in (
                ("input", "InputRule"),
                ("parameter", "ParameterRule"),
                ("time_reached", "TimeReachedRule"),
                ("known_automatic", "KnownAutomaticRule"),
                ("configuration_hint", "ConfigurationHintRule"),
                *(
                    (name, "TextRule")
                    for name in (
                        "lower",
                        "strip",
                        "normalize_key",
                        "matches",
                        "contains",
                        "compound_key",
                    )
                ),
                ("ref", "ReferenceRule"),
                ("literal", "LiteralRule"),
                ("and", "BooleanRule"),
                ("or", "BooleanRule"),
                ("not", "BooleanRule"),
                ("compare", "CompareRule"),
                ("map", "MapRule"),
                ("first_match", "FirstMatchRule"),
                ("bucket", "BucketRule"),
                ("formula", "FormulaRule"),
                ("fusion", "FusionRule"),
                ("temporal", "TemporalRule"),
                ("machine", "MachineRule"),
                ("not_applicable", "NotApplicableRule"),
            )
        ],
        "formulas": [
            {
                "name": name,
                "version": version,
                "input_schema": item.input.model_dump(mode="json"),
                "output_schema": item.output.model_dump(mode="json"),
                "category": item.category,
                "lifecycle": item.lifecycle,
                "inputs": [
                    {
                        "name": i.name,
                        "schema": i.schema.model_dump(mode="json"),
                        "quantity": i.quantity,
                        "required": i.required,
                    }
                    for i in item.inputs
                ],
            }
            for (name, version), item in FORMULA_SCHEMAS.items()
        ],
        "fusion": [
            {
                "strategy": name,
                "priority": "argument_order" if name in {"first_healthy", "first_usable"} else None,
                "conflict_detection": name in {"latest", "test_agreement"},
            }
            for name in FUSION_INPUTS
        ],
        "temporal": [
            {
                "operation": name,
                "arguments": 2 if name == "delta" else 1,
                "stateful": name not in {"delta"},
                "restores_absolute_deadlines": True,
            }
            for name in ("stable_for", "dwell", "grace", "edge", "delta", "age")
        ],
        "state_machine": {
            "schema_ref": "#/$defs/MachineDefinition",
            "max_parallel_deadlines": 1,
            "guard_rules": "stateless; temporal truths can be supplied through contract inputs",
            "command_effect": "guarded_transition_only",
            "administrative_restart": {
                "default": False,
                "definition_flag": "allow_manual_restart",
                "role": "admin",
                "preview": "/api/v1/manual-restarts/preview/{contract_id}",
                "endpoint": "/api/v1/manual-restarts",
                "effect": "declared_initial_state_new_episode",
                "marker": "state_machine.manual_restart",
                "quality_until_data_confirmation": "degraded",
            },
            "sessions": True,
            "episodes": True,
        },
        "references": {
            "external": ["source_id", "contract_id.field", "contract_id (value)"],
            "internal": "named acyclic rules",
            "dynamic_lookup": False,
            "typed_sources": "value_schema or boolean/numeric/text source_type",
        },
        "limits": {
            "document_bytes": 2_000_000,
            "document_depth": 32,
            "rule_depth": MAX_DEPTH,
            "definition_nodes": MAX_NODES,
            "evaluation_steps": MAX_NODES * 2,
            "registry_evaluation_steps": 8192,
            "inputs": MAX_INPUTS,
            "outputs": MAX_FIELDS,
            "definitions": 128,
            "drafts": 100,
            "history_page": 200,
            "trace_retention": "latest_relevant_committed_per_contract",
            "trace_steps": 1024,
            "trace_values": "no_raw_values; machine_guard_decisions_and_fusion_indices_only",
            "trace_evidence_per_step": 64,
        },
        "reasons": reasons(),
    }
