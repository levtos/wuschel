"""Explicit migration to named, typed catalog classes."""

import copy
from collections import Counter
from collections.abc import Callable
from typing import Any

from core_contracts.extensions.catalog.interface_upgrade import CLASS_CODES, entry_map
from core_contracts.extensions.catalog.managed_catalog import (
    LEGACY_DIMENSIONS,
    definition_digest,
    head,
    head_key,
)
from core_contracts.freshness_templates import upgrade_document
from core_contracts.model import checksum
from core_contracts.registry import RegistryConfig, storage_document
from core_contracts.rules.catalog_models import CatalogClass, ClassValue, ManagedCatalog

MIGRATION = "named_classes"
ASSIGNMENTS = {
    "media.homepods": ("volume_offset", "damped"),
    "media.ps5": ("sound_mode", "light_look", "volume_offset"),
    "media.pc": ("sound_mode", "light_look", "volume_offset"),
    "media.appletv": ("content", "volume_offset"),
    "media.appletv_titles": ("content", "volume_offset"),
    "media.stash": ("content", "volume_offset"),
    "media.tv_device": ("tv_device",),
    "media.tv_activity": ("tv_activity",),
}
DEFAULTS = {
    "media.ps5": {"sound_mode": "home_cinema"},
    "media.pc": {"sound_mode": "headset"},
    "media.appletv": {"content": "normal"},
    "media.appletv_titles": {"content": "normal"},
    "media.stash": {"content": "private"},
}


def enum(key: str, name: str, default: str, values: dict[str, tuple[str, str]]) -> dict[str, Any]:
    return {
        "class_key": key,
        "display_name": name,
        "kind": "enum",
        "default": default,
        "values": {
            code: {"display_name": label, "description": explanation}
            for code, (label, explanation) in values.items()
        },
    }


CLASSES = (
    enum(
        "sound_mode",
        "Soundmodus",
        "home_cinema",
        {
            "home_cinema": ("Heimkino", "Der Inhalt wird im Heimkino-Modus wiedergegeben."),
            "grind": ("Grind", "Der Inhalt wird im Grind-Modus wiedergegeben."),
            "headset": ("Headset", "Der Ton wird über das Headset ausgegeben."),
        },
    ),
    enum(
        "light_look",
        "Licht-Look",
        "standard",
        {
            "standard": (
                "Standard",
                "Die gewöhnliche Beleuchtung für diesen Katalog bleibt aktiv.",
            ),
        },
    ),
    {
        "class_key": "volume_offset",
        "display_name": "Lautstärke-Korrektur",
        "kind": "integer",
        "default": 0,
        "unit": "Punkte",
        "minimum": -30,
        "maximum": 30,
    },
    {"class_key": "damped", "display_name": "Dämpfen", "kind": "boolean", "default": False},
    enum(
        "content",
        "Inhalt",
        "normal",
        {
            "normal": ("Normal", "Der Inhalt kann gewöhnlich wiedergegeben werden."),
            "private": ("Privat", "Der Inhalt soll privat bleiben."),
        },
    ),
    enum(
        "tv_device",
        "Gerät",
        "other",
        {
            "ps5": ("PlayStation 5", "Die Quelle zeigt die PlayStation 5."),
            "appletv": ("Apple TV", "Die Quelle zeigt das Apple TV."),
            "other": ("Sonstiges", "Die Quelle zeigt ein anderes Gerät."),
        },
    ),
    enum(
        "tv_activity",
        "Aktivität",
        "other",
        {
            "tv": ("Fernsehen", "Es läuft Live-Fernsehen."),
            "streaming": ("Streaming", "Es läuft ein über eine App gestreamter Inhalt."),
            "gaming": ("Spielen", "Die Quelle wird zum Spielen genutzt."),
            "other": ("Sonstiges", "Die Quelle wird für eine andere Aktivität genutzt."),
        },
    ),
)


def upgrade_catalog(catalog: ManagedCatalog) -> dict[str, Any]:
    row = catalog.model_dump(mode="json")
    row["version"] += 1
    row["class_assignments"] = [
        {"class_key": key, **({"default": default} if default is not None else {})}
        for key in ASSIGNMENTS[catalog.catalog_id]
        for default in (DEFAULTS.get(catalog.catalog_id, {}).get(key),)
    ]
    return row


