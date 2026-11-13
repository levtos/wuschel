"""Explicit b22 portable upgrade; no startup mutation, private data or network I/O."""

import copy
import json
import re
from collections import Counter
from typing import Any

from core_contracts.extensions.catalog.managed_catalog import definition_digest, identity
from core_contracts.model import checksum
from core_contracts.rules.catalog_models import EntryChange, ManagedCatalog

CLASS_CODES = {
    "heimkino": "home_cinema",
    "stumm": "mute",
    "leiser": "quieter",
    "privat": "private",
    "apple_tv": "appletv",
    "sonstige": "other",
}
VALUE_CODES = {
    **CLASS_CODES,
    "keines": "none",
    "werktag": "workday",
    "wochenende": "weekend",
    "frei": "day_off",
}
RENAMES = {
    "day.tomorrow.date": "day.date.tomorrow",
    "day.sleep_context.today": "day.context.today",
    "day.sleep_context.tomorrow": "day.context.tomorrow",
    "day.sleep_workday.tomorrow": "day.workday.tomorrow",
    "simulation_state": "presence.simulation",
}


def contract_id(value: str) -> str:
    if match := re.fullmatch(r"climate\.room\.([^.]+)\.([^.]+)", value):
        return f"climate.{match[1]}.{match[2]}"
    if match := re.fullmatch(r"climate\.thermostat\.([^.]+)", value):
        return f"climate.{match[1]}.thermostat"
    return RENAMES.get(value, value)


def translate_definition(value: Any, field: str = "") -> Any:
    """Translate declared codes, never labels, arbitrary source data or media keys."""
    code_fields = {
        "value",
        "value_catalog",
        "classes",
        "states",
        "source",
        "target",
        "initial",
        "default",
    }
    if isinstance(value, list):
        return [translate_definition(item, field) for item in value]
    if not isinstance(value, dict):
        return (
            VALUE_CODES.get(value, value)
            if isinstance(value, str) and field in code_fields
            else value
        )
    result = {}
    for key, item in value.items():
        if key in {"display_name", "description", "label", "message"}:
            result[key] = copy.deepcopy(item)
        elif key == "entries" and value.get("op") == "map":
            result[key] = {
                json.dumps(
                    VALUE_CODES.get(decoded, decoded)
                    if isinstance(decoded := json.loads(encoded), str)
                    else decoded
                ): translate_definition(mapped, "value")
                for encoded, mapped in item.items()
            }
        else:
            result[key] = translate_definition(item, key)
    if result.get("op") == "managed_catalog_lookup" and result.get("definition_version") == 1:
        result["definition_version"] = 2
    # Both device views expose the same 'other' code; this used to collapse
    # a known other TV device into the no-device state.
    if (
        result.get("op") == "map"
        and '"other"' in result.get("entries", {})
        and result["entries"]['"other"'] == "none"
    ):
        result["entries"]['"other"'] = "other"
        if "other" not in result["schema"]["value_catalog"]:
            result["schema"]["value_catalog"].append("other")
    return result


