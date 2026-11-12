"""Explicit P8 private package assembly; no I/O or live activation."""

import copy
from typing import Any

from core_contracts.extensions.catalog.interface_upgrade import upgrade_package

from .portable import objects, package_checksum, payload_digest


def additions_for(
    config: dict[str, Any], addresses: dict[str, str], pc_watts: str, distance_meters: str
) -> dict[str, Any]:
    """Prepare neutral logical identities from private inputs and configured CMP sources."""
    result: dict[str, Any] = {"sources": [], "bindings": [], "contracts": [], "rooms": {}}
    dependency = next(
        row["dependency_id"]
        for row in config["dependencies"]
        if row["kind"] == "ha" and row["monitor"] == "internal"
    )
    sources = {row["source_id"]: row for row in config["sources"]}
    if (
        sources[pc_watts].get("value_schema", {}).get("unit") != "W"
        or sources[distance_meters].get("value_schema", {}).get("unit") != "m"
    ):
        raise ValueError("fact_package_source_unit_invalid")

    def source(
        identifier: str,
        entity: str,
        field_type: str,
        label: str,
        *,
        attribute: str | None = None,
        unit: str | None = None,
    ) -> str:
        existing = next(
            (
                binding["source_id"]
                for binding in config["bindings"]
                if binding["adapter"].get("ha_entity_id") == entity
                and binding["adapter"]["kind"] == ("ha_attribute" if attribute else "ha_state")
                and binding["adapter"].get("ha_attribute") == attribute
                and (sources[binding["source_id"]].get("value_schema") or {}).get("type")
                == field_type
                and (sources[binding["source_id"]].get("value_schema") or {}).get("unit") == unit
            ),
            None,
        )
        if existing:
            return str(existing)
        shape = {
            "type": field_type,
            "required": True,
            "physical_state": False,
            **({"unit": unit} if unit else {}),
        }
        result["sources"].append(
            {
                "source_id": identifier,
                "display_name": label,
                "source_type": {"bool": "boolean", "number": "numeric"}.get(field_type, "text"),
                "source_origin": "local",
                "dependency_id": dependency,
                "physical_source_key": identifier.replace("source.", "physical.", 1),
                "value_schema": shape,
                "freshness": {"mode": "event_stateful", "future_tolerance_s": 0},
            }
        )
        result["bindings"].append(
            {
                "binding_id": identifier.replace("source.", "binding.", 1),
                "source_id": identifier,
                "adapter": {
                    "kind": "ha_attribute" if attribute else "ha_state",
                    "ha_entity_id": entity,
                    **({"ha_attribute": attribute} if attribute else {}),
                },
                "transform": {"kind": "type_cast", "target": field_type},
            }
        )
        return identifier

    def contract(identifier: str, type_id: str, label: str, inputs: dict[str, str]) -> None:
        result["contracts"].append(
            {
                "contract_id": identifier,
                "type_id": type_id,
                "type_version": 1,
                "display_name": label,
                "enabled": True,
                "ha_projection": True,
                "parameters": {},
                "inputs": [{"name": name, "ref": ref} for name, ref in inputs.items()],
            }
        )

    battery = source(
        "source.tablet.battery", addresses["battery"], "number", "Tablet: Akkustand", unit="%"
    )
    charging = source("source.tablet.charging", addresses["charging"], "bool", "Tablet: Laden")
    call = source("source.communication.call_state", addresses["call"], "text", "Anrufzustand")
    contract("device.tablet.battery", "device.battery", "Tablet: Akkustand", {"level": battery})
    contract("device.tablet.charging", "device.charging", "Tablet: Laden", {"charging": charging})
    contract("device.pc.power_active", "device.pc_power", "PC: Leistungsfakt", {"watts": pc_watts})
    contract(
        "communication.call.active", "communication.call_active", "Anruf aktiv", {"state": call}
    )
    contract(
        "presence.phone.home_distance",
        "presence.distance",
        "Entfernung zur Wohnung",
        {"meters": distance_meters},
    )

    # CMP scope follows configured media/device contract dependencies. Discovery
    # alone never imports arbitrary unrelated players from the installation.
    contracts = {row["contract_id"]: row for row in config["contracts"]}
    needed: set[str] = set()

    def visit(ref: str, visited: set[str]) -> None:
        if ref in sources:
            needed.add(ref)
        for identifier, row in contracts.items():
            if identifier not in visited and ref.startswith(identifier + "."):
                for item in row["inputs"]:
                    visit(item["ref"], visited | {identifier})

    for row in contracts.values():
        if row["type_id"].startswith(("media.", "device.")):
            for item in row["inputs"]:
                visit(item["ref"], set())
    entities = sorted(
        {
            binding["adapter"]["ha_entity_id"]
            for binding in config["bindings"]
            if binding["source_id"] in needed
            and (binding["adapter"].get("ha_entity_id") or "").startswith("media_player.")
        }
    )
    for index, entity in enumerate(entities, 1):
        owner = next(
            binding["source_id"]
            for binding in config["bindings"]
            if binding["adapter"].get("ha_entity_id") == entity and binding["source_id"] in needed
        )
        label = sources[owner].get("display_name") or f"Mediengerät {index}"
        inputs = {
            "state": source(
                f"source.media.device_{index}.state", entity, "text", label + ": Zustand"
            ),
            "title": source(
                f"source.media.device_{index}.title",
                entity,
                "text",
                label + ": Titel",
                attribute="media_title",
            ),
            "artist": source(
                f"source.media.device_{index}.artist",
                entity,
                "text",
                label + ": Interpret",
                attribute="media_artist",
            ),
        }
        contract(
            f"media.device_{index}.state", "media.device_state", label + ": Medienzustand", inputs
        )
    for kind, class_type, alias in (
        ("device.pc_state", "media.pc_class", "pc"),
        ("device.console_state", "media.ps5_class", "console"),
    ):
        for row in contracts.values():
            if row["type_id"] != kind:
                continue
            title = next(
                (
                    item["ref"]
                    for candidate in contracts.values()
                    if candidate["type_id"] == class_type
                    for item in candidate["inputs"]
                    if item["name"] == "title"
                ),
                None,
            )
            contract(
                f"media.{alias}.state",
                "media.device_power_facts",
                (row.get("display_name") or alias) + ": Medienzustand",
                {
                    "active": row["contract_id"]
                    + (".active" if kind == "device.console_state" else ".value"),
                    **({"title": title} if title else {}),
                },
            )
    result["holiday_sources"] = list(
        {
            item["ref"]
            for row in contracts.values()
            if "holiday" in row["contract_id"] or "holiday" in row["type_id"]
            for item in row["inputs"]
            if item["ref"] in sources
            and any(
                binding["source_id"] == item["ref"] and binding["adapter"]["kind"] == "ha_calendar"
                for binding in config["bindings"]
            )
        }
    )
    result["remove_unsupported_origins"] = [
        binding["source_id"]
        for binding in config["bindings"]
        if binding["adapter"]["kind"] in {"connectivity", "scheduler"}
        and sources[binding["source_id"]].get("value_origin")
    ]
    result["media_players"] = len(entities)
    return result


