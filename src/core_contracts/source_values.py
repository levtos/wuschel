"""Source value transformations and declared-origin normalization."""

from math import isfinite
from typing import TYPE_CHECKING, Any

from .value_origin import ValueOrigin, forecast_points

if TYPE_CHECKING:
    from .registry import Binding, RegistryConfig


def transform(value: Any, binding: Binding, config: RegistryConfig) -> Any:
    def finite(number: float) -> float:
        if not isfinite(number):
            raise ValueError("non-finite number")
        return number

    transformation = binding.transform
    if transformation is None:
        return value
    if transformation.kind == "type_cast":
        if transformation.target == "number":
            if type(value) is bool:
                raise ValueError("invalid number")
            return finite(float(value))
        if transformation.target == "bool":
            if type(value) is not bool and (
                not isinstance(value, str) or value not in ("true", "false", "on", "off")
            ):
                raise ValueError("invalid boolean")
            return value in (True, "true", "on")
        return str(value)
    if transformation.kind == "unit_scale":
        assert transformation.factor is not None and transformation.offset is not None
        return finite(float(value) * transformation.factor + transformation.offset)
    catalog = next(c for c in config.catalogs if c.catalog_id == transformation.catalog_id)
    if catalog.kind != "map":
        raise ValueError("inline_catalog_required")
    return catalog.entries[str(value)]


def normalize(value: Any, metadata: Any, binding: Any, config: Any) -> tuple[Any, dict[str, Any]]:
    source = next(source for source in config.sources if source.source_id == binding.source_id)
    declaration = source.value_origin
    if config.config_schema_version == 1:
        return transform(value, binding, config), {}
    if isinstance(value, list) and declaration and declaration.kind == "forecast":
        points = forecast_points(value)
        converted = tuple(
            point.model_copy(update={"value": transform(point.value, binding, config)})
            for point in points
        )
        if not source.value_schema or any(
            not source.value_schema.accepts(point.value) for point in converted
        ):
            raise ValueError("forecast_point_type")
        if declaration and any(
            (point.value_origin.kind, point.value_origin.provider)
            != (declaration.kind, declaration.provider)
            for point in points
        ):
            raise ValueError("value_origin_mismatch")
        return [point.model_dump(mode="json") for point in converted], {"forecast": converted}
    origin = (
        ValueOrigin.model_validate(metadata)
        if metadata is not None
        else ValueOrigin.model_validate(declaration.model_dump())
        if declaration
        else None
    )
    if (
        declaration
        and origin
        and (origin.kind, origin.provider) != (declaration.kind, declaration.provider)
    ):
        raise ValueError("value_origin_mismatch")
    return transform(value, binding, config), {"value_origin": origin} if origin else {}