def upgrade_definition(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = translate_definition(row)
    if row["type_id"] == "media.lead_device" and result != row:
        for field in result["schema"]["fields"].values():
            if "value_catalog" in field and "other" not in field["value_catalog"]:
                field["value_catalog"].append("other")
    if result != row:
        result["type_version"] = row["type_version"] + 1
    return result


def upgrade_catalog(row: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(row)
    if row.get("kind") != "managed" or row["version"] != 1:
        return result
    result["version"] = 2
    result["unassigned_value"] = CLASS_CODES.get(row["unassigned_value"], row["unassigned_value"])
    shape = result["classification_schema"]
    shape["values"] = [CLASS_CODES.get(value, value) for value in shape["values"]]
    if "legend" in shape:
        shape["legend"] = {
            CLASS_CODES.get(key, key): value for key, value in shape["legend"].items()
        }
    return result


def translated_entry(row: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(row)
    value = row.get("classification")
    result["classification"] = CLASS_CODES.get(value, value) if isinstance(value, str) else value
    return result


def entry_map(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        identity(
            row["scope"], row["media_type"], row["signal_type"], row["key"]
        ): EntryChange.model_validate(
            {key: value for key, value in row.items() if key in EntryChange.model_fields}
        ).model_dump(mode="json")
        for row in rows
    }


def upgrade_package(package: dict[str, Any]) -> dict[str, Any]:
    """Build P8's private import file from an exported complete configuration.

    Names/addresses remain only in the caller's private file. Published examples
    and tests supply synthetic data. The app later verifies every source head.
    """
    from core_contracts.portable import objects, package_checksum, payload_digest, read_package

    result = copy.deepcopy(read_package(package))
    if result.get("legacy") or result["scope"] != "configuration":
        raise ValueError("interface_upgrade_invalid")
    config = result["config"]
    rows = config["contracts"]
    remaps = {row["contract_id"]: contract_id(row["contract_id"]) for row in rows}
    if any(row["contract_id"] == "day.context" for row in rows):
        if not any(
            remaps[row["contract_id"]] == "day.context.today" and row["type_id"] == "day.context_at"
            for row in rows
        ):
            raise ValueError("interface_upgrade_context_missing")
        remaps["day.context"] = "day.context.today"
        rows[:] = [row for row in rows if row["contract_id"] != "day.context"]
    renamed = [remaps[row["contract_id"]] for row in rows]
    if len(renamed) != len(set(renamed)):
        raise ValueError("interface_upgrade_identity_conflict")
    if any(
        row["type_id"] == "day.context"
        or (
            remaps[row["contract_id"]] in {"day.context.today", "day.context.tomorrow"}
            and row["type_id"] != "day.context_at"
        )
        for row in rows
    ):
        raise ValueError("interface_upgrade_context_missing")

    def refs(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {name: refs(value, name) for name, value in item.items()}
        if isinstance(item, list):
            return [refs(value, key) for value in item]
        if isinstance(item, str) and key in {"ref", "trigger_ref", "contract_id"}:
            for old in sorted(remaps, key=len, reverse=True):
                if item == old or item.startswith(old + "."):
                    return remaps[old] + item[len(old) :]
        return item

    replacements = {}
    for row in config.get("definitions", []):
        if row["type_id"] == "day.context":
            continue
        upgraded = upgrade_definition(row)
        replacements[row["type_id"], row["type_version"]] = upgraded
    config["definitions"] = list(
        {(row["type_id"], row["type_version"]): row for row in replacements.values()}.values()
    )
    for row in rows:
        replacement = replacements.get((row["type_id"], row["type_version"]))
        if replacement:
            row["type_version"] = replacement["type_version"]
        # Public projections are enabled after renaming, in the same confirmed
        # activation. The sole internal sleep producer is never projected.
        row["ha_projection"] = row["type_id"] != "sleep.flow"
    config = refs(config)
    result["config"] = config
    original_catalogs = {(row["catalog_id"], row["version"]): row for row in config["catalogs"]}
    config["catalogs"] = [upgrade_catalog(row) for row in config["catalogs"]]
    for content in result["catalog_contents"]:
        old = original_catalogs[content["catalog_id"], content["definition_version"]]
        new = upgrade_catalog(old)
        if new == old:
            continue
        content["class_migration"] = {
            "source_version": old["version"],
            "source_revision": content["source_revision"],
            "source_definition_digest": content["definition_digest"],
            "source_entries_digest": checksum(entry_map(content["entries"])),
        }
        content["entries"] = [translated_entry(row) for row in content["entries"]]
        content["definition_version"] = new["version"]
        content["definition_digest"] = definition_digest(ManagedCatalog.model_validate(new))
    for row in result["editorial"]:
        if row["object"]["kind"] == "contract":
            row["object"]["id"] = remaps.get(row["object"]["id"], row["object"]["id"])
    result["editorial"] = list(
        {checksum(row["object"]): row for row in result["editorial"]}.values()
    )
    version = result["format_version"]
    result["manifest"].update(
        object_digests={
            key: package_checksum(value, version) for key, value in objects(config).items()
        },
        config_digest=package_checksum(config, version),
    )
    result["manifest"]["payload_digest"] = payload_digest(result)
    return result


def migration_preview(state: Any, contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verify exact preservation and OCC before preview and again before activation."""
    findings = []
    for content in contents:
        proof = content.get("class_migration")
        if proof is None:
            continue
        if (
            not isinstance(proof, dict)
            or set(proof)
            != {
                "source_version",
                "source_revision",
                "source_definition_digest",
                "source_entries_digest",
            }
            or proof["source_version"] != 1
            or type(proof["source_version"]) is not int
            or type(proof["source_revision"]) is not int
            or proof["source_revision"] < 0
            or content["definition_version"] != 2
        ):
            raise ValueError("interface_upgrade_invalid")
        source_key = checksum([content["catalog_id"], proof["source_version"]])
        source = state.tables["catalog_head"].get(source_key, {})
        config = (
            state.tables["registry_revision"].get(str(state.active_revision), {}).get("config", {})
        )
        original_definition = next(
            (
                row
                for row in config.get("catalogs", [])
                if row["catalog_id"] == content["catalog_id"] and row["version"] == 1
            ),
            None,
        )
        if (
            original_definition is None
            or definition_digest(
                ManagedCatalog.model_validate(upgrade_catalog(original_definition))
            )
            != content["definition_digest"]
        ):
            raise ValueError("interface_upgrade_invalid")
        if (
            state.tables["catalog_head"]
            .get(checksum([content["catalog_id"], 2]), {})
            .get("revision", 0)
        ):
            raise ValueError("interface_upgrade_conflict")
        entries = source.get("entries", {})
        original = entry_map(list(entries.values()))
        if (
            source.get("revision", 0) != proof["source_revision"]
            or (source and source["definition_digest"] != proof["source_definition_digest"])
            or checksum(original) != proof["source_entries_digest"]
        ):
            raise ValueError("interface_upgrade_conflict")
        expected = {key: translated_entry(row) for key, row in original.items()}
        if entry_map(content["entries"]) != expected or len(content["entries"]) != len(expected):
            raise ValueError("interface_upgrade_invalid")
        before = Counter(row["classification"] or "unassigned" for row in original.values())
        after = Counter(row["classification"] or "unassigned" for row in expected.values())
        findings.append(
            {
                "catalog_id": content["catalog_id"],
                "entries": len(expected),
                "before": dict(before),
                "after": dict(after),
            }
        )
    return findings


def migrate_runtime(state: Any, contents: list[dict[str, Any]], now: Any) -> None:
    """Part of portable.apply's existing transaction; never called on startup."""
    from core_contracts.extensions.catalog.catalog_mapping import current_mapping
    from core_contracts.extensions.catalog.catalog_playback import PREFIX

    upgrades = {row["catalog_id"]: row for row in contents if row.get("class_migration")}
    for catalog_id, content in upgrades.items():
        old_key, new_key = checksum([catalog_id, 1]), checksum([catalog_id, 2])
        for key, row in list(state.tables["catalog_sighting"].items()):
            if row["catalog"] != old_key:
                continue
            target = new_key + ":" + row["identity"]
            if target in state.tables["catalog_sighting"]:
                raise ValueError("interface_upgrade_conflict")
            state.tables["catalog_sighting"][target] = {**row, "catalog": new_key}
            del state.tables["catalog_sighting"][key]
            if row.get("source"):
                suffix = [row["scope"], row["media_type"], row["signal_type"], row["source"]]
                old_stream, new_stream = (
                    PREFIX + checksum([catalog, *suffix]) for catalog in (old_key, new_key)
                )
                tracker = state.tables["service_state"].pop(old_stream, None)
                if tracker:
                    tracker = copy.deepcopy(tracker)
                    if pending := tracker.pop("pending", None):
                        tracker.update(
                            last=pending["identity"],
                            last_raw=pending["key"],
                            last_session=pending["session"],
                        )
                    state.tables["service_state"][new_stream] = tracker
    saved = current_mapping(state)
    mapping = copy.deepcopy(saved["mapping"])
    for target in mapping["mappings"].values():
        if target["catalog_id"] in upgrades and target["definition_version"] == 1:
            target["definition_version"] = 2
    if mapping != saved["mapping"]:
        version = saved["version"] + 1
        state.tables["title_sync_mapping"][str(version)] = {
            "version": version,
            "mapping": mapping,
            "at": now.isoformat(),
        }