def convert_entry(row: dict[str, Any], catalog: ManagedCatalog) -> dict[str, Any]:
    result = {**row, "classification": None}
    raw = row.get("classification")
    if raw is None:
        return result
    code = CLASS_CODES.get(raw, raw)
    catalog_id = catalog.catalog_id
    values: dict[str, ClassValue]
    if catalog_id == "media.homepods":
        if code == "normal":
            return result
        if code == "mute":
            values = {"damped": True}
        elif code in (offsets := {"boost": 15, "quieter": -10}):
            values = {"volume_offset": offsets[code]}
        else:
            raise ValueError("catalog_migration_unmapped")
    else:
        dimension = LEGACY_DIMENSIONS[catalog_id]
        if dimension.startswith("tv_") and raw in (
            catalog.assigned_classes[dimension].values or {}
        ):
            code = raw
        values = {dimension: code}
    catalog.validate_classes(values)
    result["class_values"] = values
    return result


def rewrite_lookups(value: Any, versions: dict[tuple[str, int], int]) -> Any:
    if isinstance(value, list):
        return [rewrite_lookups(item, versions) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: rewrite_lookups(item, versions) for key, item in value.items()}
    if value.get("op") == "managed_catalog_lookup":
        identity = (value["catalog_id"], value["definition_version"])
        if identity in versions:
            result["definition_version"] = versions[identity]
    return result


HOME_PODS_ADJUSTMENT = "media.homepods.track_adjustment"


def typed_absence(schema: dict[str, Any]) -> dict[str, Any]:
    if schema["type"] == "enum":
        value: ClassValue = schema["value_catalog"][0]
    else:
        value = False if schema["type"] == "bool" else 0
    return {
        "op": "map",
        "arg": {
            "op": "not_applicable",
            "arg": {
                "op": "literal",
                "value": True,
                "schema": {"type": "bool", "required": True, "physical_state": False},
            },
        },
        "schema": schema,
        "entries": {"false": value},
    }


def dimension_output(rule: dict[str, Any], key: str, schema: dict[str, Any]) -> dict[str, Any]:
    op = rule["op"]
    if op == "first_match":
        return {
            **rule,
            "cases": [
                {**case, "then": dimension_output(case["then"], key, schema)}
                for case in rule["cases"]
            ],
        }
    if op == "managed_catalog_lookup":
        return {**rule, "schema": schema, "class_key": key}
    if op == "not_applicable" or (op == "map" and rule["arg"]["op"] == "not_applicable"):
        return typed_absence(schema)
    raise ValueError("catalog_migration_contract_shape")


def add_output(row: dict[str, Any], key: str, schema: dict[str, Any], rule: dict[str, Any]) -> None:
    row["schema"]["fields"][key] = schema
    row["outputs"][key] = rule
    projected = list(row["schema"].get("available_projection", []))
    if key not in projected:
        row["schema"]["available_projection"] = [*projected, key]


def add_media_fields(row: dict[str, Any], classes: dict[str, CatalogClass]) -> None:
    dimensions = {
        "media.homepods_class": ("volume_offset", "damped"),
        "media.ps5_class": ("sound_mode", "light_look", "volume_offset"),
        "media.pc_class": ("sound_mode", "light_look", "volume_offset"),
        "media.appletv_class": ("content", "volume_offset"),
        "media.appletv_title_class": ("content", "volume_offset"),
        "media.stash_class": ("content", "volume_offset"),
        "media.stash_slot": ("content", "volume_offset"),
    }.get(row["type_id"], ())
    for key in dimensions:
        schema = classes[key].field_schema().model_dump(mode="json")
        add_output(row, key, schema, dimension_output(row["outputs"]["value"], key, schema))