def category(row: dict[str, Any]) -> str:
    if row["type_id"] in {
        "device.pc_state",
        "device.power_state",
        "device.active",
        "device.console_state",
        "device.appletv_state",
        "device.media_power",
    }:
        return "media"
    identity = row["contract_id"] + " " + row["type_id"]
    for prefixes, result in (
        (("opening.", "opening_zone."), "openings"),
        (("presence.",), "presence"),
        (("day.", "time.", "sun.", "calendar."), "time"),
        (("sleep.", "wake."), "sleep_wake"),
        (("media.",), "media"),
        (("climate.",), "climate"),
        (
            (
                "system.",
                "connectivity.",
                "bridge.",
                "health.",
                "ha.",
                "mqtt.",
                "traber.",
                "wuschel.",
            ),
            "system",
        ),
    ):
        if any(word.startswith(prefixes) for word in identity.split()):
            return result
    return "devices"


def assemble(
    exported: dict[str, Any],
    templates: list[dict[str, Any]],
    interface_templates: list[dict[str, Any]],
    additions: dict[str, Any],
) -> dict[str, Any]:
    """Addresses come exclusively from a caller-owned private additions file.

    The supplied source/contract/definition rows are ordinary portable authoring,
    checked by the normal import preview. Catalog snapshots and migration proofs
    are preserved byte-semantically by the P11 helper, never reconstructed here.
    """
    result = upgrade_package(exported)
    config = result["config"]
    for group, key in (
        ("sources", "source_id"),
        ("bindings", "binding_id"),
        ("contracts", "contract_id"),
    ):
        existing = {row[key] for row in config[group]}
        rows = copy.deepcopy(additions.get(group, []))
        if group == "sources" and config["config_schema_version"] == 4:
            for source in rows:
                if "freshness" in source:
                    if "freshness_selection" in source:
                        raise ValueError("fact_package_freshness_conflict")
                    source["freshness_selection"] = {"kind": "own", "rule": source.pop("freshness")}
        if len({row[key] for row in rows}) != len(rows) or any(
            row[key] in existing for row in rows
        ):
            raise ValueError("fact_package_identity_conflict")
        config[group].extend(rows)
    replacements = {row["type_id"]: row for row in interface_templates}
    used = {(row["type_id"], row["type_version"]): row for row in config["definitions"]}
    for row in templates:
        definition_key = row["type_id"], row["type_version"]
        if definition_key in used and used[definition_key] != row:
            raise ValueError("fact_package_definition_conflict")
        used[definition_key] = copy.deepcopy(row)
    tv_input = next(
        (
            item["ref"]
            for row in config["contracts"]
            if row["type_id"] == "media.tv_source"
            for item in row["inputs"]
            if item["name"] == "tv"
        ),
        None,
    )
    for row in config["contracts"]:
        if row["type_id"] == "media.tv_usage":
            latest = replacements["media.tv_usage"]
            if latest["type_version"] != 3 or tv_input is None:
                raise ValueError("fact_package_tv_input_missing")
            row["type_version"] = 3
            row["inputs"] = [
                item for item in row["inputs"] if item["name"] not in {"tv", "applicable"}
            ] + [{"name": "applicable", "ref": tv_input}]
            used[latest["type_id"], 3] = copy.deepcopy(latest)
        row["ha_projection"] = row["type_id"] != "sleep.flow"
    config["definitions"] = list(used.values())
    sources = {row["source_id"]: row for row in config["sources"]}
    for source_id in additions.get("holiday_sources", []):
        sources[source_id]["calendar_coverage_months"] = 12
    for source_id in additions.get("remove_unsupported_origins", []):
        binding = next(row for row in config["bindings"] if row["source_id"] == source_id)
        if binding["adapter"]["kind"] not in {"connectivity", "scheduler"}:
            raise ValueError("fact_package_origin_correction_invalid")
        sources[source_id].pop("value_origin", None)

    editorial = {
        row["object"]["id"]: row
        for row in result["editorial"]
        if row["object"]["kind"] == "contract"
    }
    rooms = additions.get("rooms", {})
    contracts = {row["contract_id"]: row for row in config["contracts"]}

    def room(identifier: str, visited: set[str]) -> dict[str, Any] | None:
        if identifier in visited:
            return None
        current = rooms.get(identifier) or editorial.get(identifier, {}).get("room")
        if current and current["mode"] != "none":
            return dict(copy.deepcopy(current))
        parents = [
            key
            for item in contracts[identifier]["inputs"]
            for key in contracts
            if item["ref"].startswith(key + ".")
        ]
        candidates = [r for key in parents if (r := room(key, visited | {identifier}))]
        candidates.extend(
            copy.deepcopy(additions["source_rooms"][item["ref"]])
            for item in contracts[identifier]["inputs"]
            if item["ref"] in additions.get("source_rooms", {})
        )
        if candidates and all(r == candidates[0] for r in candidates):
            return candidates[0]
        return None

    proposed = []
    for identifier, row in contracts.items():
        group = category(row)
        selected_room = room(identifier, set()) or {
            "mode": "manual",
            "area_id": None,
            "name": "System" if group == "system" else "Wohnung",
            "icon": "mdi:cog" if group == "system" else "mdi:home",
            "missing": False,
        }
        proposed.append(
            {
                "object": {"kind": "contract", "id": identifier},
                "category": group,
                "room": selected_room,
            }
        )
    result["editorial"] = [
        row for row in result["editorial"] if row["object"]["kind"] != "contract"
    ] + proposed
    version = result["format_version"]
    result["manifest"]["object_digests"] = {
        key: package_checksum(value, version) for key, value in objects(config).items()
    }
    result["manifest"]["config_digest"] = package_checksum(config, version)
    result["manifest"]["payload_digest"] = payload_digest(result)
    return result
