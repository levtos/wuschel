"""Explicit private import augmentation for four Stash slots; no live writes."""

import copy
from typing import Any

from .portable import objects, package_checksum, payload_digest, read_package


def augment(
    package: dict[str, Any], templates: list[dict[str, Any]], players: list[str]
) -> dict[str, Any]:
    if (
        len(players) != 4
        or len(set(players)) != 4
        or any(not player.startswith("media_player.") for player in players)
    ):
        raise ValueError("stash_four_players_required")
    result = copy.deepcopy(read_package(package))
    config = result["config"]
    if result["scope"] != "configuration" or not any(
        row["catalog_id"] == "media.stash" and row["version"] == 2 for row in config["catalogs"]
    ):
        raise ValueError("stash_catalog_version_required")
    aggregate = next(row for row in config["contracts"] if row["type_id"] == "media.stash_class")
    definitions = {(row["type_id"], row["type_version"]): row for row in config["definitions"]}
    for row in templates:
        key = row["type_id"], row["type_version"]
        if key in definitions and definitions[key] != row:
            raise ValueError("stash_definition_conflict")
        definitions[key] = copy.deepcopy(row)
    config["definitions"] = list(definitions.values())
    dependency = next(
        row["dependency_id"]
        for row in config["dependencies"]
        if row["kind"] == "ha" and row["monitor"] == "internal"
    )
    ids = (
        {row["source_id"] for row in config["sources"]}
        | {row["binding_id"] for row in config["bindings"]}
        | {row["contract_id"] for row in config["contracts"]}
    )
    inputs = []
    room = next(
        (
            row["room"]
            for row in result["editorial"]
            if row["object"] == {"kind": "contract", "id": aggregate["contract_id"]}
        ),
        {
            "mode": "manual",
            "area_id": None,
            "name": "Wohnung",
            "icon": "mdi:home",
            "missing": False,
        },
    )
    for index, player in enumerate(players, 1):
        contract_id = f"media.stash.slot_{index}"
        if contract_id in ids:
            raise ValueError("stash_identity_conflict")
        refs = []
        for field in ("scene", "state"):
            source_id, binding_id = (
                f"source.stash.slot_{index}.{field}",
                f"binding.stash.slot_{index}.{field}",
            )
            if source_id in ids or binding_id in ids:
                raise ValueError("stash_identity_conflict")
            source = {
                "source_id": source_id,
                "display_name": f"Stash-Slot {index}: "
                + ("Szene" if field == "scene" else "Zustand"),
                "source_type": "text",
                "source_origin": "local",
                "dependency_id": dependency,
                "physical_source_key": f"physical.stash.slot_{index}.{field}",
                "value_schema": {"type": "text", "required": True, "physical_state": False},
            }
            rule = {"mode": "event_stateful", "future_tolerance_s": 0}
            source.update(
                {"freshness_selection": {"kind": "own", "rule": rule}}
                if config["config_schema_version"] == 4
                else {"freshness": rule}
            )
            config["sources"].append(source)
            adapter = (
                {"kind": "ha_state", "ha_entity_id": player}
                if field == "state"
                else {
                    "kind": "ha_attribute",
                    "ha_entity_id": player,
                    "ha_attribute": "scene_id",
                    "catalog_profile": "stash_slot",
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
            refs.append({"name": field, "ref": source_id})
        config["contracts"].append(
            {
                "contract_id": contract_id,
                "type_id": "media.stash_slot",
                "type_version": 1,
                "enabled": True,
                "ha_projection": True,
                "display_name": f"Stash-Slot {index}",
                "parameters": {},
                "inputs": refs,
            }
        )
        inputs.extend(
            [
                {"name": f"slot_{index}_active", "ref": contract_id + ".active"},
                {"name": f"slot_{index}_class", "ref": contract_id + ".value"},
            ]
        )
        result["editorial"].append(
            {
                "object": {"kind": "contract", "id": contract_id},
                "category": "media",
                "room": copy.deepcopy(room),
            }
        )
    aggregate.update(type_version=3, inputs=inputs)
    version = result["format_version"]
    result["manifest"]["object_digests"] = {
        key: package_checksum(value, version) for key, value in objects(config).items()
    }
    result["manifest"]["config_digest"] = package_checksum(config, version)
    result["manifest"]["payload_digest"] = payload_digest(result)
    return result
