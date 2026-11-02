"""Local-only P7 import augmentation; private addresses are supplied by the caller."""

import copy
from typing import Any

from .apple_tv_catalog import AppleTitleConfig
from .portable import objects, package_checksum, payload_digest, read_package


def augment(
    package: dict[str, Any],
    templates: list[dict[str, Any]],
    catalog: dict[str, Any],
    player: str,
    settings: AppleTitleConfig,
) -> dict[str, Any]:
    if not player.startswith("media_player."):
        raise ValueError("apple_tv_player_required")
    result = copy.deepcopy(read_package(package))
    if result["scope"] != "configuration":
        raise ValueError("apple_tv_configuration_required")
    config = result["config"]
    contract_id = "media.appletv.title_class"
    source_ids = ["source.appletv.catalog_title", "source.appletv.catalog_state"]
    binding_ids = ["binding.appletv.catalog_title", "binding.appletv.catalog_state"]
    if (
        any(row["contract_id"] == contract_id for row in config["contracts"])
        or any(row["source_id"] in source_ids for row in config["sources"])
        or any(row["binding_id"] in binding_ids for row in config["bindings"])
        or any(row["catalog_id"] == catalog["catalog_id"] for row in config["catalogs"])
    ):
        raise ValueError("apple_tv_identity_conflict")
    existing = {(row["type_id"], row["type_version"]): row for row in config["definitions"]}
    for row in templates:
        key = row["type_id"], row["type_version"]
        if key in existing and existing[key] != row:
            raise ValueError("apple_tv_definition_conflict")
        existing[key] = copy.deepcopy(row)
    config["definitions"] = list(existing.values())
    config["catalogs"].append(copy.deepcopy(catalog))
    dependency = next(
        row["dependency_id"]
        for row in config["dependencies"]
        if row["kind"] == "ha" and row["monitor"] == "internal"
    )
    inputs = []
    for field, source_id, binding_id in zip(
        ("title", "state"), source_ids, binding_ids, strict=True
    ):
        existing_binding = next(
            (
                row
                for row in config["bindings"]
                if row["adapter"].get("ha_entity_id") == player
                and row["adapter"]["kind"] == ("ha_state" if field == "state" else "ha_attribute")
                and (field == "state" or row["adapter"].get("ha_attribute") == "media_title")
            ),
            None,
        )
        if existing_binding:
            existing_source = next(
                row
                for row in config["sources"]
                if row["source_id"] == existing_binding["source_id"]
            )
            if existing_source["value_schema"]["type"] != "text":
                raise ValueError("apple_tv_text_source_required")
            if field == "title":
                if existing_binding["adapter"].get("catalog_profile") not in {
                    None,
                    "apple_tv_title",
                }:
                    raise ValueError("apple_tv_profile_conflict")
                existing_binding["adapter"].update(
                    catalog_profile="apple_tv_title", title_catalog=settings.model_dump(mode="json")
                )
            inputs.append({"name": field, "ref": existing_source["source_id"]})
            continue
        source = {
            "source_id": source_id,
            "display_name": "Apple-TV-Titel" if field == "title" else "Apple-TV-Titel: Zustand",
            "source_type": "text",
            "source_origin": "local",
            "dependency_id": dependency,
            "physical_source_key": "physical.appletv.catalog_" + field,
            "value_schema": {"type": "text", "required": True, "physical_state": False},
        }
        freshness = {"mode": "event_stateful", "future_tolerance_s": 0}
        source.update(
            {"freshness_selection": {"kind": "own", "rule": freshness}}
            if config["config_schema_version"] == 4
            else {"freshness": freshness}
        )
        config["sources"].append(source)
        adapter = (
            {"kind": "ha_state", "ha_entity_id": player}
            if field == "state"
            else {
                "kind": "ha_attribute",
                "ha_entity_id": player,
                "ha_attribute": "media_title",
                "catalog_profile": "apple_tv_title",
                "title_catalog": settings.model_dump(mode="json"),
            }
        )
        config["bindings"].append(
            {
                "binding_id": binding_id,
                "source_id": source_id,
                "adapter": adapter,
                "transform": {"kind": "type_cast", "target": "text"},
            }
        )
        inputs.append({"name": field, "ref": source_id})
    config["contracts"].append(
        {
            "contract_id": contract_id,
            "type_id": "media.appletv_title_class",
            "type_version": 1,
            "display_name": "Apple-TV-Titelklasse",
            "enabled": True,
            "ha_projection": True,
            "parameters": {},
            "inputs": inputs,
        }
    )
    previous = next(
        (row for row in config["contracts"] if row["type_id"] == "media.appletv_class"), None
    )
    room = next(
        (
            row["room"]
            for row in result["editorial"]
            if previous and row["object"] == {"kind": "contract", "id": previous["contract_id"]}
        ),
        {
            "mode": "manual",
            "area_id": None,
            "name": "Wohnung",
            "icon": "mdi:home",
            "missing": False,
        },
    )
    result["editorial"].append(
        {
            "object": {"kind": "contract", "id": contract_id},
            "category": "media",
            "room": copy.deepcopy(room),
        }
    )
    version = result["format_version"]
    result["manifest"]["object_digests"] = {
        key: package_checksum(value, version) for key, value in objects(config).items()
    }
    result["manifest"]["config_digest"] = package_checksum(config, version)
    result["manifest"]["payload_digest"] = payload_digest(result)
    return result