def connect_stash_fields(
    document: dict[str, Any],
    definitions: dict[tuple[str, int], dict[str, Any]],
    contracts: dict[str, dict[str, Any]],
    offset: dict[str, Any],
) -> None:
    for contract in list(contracts.values()):
        if contract["type_id"] != "media.stash_class":
            continue
        original = definitions.get((contract["type_id"], contract["type_version"]))
        if not original or "volume_offset" in original["outputs"]:
            continue
        inputs = {row["name"]: row["ref"] for row in contract["inputs"]}
        slots = sorted(name.removesuffix("_active") for name in inputs if name.endswith("_active"))
        if not slots or any(slot + "_class" not in inputs for slot in slots):
            continue
        sources = {
            slot: contracts.get(inputs[slot + "_class"].removesuffix(".value")) for slot in slots
        }
        if any(
            source is None
            or "volume_offset"
            not in definitions.get((source["type_id"], source["type_version"]), {}).get(
                "outputs", {}
            )
            for source in sources.values()
        ):
            continue
        # Use a separate recipe when the aggregate has new inputs. Other
        # instances of the original recipe keep their existing input contract.
        version = (
            max(
                row["type_version"]
                for row in document["definitions"]
                if row["type_id"] == original["type_id"]
            )
            + 1
        )
        target = copy.deepcopy(original)
        target["type_version"] = version
        contract["type_version"] = version
        document["definitions"].append(target)

        def read(name: str) -> dict[str, Any]:
            return {"op": "input", "name": name}

        cases = []
        for slot in slots:
            source = sources[slot]
            assert source is not None
            key = slot + "_offset"
            target["inputs"].append({"name": key, "schema": offset})
            contract["inputs"].append(
                {"name": key, "ref": source["contract_id"] + ".volume_offset"}
            )
            private = {
                "op": "compare",
                "left": read(slot + "_class"),
                "comparison": "eq",
                "right": {
                    "op": "literal",
                    "value": "private",
                    "schema": {"type": "text", "required": True, "physical_state": False},
                },
            }
            cases.append(
                {
                    "when": {"op": "and", "args": [read(slot + "_active"), private]},
                    "then": read(key),
                }
            )
        cases.extend(
            {"when": read(slot + "_active"), "then": read(slot + "_offset")} for slot in slots
        )
        cases.append(
            {
                "when": {
                    "op": "not",
                    "args": [{"op": "or", "args": [read(slot + "_active") for slot in slots]}],
                },
                "then": typed_absence(offset),
            }
        )
        add_output(target, "volume_offset", offset, {"op": "first_match", "cases": cases})
        add_output(
            target,
            "content",
            target["schema"]["fields"]["value"],
            copy.deepcopy(target["outputs"]["value"]),
        )


def connect_media_fields(document: dict[str, Any], classes: dict[str, CatalogClass]) -> None:
    definitions = {
        (row["type_id"], row["type_version"]): row for row in document.get("definitions", [])
    }
    contracts = {row["contract_id"]: row for row in document["contracts"]}
    offset = classes["volume_offset"].field_schema().model_dump(mode="json")
    app = contracts.get("media.appletv.app_class")
    title = contracts.get("media.appletv.title_class")
    if app and title:
        app_type = definitions.get((app["type_id"], app["type_version"]))
        title_type = definitions.get((title["type_id"], title["type_version"]))
        if (
            app_type
            and title_type
            and "volume_offset" in app_type["outputs"]
            and "volume_offset" in title_type["outputs"]
        ):
            title_type["inputs"].append({"name": "application_volume_offset", "schema": offset})
            title["inputs"].append(
                {"name": "application_volume_offset", "ref": app["contract_id"] + ".volume_offset"}
            )
            title_type["outputs"]["volume_offset"] = {
                "op": "catalog_offset_sum",
                "application": {"op": "input", "name": "application_volume_offset"},
                "title": title_type["outputs"]["volume_offset"],
            }
    connect_stash_fields(document, definitions, contracts, offset)
    track = contracts.get("media.homepods.track_class")
    if track:
        track_type = definitions.get((track["type_id"], track["type_version"]))
        if (
            track_type
            and "volume_offset" in track_type["outputs"]
            and "damped" in track_type["outputs"]
        ):
            definition = copy.deepcopy(track_type)
            definition.update(
                type_id=HOME_PODS_ADJUSTMENT,
                type_version=1,
                display_name="HomePods Lautstärke-Korrektur",
            )
            definition["outputs"].pop("value")
            definition["schema"]["fields"].pop("value")
            definition["schema"]["available_projection"] = ["volume_offset", "damped"]
            track_type["outputs"].pop("volume_offset")
            track_type["outputs"].pop("damped")
            track_type["schema"]["fields"].pop("volume_offset")
            track_type["schema"]["fields"].pop("damped")
            track_type["schema"]["available_projection"] = [
                key
                for key in track_type["schema"].get("available_projection", [])
                if key not in {"volume_offset", "damped"}
            ]
            document["definitions"].append(definition)
            document["contracts"].append(
                {
                    **copy.deepcopy(track),
                    "contract_id": HOME_PODS_ADJUSTMENT,
                    "type_id": HOME_PODS_ADJUSTMENT,
                    "type_version": 1,
                    "display_name": "HomePods Lautstärke-Korrektur",
                }
            )


