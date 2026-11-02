"""Portable four-slot Stash definitions; no addresses or real media metadata."""

import json
from pathlib import Path

from dev.build_media_templates import (
    compare,
    conjunction,
    definition,
    disjunction,
    inp,
    literal,
    mapping,
    negate,
    not_applicable,
    schema,
    select,
)


def build():
    classes = ["normal", "private"]
    active = mapping(
        inp("state"),
        [
            (value, value in {"playing", "paused", "buffering"})
            for value in ("playing", "paused", "buffering", "idle", "off", "standby")
        ],
    )
    lookup = {
        "op": "managed_catalog_lookup",
        "profile": "stash_slot",
        "catalog_id": "media.stash",
        "definition_version": 2,
        "scope": literal("stash"),
        "media_type": literal("video"),
        "signal_type": literal("scene"),
        "key": inp("scene"),
        "schema": schema("enum", classes),
    }
    slot = definition(
        "media.stash_slot",
        {"scene": schema("text"), "state": schema("text")},
        {
            "value": select((negate(active), not_applicable("enum", classes)), (active, lookup)),
            "active": active,
        },
        {"value": schema("enum", classes), "active": schema()},
    )
    slot.update(display_name="Stash-Slot", language_version=7)
    slot["schema"]["available_projection"] = ["value", "active"]
    inputs = {
        name: field
        for index in range(1, 5)
        for name, field in (
            (f"slot_{index}_active", schema()),
            (f"slot_{index}_class", schema("enum", classes, required=False)),
        )
    }
    private = disjunction(
        *(
            conjunction(
                inp(f"slot_{index}_active"), compare(inp(f"slot_{index}_class"), literal("private"))
            )
            for index in range(1, 5)
        )
    )
    any_active = disjunction(*(inp(f"slot_{index}_active") for index in range(1, 5)))
    combined = definition(
        "media.stash_class",
        inputs,
        {
            "value": select(
                (private, literal("private", "enum", classes)),
                (negate(any_active), not_applicable("enum", classes)),
                (conjunction(any_active, negate(private)), literal("normal", "enum", classes)),
            )
        },
        {"value": schema("enum", classes)},
    )
    combined.update(type_version=3, display_name="Stash-Klasse aus vier Slots")
    return [slot, combined]


if __name__ == "__main__":
    target = Path(__file__).parents[1] / "examples/stash-templates-b22.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
