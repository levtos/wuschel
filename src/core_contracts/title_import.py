"""Read-only v3 planning. No DB access, application, HA calls or domain-class inference."""

from collections import Counter, defaultdict
from typing import Any

from core_contracts.extensions.catalog.catalog_mapping import MAX_ENTRIES as MAX_ENTRIES

from .model import checksum
from .text_rules import normalize_key

MAX_BYTES = 50 * 1024 * 1024


def source_identity(row: dict[str, Any], key: str | None = None) -> tuple[str, ...]:
    return (
        row["scope"],
        row["media_type"],
        row["signal_type"],
        row["normalized_key"] if key is None else key,
    )


def target_identity(row: dict[str, Any]) -> tuple[str, ...]:
    return (row["scope"], row["media_type"], row["signal_type"], normalize_key(row["key"]))


def row_errors(row: Any) -> list[str]:
    if not isinstance(row, dict):
        return ["record_object_required"]
    errors = []
    for key in ("scope", "key", "normalized_key"):
        if not isinstance(row.get(key), str) or not 1 <= len(row[key]) <= 8192:
            errors.append(key + "_invalid")
    if row.get("media_type") not in ("music", "video", "game"):
        errors.append("media_type_invalid")
    if row.get("signal_type") not in ("title", "app"):
        errors.append("signal_type_invalid")
    if type(row.get("enum")) is not int or not 0 <= row["enum"] <= 9:
        errors.append("enum_invalid")
    if row.get("parent_key") is not None and not isinstance(row["parent_key"], str):
        errors.append("parent_key_invalid")
    contexts = row.get("contexts", [])
    if not isinstance(contexts, list) or len(contexts) > 64:
        errors.append("contexts_invalid")
    else:
        seen = set()
        for item in contexts:
            if not isinstance(item, dict):
                errors.append("context_invalid")
                continue
            if not isinstance(item.get("context"), str) or not isinstance(
                item.get("source_app", ""), str
            ):
                errors.append("context_invalid")
                continue
            identity = (item["context"], item.get("source_app", ""))
            if identity in seen:
                errors.append("duplicate_context")
            seen.add(identity)
            override = item.get("enum_override")
            if override is not None and (type(override) is not int or not 0 <= override <= 9):
                errors.append("enum_override_invalid")
    if any(key in row for key in ("image", "artwork", "cover_url", "entity_picture")):
        errors.append("image_data_not_allowed")
    return errors


