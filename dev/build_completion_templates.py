"""Portable definitions for issue 80; instance bindings remain in private packages."""

import json
from pathlib import Path


def schema(kind="number", unit=None, values=None):
    return {
        "type": kind,
        "required": True,
        "physical_state": False,
        **({"unit": unit} if unit else {}),
        **({"value_catalog": values} if values else {}),
    }


def inp(name):
    return {"op": "input", "name": name}


def literal(value, kind="text"):
    return {"op": "literal", "schema": schema(kind), "value": value}


def template(name, title, inputs, outputs, fields):
    return {
        "type_id": name,
        "type_version": 1,
        "language_version": 6,
        "display_name": title,
        "inputs": [{"name": n, "schema": s} for n, s in inputs.items()],
        "schema": {"fields": fields, "available_projection": list(fields), "fixture": False},
        "outputs": outputs,
    }


def definitions():
    result = []
    for kind, unit, label in [
        ("temperature", "°C", "Temperatur"),
        ("humidity", "%", "Luftfeuchte"),
    ]:
        field = schema(unit=unit)
        result.append(
            template(
                "climate.room_" + kind,
                "Raum" + label.lower(),
                {"primary": field},
                {"value": {"op": "fusion", "strategy": "first_healthy", "args": [inp("primary")]}},
                {"value": field},
            )
        )
        result.append(
            template(
                "climate.outdoor_" + kind,
                label + " außen",
                {"primary": field, "replacement": field},
                {
                    "value": {
                        "op": "fusion",
                        "strategy": "first_usable",
                        "args": [inp("primary"), inp("replacement")],
                    }
                },
                {"value": field},
            )
        )
    result.append(
        template(
            "climate.wind_speed",
            "Windgeschwindigkeit",
            {"value": schema(unit="m/s")},
            {"value": inp("value")},
            {"value": schema(unit="m/s")},
        )
    )
    result.append(
        template(
            "climate.toilet_event",
            "Toilettenereignis",
            {"value": schema("bool")},
            {"value": inp("value")},
            {"value": schema("bool")},
        )
    )
    result.append(
        template(
            "climate.shower_active",
            "Dusche aktiv",
            {"value": schema("bool")},
            {"value": inp("value")},
            {"value": schema("bool")},
        )
    )
    result.append(
        template(
            "climate.thermostat",
            "Thermostat – technische Gerätewerte",
            {"temperature": schema(unit="°C"), "valve_position": schema(unit="%")},
            {"temperature": inp("temperature"), "valve_position": inp("valve_position")},
            {"temperature": schema(unit="°C"), "valve_position": schema(unit="%")},
        )
    )
    formula_inputs = {
        "dew_point": ({"temperature": "°C", "relative_humidity": "%"}, "°C", "Taupunkt"),
        "absolute_humidity": (
            {"temperature": "°C", "relative_humidity": "%"},
            "g/m³",
            "Absolute Feuchte",
        ),
        "apparent_temperature": (
            {"temperature": "°C", "relative_humidity": "%", "wind_speed": "m/s"},
            "°C",
            "Gefühlte Temperatur",
        ),
        "expected_radiation": (
            {"sun_elevation": "°", "sun_azimuth": "°", "dni": "W/m²", "diffuse": "W/m²"},
            "W/m²",
            "Erwartete Strahlung",
        ),
    }
    for name, (quantities, unit, title) in formula_inputs.items():
        args = {n: inp(n) for n in quantities}
        item = template(
            "climate." + name,
            title,
            {n: schema(unit=u) for n, u in quantities.items()},
            {"value": {"op": "formula", "name": name, "version": 1, "args": args}},
            {"value": schema(unit=unit)},
        )
        if name == "expected_radiation":
            item["parameter_schema"] = {
                n: {"schema": schema(unit="°"), "minimum": 0, "maximum": limit, "label": title}
                for n, limit, title in [
                    ("window_azimuth", 360, "Fensterrichtung"),
                    ("window_tilt", 180, "Fensterneigung"),
                ]
            }
            args.update({n: {"op": "parameter", "name": n} for n in item["parameter_schema"]})
            item["output_origins"] = {"value": {"kind": "model", "provider": "window_geometry"}}
        result.append(item)
    simulation = {
        "op": "and",
        "args": [
            {
                "op": "compare",
                "comparison": "eq",
                "left": inp("location"),
                "right": literal("away"),
            },
            {
                "op": "compare",
                "comparison": "eq",
                "left": inp("brightness"),
                "right": literal("dark"),
            },
            {
                "op": "or",
                "args": [
                    {
                        "op": "compare",
                        "comparison": "eq",
                        "left": inp("phase"),
                        "right": literal(phase),
                    }
                    for phase in ["evening", "late_evening", "early_night"]
                ],
            },
        ],
    }
    # A proved home/parents value determines inactive even if the other inputs are missing.
    inactive = {
        "op": "or",
        "args": [
            {"op": "compare", "comparison": "eq", "left": inp("location"), "right": literal(place)}
            for place in ["home", "parents"]
        ],
    }

    def state(value):
        return {
            "op": "literal",
            "schema": schema("enum", values=["active", "inactive"]),
            "value": value,
        }

    result.append(
        template(
            "simulation.state",
            "Anwesenheitssimulation",
            {name: schema("text") for name in ["location", "brightness", "phase"]},
            {
                "value": {
                    "op": "first_match",
                    "cases": [
                        {"when": inactive, "then": state("inactive")},
                        {"when": simulation, "then": state("active")},
                        {"when": literal(True, "bool"), "then": state("inactive")},
                    ],
                }
            },
            {"value": schema("enum", values=["active", "inactive"])},
        )
    )
    result.append(
        template(
            "connectivity.binary",
            "Verbindung",
            {"connected": schema("bool")},
            {"value": inp("connected")},
            {"value": schema("bool")},
        )
    )
    return result


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    (root / "examples/completion-templates-v1.json").write_text(
        json.dumps(definitions(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