def projection_changes(runtime: Any, before: Any, after: Any) -> list[dict[str, Any]]:
    import re

    from core_contracts.registry import registry_types
    from core_contracts.schema import primary_field

    def fields(config: RegistryConfig) -> dict[str, tuple[str | None, list[str]]]:
        types = registry_types(config, runtime.types)
        result = {}
        for contract in config.contracts:
            if contract.enabled and contract.ha_projection:
                schema = types.get(contract.type_id, contract.type_version).schema
                result[contract.contract_id] = (
                    primary_field(schema, contract.type_id),
                    list(schema.fields),
                )
        return result

    old = fields(RegistryConfig.model_validate(before))
    new = fields(RegistryConfig.model_validate(after))
    return [
        {
            "contract_id": key,
            "entity_id": "sensor.wuschel_" + re.sub(r"[^a-z0-9_]+", "_", key.casefold()),
            "unique_id": f"{runtime.installation_id}:{key}",
            "primary_field": primary,
            "old_fields": old.get(key, (None, []))[1],
            "fields": values,
        }
        for key, (primary, values) in sorted(new.items())
        if primary != old.get(key, (None, []))[0] or set(values) != set(old.get(key, (None, []))[1])
    ]


def seal(package: dict[str, Any]) -> dict[str, Any]:
    from core_contracts.portable import objects, package_checksum, payload_digest

    version = package["format_version"]
    config = package["config"]
    package["manifest"].update(
        config_digest=package_checksum(config, version),
        object_digests={
            key: package_checksum(row, version) for key, row in objects(config).items()
        },
    )
    package["manifest"]["payload_digest"] = payload_digest(package)
    return package


def migration_blockers(package: dict[str, Any]) -> list[dict[str, Any]]:
    config = RegistryConfig.model_validate(package["config"])
    classes = {str(row["class_key"]): CatalogClass.model_validate(row) for row in CLASSES}
    classes.update({row.class_key: row for row in config.catalog_classes})
    catalogs = {
        (row.catalog_id, row.version): row
        for row in config.catalogs
        if isinstance(row, ManagedCatalog)
        and row.catalog_id in ASSIGNMENTS
        and not row.class_assignments
    }
    issues = []
    for content in package["catalog_contents"]:
        old = catalogs.get((content["catalog_id"], content["definition_version"]))
        if old is None:
            continue
        catalog = ManagedCatalog.model_validate(upgrade_catalog(old))
        catalog.resolve_assignments(classes)
        counts = Counter(
            row["classification"]
            for row in content["entries"]
            if row.get("classification") is not None
        )
        invalid = {}
        for value, count in counts.items():
            try:
                convert_entry({"classification": value}, catalog)
            except ValueError:
                invalid[value] = count
        if invalid:
            issues.append({"catalog_id": old.catalog_id, "values": invalid})
    return issues


def build_package(package: dict[str, Any]) -> dict[str, Any]:
    from core_contracts.portable import read_package

    return migrate_package(copy.deepcopy(read_package(package)))


