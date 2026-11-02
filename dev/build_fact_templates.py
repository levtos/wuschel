"""P8 fact definitions, with neutral identifiers and no installation addresses."""

import json
from pathlib import Path

from dev.build_media_templates import definition, inp, mapping, schema


def build():
    def number(unit):
        return {**schema("number"), "unit": unit}

    def watts(value):
        return {"op": "literal", "value": value, "schema": number("W")}

    rows = [
        definition(
            "device.battery",
            {"level": number("%")},
            {"value": inp("level")},
            {"value": number("%")},
        ),
        definition(
            "device.charging",
            {"charging": schema()},
            {"value": inp("charging")},
            {"value": schema()},
        ),
        definition(
            "presence.distance",
            {"meters": number("m")},
            {"value": inp("meters")},
            {"value": number("m")},
        ),
        definition(
            "communication.call_active",
            {"state": schema("text")},
            {
                "value": mapping(
                    inp("state"),
                    [("idle", False), ("dialing", True), ("talking", True), ("ringing", True)],
                )
            },
            {"value": schema()},
        ),
        definition(
            "device.pc_power",
            {"watts": number("W")},
            {
                "value": {
                    "op": "bucket",
                    "id": "power",
                    "arg": inp("watts"),
                    "classes": [False, True],
                    "initial": {"unknown": True},
                    "schema": schema(),
                    "boundaries": [
                        {
                            "at": watts(20),
                            "hysteresis": {
                                "lower": watts(6),
                                "upper": watts(20),
                            },
                        }
                    ],
                },
                "watts": inp("watts"),
            },
            {"value": schema(), "watts": number("W")},
        ),
        definition(
            "media.device_state",
            {
                "state": schema("text"),
                "title": schema("text", required=False),
                "artist": schema("text", required=False),
            },
            {
                "value": mapping(
                    inp("state"),
                    [
                        (v, v)
                        for v in ("off", "idle", "standby", "playing", "paused", "buffering", "on")
                    ],
                    "enum",
                    ["off", "idle", "standby", "playing", "paused", "buffering", "on"],
                ),
                "title": inp("title"),
                "artist": inp("artist"),
            },
            {
                "value": schema(
                    "enum", ["off", "idle", "standby", "playing", "paused", "buffering", "on"]
                ),
                "title": schema("text", required=False),
                "artist": schema("text", required=False),
            },
        ),
    ]
    names = ["Akkustand", "Laden", "Entfernung", "Anruf aktiv", "PC-Leistung", "Medienzustand"]
    for row, name in zip(rows, names, strict=True):
        row["display_name"] = name
        row["schema"]["available_projection"] = ["value"]
    rows.append(
        definition(
            "media.device_power_facts",
            {
                "active": schema(),
                "title": schema("text", required=False),
                "artist": schema("text", required=False),
            },
            {
                "value": mapping(
                    inp("active"), [(False, "off"), (True, "on")], "enum", ["off", "on"]
                ),
                "title": inp("title"),
                "artist": inp("artist"),
            },
            {
                "value": schema("enum", ["off", "on"]),
                "title": schema("text", required=False),
                "artist": schema("text", required=False),
            },
        )
    )
    rows[-1]["display_name"] = "Mediengerät mit Betriebszustand"
    rows[-1]["schema"]["available_projection"] = ["value"]
    return rows


if __name__ == "__main__":
    target = Path(__file__).parents[1] / "examples/fact-templates-b22.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
