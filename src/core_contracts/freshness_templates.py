"""Revision-owned freshness selections; runtime resolution has one authority."""

import copy
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .evidence import Freshness
from .model import Model

SensorKind = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
LEGACY_SENSOR_KINDS = frozenset({"contact", "temperature", "motion", "counter"})
DEFAULT_SENSOR_KINDS = {
    "contact": "Kontakt",
    "motion": "Bewegung",
    "temperature": "Temperatur",
    "humidity": "Luftfeuchtigkeit",
    "power": "Leistung",
    "counter": "Zähler",
    "illuminance": "Helligkeit",
    "wind": "Wind",
    "radiation": "Strahlung",
    "state_text": "Zustand oder Text",
    "other": "Sonstiges",
}


class SensorType(Model):
    sensor_kind: SensorKind
    display_name: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def trimmed(self) -> SensorType:
        if not self.display_name.strip() or self.display_name != self.display_name.strip():
            raise ValueError("sensor_kind_label")
        return self


class TemplateRule(Model):
    mode: Literal["report_heartbeat", "liveness_source", "periodic_ttl", "event_stateful"]
    future_tolerance_s: float = Field(ge=0, strict=True, allow_inf_nan=False)
    interval_s: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def complete(self) -> TemplateRule:
        if self.mode != "event_stateful" and self.interval_s is None:
            raise ValueError("freshness interval required")
        return self


class FreshnessTemplate(Model):
    template_id: str = Field(pattern=r"^freshness\.[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=256)
    sensor_kind: SensorKind
    rule: TemplateRule


class OwnFreshness(Model):
    kind: Literal["own"]
    rule: Freshness
    comparison_template_id: str | None = None


class SharedFreshness(Model):
    kind: Literal["shared"]
    template_id: str
    liveness_source: str | None = None


FreshnessSelection = Annotated[OwnFreshness | SharedFreshness, Field(discriminator="kind")]


def upgrade_document(document: dict[str, Any], *, version: int = 4) -> dict[str, Any]:
    """Upgrade an editable copy, preserving the exact effective freshness rules."""
    result = copy.deepcopy(document)
    previous = result.get("config_schema_version", 1)
    if previous < 4:
        result.update(config_schema_version=4, fingerprint_version=4, freshness_templates=[])
        for source in result.get("sources", []):
            source["freshness_selection"] = {"kind": "own", "rule": source.pop("freshness")}
    if previous < 5 and version == 5:
        result["config_schema_version"] = 5
        result["sensor_kinds"] = [
            {"sensor_kind": key, "display_name": name} for key, name in DEFAULT_SENSOR_KINDS.items()
        ]
    return result


DEVICE_CLASSES = {
    "door": "contact",
    "garage_door": "contact",
    "opening": "contact",
    "window": "contact",
    "motion": "motion",
    "temperature": "temperature",
    "humidity": "humidity",
    "power": "power",
    "energy": "counter",
    "water": "counter",
    "gas": "counter",
    "duration": "counter",
    "illuminance": "illuminance",
    "wind_speed": "wind",
    "irradiance": "radiation",
}
UNITS = {
    "°c": "temperature",
    "°f": "temperature",
    "w": "power",
    "kw": "power",
    "mw": "power",
    "wh": "counter",
    "kwh": "counter",
    "lx": "illuminance",
    "m/s": "wind",
    "km/h": "wind",
    "w/m²": "radiation",
    "w/m2": "radiation",
}


def propose_sensor_kinds(config: Any, entities: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = {row["entity_id"]: row for row in entities}
    bindings = {
        binding.source_id: metadata.get(binding.adapter.ha_entity_id, {})
        for binding in config.bindings
        if binding.adapter.kind == "ha_state"
    }
    kinds = {item.sensor_kind for item in config.sensor_kinds}
    proposals = {}
    for source in config.sources:
        if source.origin == "manual" or source.sensor_kind_origin in {"declared", "confirmed"}:
            continue
        entity = bindings.get(source.source_id, {})
        device_class = entity.get("device_class")
        schema = source.value_schema
        units = (entity.get("unit"), schema.unit if schema else None)
        unit_kinds = {
            UNITS[u.casefold().strip()]
            for u in units
            if isinstance(u, str) and u.casefold().strip() in UNITS
        }
        kind = DEVICE_CLASSES.get(device_class) if isinstance(device_class, str) else None
        basis = "device_class"
        if kind is None and len(unit_kinds) == 1:
            kind = next(iter(unit_kinds))
            basis = "unit"
        if (
            kind is None
            and not any(units)
            and (source.source_type == "text" or (schema and schema.type in {"text", "enum"}))
        ):
            kind = "state_text"
            basis = "value_type"
        proposals[source.source_id] = (
            {"sensor_kind": kind, "basis": basis}
            if kind in kinds
            else {"sensor_kind": None, "basis": "uncertain"}
        )
    return proposals


def identical_own_rules(config: Any) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, float | None, float], list[Any]] = {}
    for source in config.sources:
        selection = source.freshness_selection
        rule = source.freshness
        if (
            source.origin == "manual"
            or not source.sensor_kind
            or not source.sensor_kind_origin
            or not selection
            or selection.kind != "own"
            or (rule.liveness_source is not None and rule.mode != "liveness_source")
        ):
            continue
        key = (source.sensor_kind, rule.mode, rule.interval_s, rule.future_tolerance_s)
        groups.setdefault(key, []).append(source)
    result = []
    for (kind, *_), sources in groups.items():
        if len(sources) < 2:
            continue
        rule = TemplateRule.model_validate(
            sources[0].freshness.model_dump(exclude={"liveness_source"})
        )
        selections = {}
        for source in sources:
            reference = source.freshness.liveness_source
            restored = Freshness.model_validate({**rule.model_dump(), "liveness_source": reference})
            if restored != source.freshness:
                raise ValueError("freshness_template_not_equivalent")
            selections[source.source_id] = (
                {"liveness_source": reference} if reference is not None else {}
            )
        result.append(
            {
                "sensor_kind": kind,
                "rule": rule.model_dump(mode="json"),
                "sources": selections,
            }
        )
    return result