def migrate_package(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("legacy") or result["scope"] != "configuration":
        raise ValueError("catalog_migration_requires_configuration")
    document = upgrade_document(result["config"], version=5)
    config = RegistryConfig.model_validate(document)
    previous = {
        (catalog.catalog_id, catalog.version): catalog
        for catalog in config.catalogs
        if isinstance(catalog, ManagedCatalog)
        and catalog.lifecycle != "retired"
        and catalog.catalog_id in ASSIGNMENTS
        and not catalog.class_assignments
    }
    if not previous:
        raise ValueError("catalog_migration_no_legacy_catalogs")
    class_rows = {str(row["class_key"]): row for row in CLASSES}
    class_rows.update({str(row["class_key"]): row for row in document.get("catalog_classes", [])})
    document["catalog_classes"] = list(class_rows.values())
    classes = {key: CatalogClass.model_validate(row) for key, row in class_rows.items()}
    versions = {}
    for row in document["catalogs"]:
        key = (row["catalog_id"], row["version"])
        if key in previous:
            upgraded = upgrade_catalog(previous[key])
            before = row["version"]
            row.update(upgraded)
            versions[(row["catalog_id"], before)] = row["version"]

    changed_types = {}
    next_version: dict[str, int] = {}
    for row in document.get("definitions", []):
        key = row["type_id"]
        next_version[key] = max(row["type_version"], next_version.get(key, 0))
    for row in document.get("definitions", []):
        changed = rewrite_lookups(row, versions)
        if changed != row:
            add_media_fields(changed, classes)
            key = row["type_id"]
            next_version[key] += 1
            changed_types[(key, row["type_version"])] = next_version[key]
            changed["type_version"] = next_version[key]
            row.update(changed)
    for row in document["contracts"]:
        row["type_version"] = changed_types.get(
            (row["type_id"], row["type_version"]), row["type_version"]
        )

    connect_media_fields(document, classes)
    checked = RegistryConfig.model_validate(document)
    destinations = {
        (catalog.catalog_id, catalog.version): catalog
        for catalog in checked.catalogs
        if isinstance(catalog, ManagedCatalog)
    }
    for content in result["catalog_contents"]:
        key = (content["catalog_id"], content["definition_version"])
        if key not in previous:
            continue
        original = entry_map(content["entries"])
        updated = destinations[(content["catalog_id"], versions[key])]
        converted = [convert_entry(row, updated) for row in content["entries"]]
        content["class_migration"] = {
            "operation": MIGRATION,
            "source_version": key[1],
            "source_revision": content["source_revision"],
            "source_definition_digest": content["definition_digest"],
            "source_entries_digest": checksum(original),
        }
        content.update(
            entries=converted,
            definition_version=updated.version,
            definition_digest=definition_digest(updated),
        )
    result.update(config=document, format_version=3)
    return seal(result)


RECONFIGURE = "named_classes_reconfigure"


def filter_classes(row: dict[str, Any], target: ManagedCatalog) -> dict[str, Any]:
    updated = {**row}
    values = {
        key: value
        for key, value in row.get("class_values", {}).items()
        if key in target.assigned_classes
    }
    updated.pop("class_values", None)
    if values:
        target.validate_classes(values)
        updated["class_values"] = values
    return updated


def affected_definitions(
    document: dict[str, Any], versions: dict[tuple[str, int], int], classes: dict[str, CatalogClass]
) -> None:
    new_versions: dict[str, int] = {}
    replacements = {}
    for row in document.get("definitions", []):
        key = row["type_id"]
        new_versions[key] = max(row["type_version"], new_versions.get(key, 0))
    for row in document.get("definitions", []):
        updated = rewrite_lookups(row, versions)
        if updated == row:
            continue

        def schemas(node: Any, keys: set[str]) -> Any:
            if isinstance(node, list):
                return [schemas(child, keys) for child in node]
            if not isinstance(node, dict):
                return node
            result = {name: schemas(child, keys) for name, child in node.items()}
            if (
                result.get("op") == "managed_catalog_lookup"
                and (key := result.get("class_key")) in classes
            ):
                keys.add(key)
                result["schema"] = classes[key].field_schema().model_dump(mode="json")
            return result

        for name, rule in updated["outputs"].items():
            keys: set[str] = set()
            updated["outputs"][name] = schemas(rule, keys)
            if len(keys) == 1 and name in keys:
                updated["schema"]["fields"][name] = (
                    classes[name].field_schema().model_dump(mode="json")
                )
                if rule["op"] == "first_match":
                    updated["outputs"][name] = dimension_output(
                        rule, name, updated["schema"]["fields"][name]
                    )
        for name, rule in updated.get("rules", {}).items():
            updated["rules"][name] = schemas(rule, set())
        key = row["type_id"]
        new_versions[key] += 1
        replacements[(key, row["type_version"])] = new_versions[key]
        updated["type_version"] = new_versions[key]
        row.update(updated)
    for row in document["contracts"]:
        row["type_version"] = replacements.get(
            (row["type_id"], row["type_version"]), row["type_version"]
        )


def output_basis(
    rule: dict[str, Any],
    catalog: ManagedCatalog,
    rules: dict[str, Any],
    seen: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Reuse a lookup's inputs and applicability, with the new class as its result."""
    op = rule["op"]
    if op == "managed_catalog_lookup":
        if (rule["catalog_id"], rule["definition_version"]) == (
            catalog.catalog_id,
            catalog.version,
        ):
            return rule
    elif op == "ref":
        name = rule["name"]
        if name in rules and name not in seen:
            return output_basis(rules[name], catalog, rules, seen | {name})
    elif op == "map":
        return output_basis(rule["arg"], catalog, rules, seen)
    elif op == "catalog_offset_sum":
        return output_basis(rule["title"], catalog, rules, seen)
    elif op in {"not_applicable", "literal"}:
        return {"op": "not_applicable"}
    elif op == "first_match":
        cases = []
        for case in rule["cases"]:
            branch = output_basis(case["then"], catalog, rules, seen)
            if branch is None:
                return None
            cases.append({**case, "then": branch})
        if any(case["then"]["op"] != "not_applicable" for case in cases):
            return {"op": "first_match", "cases": cases}
    return None


def output_options(config: RegistryConfig) -> list[dict[str, Any]]:
    from core_contracts.registry import storage_document

    document = storage_document(config)
    definitions = {
        (row["type_id"], row["type_version"]): row for row in document.get("definitions", [])
    }
    options = []
    for contract in document["contracts"]:
        definition = definitions.get((contract["type_id"], contract["type_version"]))
        if not definition or definition.get("lifecycle") == "retired":
            continue
        for catalog in config.catalogs:
            if not isinstance(catalog, ManagedCatalog) or not catalog.class_assignments:
                continue
            for field, rule in definition["outputs"].items():
                basis = output_basis(rule, catalog, definition.get("rules", {}))
                if basis and basis["op"] != "not_applicable":
                    options.append(
                        {
                            "catalog_id": catalog.catalog_id,
                            "contract_id": contract["contract_id"],
                            "display_name": contract.get("display_name") or contract["contract_id"],
                            "from_field": field,
                            "fields": list(definition["outputs"]),
                        }
                    )
    return options


def publish_class_outputs(
    document: dict[str, Any],
    outputs: Any,
    previous: RegistryConfig,
    catalogs: dict[tuple[str, int], ManagedCatalog],
) -> None:
    from core_contracts.schema import Schema, primary_field

    if not isinstance(outputs, list) or len(outputs) > 256:
        raise ValueError("catalog_class_output_invalid")
    definitions = {
        (row["type_id"], row["type_version"]): row for row in document.get("definitions", [])
    }
    contracts = {row["contract_id"]: row for row in document["contracts"]}
    old_classes = {
        catalog.catalog_id: catalog.assigned_classes
        for catalog in previous.catalogs
        if isinstance(catalog, ManagedCatalog)
    }
    requests: dict[str, list[dict[str, str]]] = {}
    for request in outputs:
        if (
            not isinstance(request, dict)
            or set(request) != {"catalog_id", "class_key", "contract_id", "from_field"}
            or not all(isinstance(value, str) and value for value in request.values())
        ):
            raise ValueError("catalog_class_output_invalid")
        requests.setdefault(request["contract_id"], []).append(request)
    targets = {catalog.catalog_id: catalog for catalog in catalogs.values()}
    for contract_id, fields in requests.items():
        contract = contracts.get(contract_id)
        if not contract:
            raise ValueError("catalog_class_output_invalid")
        type_id = contract["type_id"]
        base = definitions.get((type_id, contract["type_version"]))
        if not base or base.get("lifecycle") == "retired":
            raise ValueError("catalog_class_output_invalid")
        candidate = copy.deepcopy(base)
        for field in fields:
            catalog = targets.get(field["catalog_id"])
            key = field["class_key"]
            source = base["outputs"].get(field["from_field"])
            if (
                catalog is None
                or source is None
                or key in old_classes.get(catalog.catalog_id, {})
                or (definition := catalog.assigned_classes.get(key)) is None
            ):
                raise ValueError("catalog_class_output_invalid")
            if key in candidate["outputs"]:
                raise ValueError("catalog_class_output_conflict")
            basis = output_basis(source, catalog, base.get("rules", {}))
            if not basis or basis["op"] == "not_applicable":
                raise ValueError("catalog_class_output_invalid")
            schema = definition.field_schema().model_dump(mode="json")
            add_output(candidate, key, schema, dimension_output(basis, key, schema))
        if contract.get("ha_projection") and primary_field(
            Schema.model_validate(candidate["schema"]), type_id
        ) != primary_field(Schema.model_validate(base["schema"]), type_id):
            raise ValueError("catalog_class_output_conflict")
        version = max(number for name, number in definitions if name == type_id) + 1
        candidate["type_version"] = version
        definitions[(type_id, version)] = candidate
        document["definitions"].append(candidate)
        contract["type_version"] = version


def configure_package(
    package: dict[str, Any], classes: Any, assignments: Any, outputs: Any = None
) -> dict[str, Any]:
    from core_contracts.portable import read_package

    return reconfigure_package(copy.deepcopy(read_package(package)), classes, assignments, outputs)


def reconfigure_package(
    result: dict[str, Any],
    classes: Any,
    assignments: Any,
    outputs: Any,
    load_contents: Callable[[set[tuple[str, int]]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    document = result["config"]
    if document.get("config_schema_version") != 5 or result["scope"] != "configuration":
        raise ValueError("registry_schema_5_required")
    if not isinstance(classes, list) or not isinstance(assignments, dict):
        raise ValueError("catalog_migration_invalid")
    before = RegistryConfig.model_validate(document)
    document["catalog_classes"] = classes
    known = {
        (row.catalog_id, row.version): row
        for row in before.catalogs
        if isinstance(row, ManagedCatalog)
    }
    if set(assignments) - {key for key, _ in known}:
        raise ValueError("catalog_migration_invalid")
    for row in document["catalogs"]:
        key = (row["catalog_id"], row["version"])
        if key in known and row["catalog_id"] in assignments:
            if not known[key].class_assignments:
                raise ValueError("catalog_migration_requires_named_classes")
            row["class_assignments"] = assignments[row["catalog_id"]]
    configured = RegistryConfig.model_validate(document)
    resolved = {
        (row.catalog_id, row.version): row
        for row in configured.catalogs
        if isinstance(row, ManagedCatalog)
    }
    versions = {}
    for row in document["catalogs"]:
        key = (row["catalog_id"], row["version"])
        if key not in known or definition_digest(known[key]) == definition_digest(resolved[key]):
            continue
        if not resolved[key].class_assignments:
            raise ValueError("catalog_migration_requires_one_class")
        row["version"] += 1
        versions[key] = row["version"]
    affected_definitions(
        document, versions, {row.class_key: row for row in configured.catalog_classes}
    )
    verified = RegistryConfig.model_validate(document)
    targets = {
        (row.catalog_id, row.version): row
        for row in verified.catalogs
        if isinstance(row, ManagedCatalog)
    }
    if outputs is not None:
        publish_class_outputs(document, outputs, before, targets)
    if load_contents is not None:
        result["catalog_contents"] = load_contents(set(versions))
    for content in result["catalog_contents"]:
        old_key = (content["catalog_id"], content["definition_version"])
        if old_key not in versions:
            continue
        target = targets[(content["catalog_id"], versions[old_key])]
        content["class_migration"] = {
            "operation": RECONFIGURE,
            "source_version": old_key[1],
            "source_revision": content["source_revision"],
            "source_definition_digest": content["definition_digest"],
            "source_entries_digest": checksum(entry_map(content["entries"])),
        }
        content.update(
            entries=[filter_classes(row, target) for row in content["entries"]],
            definition_version=target.version,
            definition_digest=definition_digest(target),
        )
    return seal(result)


def package_from_snapshot(
    state: Any, config: RegistryConfig | None, body: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    from core_contracts import __version__
    from core_contracts.extensions.catalog.catalog_contributions import export_contents
    from core_contracts.portable import package_format

    if body.get("expected_active_revision") != state.active_revision:
        raise ValueError("active_revision_conflict")
    if config is None:
        raise ValueError("export_no_configuration")
    document = storage_document(config)
    active = copy.deepcopy(document)
    refs = {f"contract:{row.contract_id}" for row in config.contracts} | {
        f"template:{row.type_id}" for row in config.definitions
    }
    editorial = [
        {key: row[key] for key in ("object", "category", "room")}
        for ref, row in state.tables["editorial_current"].items()
        if ref in refs
    ]

    def load_contents(keys: set[tuple[str, int]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = export_contents(config, state, {}, None, only=keys)
        if sum(len(row["entries"]) for row in rows) > 100_000:
            raise ValueError("export_entry_limit")
        return rows

    package = {
        "format": "wuschel.configuration",
        "format_version": package_format(config.config_schema_version),
        "scope": "configuration",
        "producer": {"name": "Wuschel", "version": __version__},
        "config": document,
        "editorial": editorial,
        "catalog_contents": [],
        "manifest": {"roots": []},
    }
    if "catalog_classes" in body:
        return (
            reconfigure_package(
                package,
                body["catalog_classes"],
                body.get("assignments", {}),
                body.get("outputs"),
                load_contents,
            ),
            active,
            [],
        )
    package["catalog_contents"] = load_contents(
        {
            (row.catalog_id, row.version)
            for row in config.catalogs
            if isinstance(row, ManagedCatalog)
            and row.catalog_id in ASSIGNMENTS
            and not row.class_assignments
            and row.lifecycle != "retired"
        }
    )
    blockers = migration_blockers(package)
    if blockers:
        return {}, active, blockers
    return migrate_package(package), active, []


def migration_proof(
    state: Any, content: dict[str, Any], incoming: RegistryConfig
) -> dict[str, Any]:
    proof = content["class_migration"]
    reconfigure = proof.get("operation") == RECONFIGURE
    if (
        set(proof)
        != {
            "operation",
            "source_version",
            "source_revision",
            "source_definition_digest",
            "source_entries_digest",
        }
        or type(proof["source_version"]) is not int
        or type(proof["source_revision"]) is not int
        or proof["source_revision"] < 0
        or proof["source_version"] < 1
        or proof["source_version"] + 1 != content["definition_version"]
    ):
        raise ValueError("catalog_migration_invalid")
    config = RegistryConfig.model_validate(
        state.tables["registry_revision"][str(state.active_revision)]["config"]
    )
    old = next(
        (
            catalog
            for catalog in config.catalogs
            if isinstance(catalog, ManagedCatalog)
            and catalog.catalog_id == content["catalog_id"]
            and catalog.version == proof["source_version"]
            and bool(catalog.class_assignments) == reconfigure
        ),
        None,
    )
    if old is None or (not reconfigure and old.catalog_id not in ASSIGNMENTS):
        raise ValueError("catalog_migration_conflict")
    new = next(
        (
            catalog
            for catalog in incoming.catalogs
            if isinstance(catalog, ManagedCatalog)
            and catalog.catalog_id == content["catalog_id"]
            and catalog.version == content["definition_version"]
        ),
        None,
    )
    if new is None or not new.class_assignments:
        raise ValueError("catalog_migration_invalid")
    upgraded = new
    if not reconfigure:
        upgraded = ManagedCatalog.model_validate(upgrade_catalog(old))
        upgraded.resolve_assignments({item.class_key: item for item in incoming.catalog_classes})
    current = head(state, old)
    if (
        head(state, new)["revision"]
        or current["revision"] != proof["source_revision"]
        or definition_digest(old) != proof["source_definition_digest"]
    ):
        raise ValueError("catalog_migration_conflict")
    original = entry_map(list(current["entries"].values()))
    if checksum(original) != proof["source_entries_digest"]:
        raise ValueError("catalog_migration_conflict")
    convert = filter_classes if reconfigure else convert_entry
    expected = {key: convert(row, upgraded) for key, row in original.items()}
    if (
        definition_digest(upgraded) != content["definition_digest"]
        or len(content["entries"]) != len(original)
        or entry_map(content["entries"]) != expected
    ):
        raise ValueError("catalog_migration_invalid")
    counts = Counter(row.get("classification") or "unassigned" for row in original.values())
    values = {}
    if not reconfigure:
        for raw in counts:
            if raw != "unassigned":
                values[raw] = convert_entry({"classification": raw}, upgraded).get(
                    "class_values", {}
                )
    removed = old.assigned_classes.keys() - new.assigned_classes.keys()
    removed_values = {
        name: sum(name in row.get("class_values", {}) for row in original.values())
        for name in sorted(removed)
    }
    return {
        "catalog_id": old.catalog_id,
        "entries": len(original),
        "before": dict(counts),
        "after": {},
        "class_values": values,
        "removed_values": removed_values,
        "variants": sum(bool(row.get("master_key")) for row in original.values()),
        "tombstones": sum(row["tombstone"] for row in original.values()),
        "sightings": sum(
            row["catalog"] == head_key(old) for row in state.tables["catalog_sighting"].values()
        ),
    }