def plan_title_import(document: Any, source_sha256: str) -> dict[str, Any]:
    """Return a private review plan; every input line has exactly one outcome."""
    if (
        not isinstance(document, dict)
        or set(document) != {"version", "entries"}
        or document.get("version") != 3
    ):
        raise ValueError("title_export_version_3_required")
    rows = document["entries"]
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise ValueError("title_export_limit")
    valid: dict[tuple[str, ...], tuple[int, dict[str, Any]]] = {}
    targets: dict[tuple[str, ...], list[int]] = defaultdict(list)
    rejected: dict[int, list[str]] = {}
    for index, row in enumerate(rows):
        errors = row_errors(row)
        if errors:
            rejected[index] = errors
            continue
        identity = source_identity(row)
        if identity in valid:
            rejected[index] = ["duplicate_source_identity"]
        else:
            valid[identity] = (index, row)
            targets[target_identity(row)].append(index)
    collisions: list[dict[str, Any]] = [
        {
            "identity": list(identity),
            "rows": [index + 1 for index in indices],
            "keys": [rows[i]["key"] for i in indices],
        }
        for identity, indices in targets.items()
        if len(indices) > 1
    ]
    colliding = {index - 1 for group in collisions for index in group["rows"]}
    variant_decisions = []
    hidden = []
    apps = []
    overrides = []
    normalization_changes = []
    classifications: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    candidates = []
    for index, row in enumerate(rows):
        if index in rejected:
            outcomes["rejected"] += 1
            continue
        classifications[row["media_type"]][str(row["enum"])] += 1
        parent = None
        if row.get("parent_key"):
            found = valid.get(source_identity(row, row["parent_key"]))
            if found is None:
                rejected[index] = ["parent_missing"]
            else:
                parent = found[1]
                if parent.get("parent_key") or source_identity(parent) == source_identity(row):
                    rejected[index] = ["parent_depth_or_cycle"]
        if index in rejected:
            outcomes["rejected"] += 1
            continue
        differing = parent is not None and parent["enum"] != row["enum"]
        if differing:
            assert parent is not None
            variant_decisions.append(
                {
                    "row": index + 1,
                    "key": row["key"],
                    "identity": list(source_identity(row)),
                    "variant_class": row["enum"],
                    "master": parent["key"],
                    "master_class": parent["enum"],
                    "decision": None,
                    "choices": ["Variante übernimmt Masterklasse", "wird selbständiger Eintrag"],
                }
            )
        if row.get("hidden_at"):
            hidden.append(
                {
                    "row": index + 1,
                    "key": row["key"],
                    "class": row["enum"],
                    "proposal": "Ausblendung als nichtsemantischen Pflegehinweis erhalten; keine Klassifikation ändern oder Daten löschen.",
                    "decision": None,
                }
            )
        if row["signal_type"] == "app":
            apps.append(
                {
                    "row": index + 1,
                    "key": row["key"],
                    "media_type": row["media_type"],
                    "proposal": "Eigene Signalart app beibehalten.",
                }
            )
        for context in row.get("contexts", []):
            if context.get("enum_override") is not None:
                overrides.append(
                    {"row": index + 1, "key": row["key"], "context": context, "decision": None}
                )
        normalized = normalize_key(row["key"])
        if not normalized:
            rejected[index] = ["empty_target_key"]
            outcomes["rejected"] += 1
            continue
        if normalized != row["normalized_key"]:
            normalization_changes.append(
                {
                    "row": index + 1,
                    "key": row["key"],
                    "source": row["normalized_key"],
                    "target": normalized,
                }
            )
        outcome = (
            "normalization_collision"
            if index in colliding
            else "variant_decision_required"
            if differing
            else "hidden_decision_required"
            if row.get("hidden_at")
            else "class_mapping_required"
        )
        outcomes[outcome] += 1
        candidates.append(
            {
                "row": index + 1,
                "identity": list(target_identity(row)),
                "display_name": row["key"],
                "source_class": row["enum"],
                "parent_identity": list(target_identity(parent)) if parent else None,
                "contexts": row.get("contexts", []),
                "source_telemetry": row.get("telemetry"),
                "hidden_at": row.get("hidden_at"),
                "reviewed_at": row.get("reviewed_at"),
                "outcome": outcome,
            }
        )
    summary = {
        "input_rows": len(rows),
        "outcomes": dict(sorted(outcomes.items())),
        "classified_rows": sum(sum(count.values()) for count in classifications.values()),
        "classes_by_media": {
            key: dict(sorted(value.items())) for key, value in sorted(classifications.items())
        },
        "variants": sum(bool(row.get("parent_key")) for row in rows if isinstance(row, dict)),
        "variant_class_conflicts": len(variant_decisions),
        "hidden": len(hidden),
        "app_entries": len(apps),
        "normalization_collisions": len(collisions),
        "normalization_changes": len(normalization_changes),
        "context_overrides": len(overrides),
        "rejected": len(rejected),
        "contexts": sum(
            len(row.get("contexts", []))
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("contexts", []), list)
        ),
        "source_apps": len(
            {
                c.get("source_app", "")
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("contexts", []), list)
                for c in row.get("contexts", [])
                if isinstance(c, dict) and isinstance(c.get("source_app", ""), str)
            }
        ),
    }
    assert sum(outcomes.values()) == len(rows)
    return {
        "format": "wuschel.title-import-plan",
        "version": 1,
        "applied": False,
        "source_sha256": source_sha256,
        "source_format": 3,
        "import_id": checksum(
            {"source": "levtos/Title_classifier", "sha256": source_sha256, "planner": 1}
        ),
        "summary": summary,
        "class_mapping": {str(n): {"meaning": None, "target_class": None} for n in range(4)},
        "variant_decisions": variant_decisions,
        "hidden_decisions": hidden,
        "app_entries": apps,
        "normalization_collisions": collisions,
        "normalization_changes": normalization_changes,
        "context_override_decisions": overrides,
        "rejected_rows": [
            {"row": index + 1, "reasons": reasons} for index, reasons in sorted(rejected.items())
        ],
        "candidates": candidates,
        "next_gate": "Benni bestätigt Klassenbedeutung und Entscheidungen. In B frischer Export über title_classifier/v3/export_entries und eigenes Import-Go.",
    }