def effective_freshness(config: Any) -> dict[str, Any]:
    templates = {template.template_id: template for template in config.freshness_templates}
    sources = {}
    usage = {}
    for source in config.sources:
        selection = source.freshness_selection
        template_id = (
            (
                selection.template_id
                if selection.kind == "shared"
                else selection.comparison_template_id
            )
            if selection
            else None
        )
        template = templates.get(template_id)
        comparison = template.rule.model_dump() if template else None
        rule = source.freshness.model_dump(mode="json")
        comparable = {key: value for key, value in rule.items() if key != "liveness_source"}
        sources[source.source_id] = {
            "rule": rule,
            "origin": selection.kind if selection else "own",
            "template_id": template_id,
            "sensor_kind": source.sensor_kind,
            "differs": comparison is not None and comparison != comparable,
        }
    for template_id in templates:
        shared = {
            key
            for key, row in sources.items()
            if row["origin"] == "shared" and row["template_id"] == template_id
        }
        own = {
            key
            for key, row in sources.items()
            if row["origin"] == "own" and row["template_id"] == template_id
        }
        contracts = {
            contract.contract_id
            for contract in config.contracts
            if any(item.ref in shared for item in contract.inputs)
        }
        usage[template_id] = {
            "source_ids": sorted(shared),
            "own_source_ids": sorted(own),
            "deviation_source_ids": sorted(key for key in own if sources[key]["differs"]),
            "contract_ids": sorted(contracts),
        }
    result: dict[str, Any] = {"sources": sources, "templates": usage}
    if config.config_schema_version == 5:
        result["identical_own_rules"] = identical_own_rules(config)
    return result
