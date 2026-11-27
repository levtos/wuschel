"""Versioned managed definitions; no mutable contents in configuration."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, PrivateAttr, StrictBool, StrictInt, StrictStr, model_validator

from core_contracts.lifecycle import Lifecycle
from core_contracts.model import Model
from core_contracts.schema import FieldSchema


class IdentityRule(Model):
    version: Literal[1] = 1
    fields: tuple[Literal["scope", "media_type", "signal_type", "normalized_key"], ...] = (
        "scope",
        "media_type",
        "signal_type",
        "normalized_key",
    )
    normalizer: Literal["normalize_key"] = "normalize_key"

    @model_validator(mode="after")
    def fixed(self) -> Self:
        if self.fields != ("scope", "media_type", "signal_type", "normalized_key"):
            raise ValueError("catalog_identity_rule")
        return self


class ClassLegend(Model):
    display_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def trimmed(self) -> Self:
        if any(
            not text.strip() or text != text.strip()
            for text in (self.display_name, self.description)
        ):
            raise ValueError("catalog_legend_text")
        return self


class ClassificationSchema(Model):
    type: Literal["enum"] = "enum"
    values: tuple[str, ...] = Field(min_length=1, max_length=64)
    legend: dict[str, ClassLegend] | None = Field(
        default=None, max_length=64, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.values)) != len(self.values) or any(
            not value or len(value) > 128 for value in self.values
        ):
            raise ValueError("catalog_classes")
        if self.legend is not None and self.legend.keys() != set(self.values):
            raise ValueError("catalog_legend_classes")
        return self

    def field_schema(self) -> FieldSchema:
        return FieldSchema(
            type="enum", required=True, physical_state=False, value_catalog=self.values
        )


ClassKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ClassValue = StrictBool | StrictInt | StrictStr


class CatalogClass(Model):
    class_key: ClassKey
    display_name: str = Field(min_length=1, max_length=80)
    kind: Literal["enum", "integer", "boolean"]
    default: ClassValue
    values: dict[ClassKey, ClassLegend] | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    minimum: StrictInt | None = None
    maximum: StrictInt | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not self.display_name.strip() or self.display_name != self.display_name.strip():
            raise ValueError("catalog_class_label")
        if self.kind == "integer":
            if (
                not self.unit
                or not self.unit.strip()
                or self.minimum is None
                or self.maximum is None
                or self.minimum > self.maximum
                or self.values is not None
            ):
                raise ValueError("catalog_class_integer_schema")
        elif any(item is not None for item in (self.unit, self.minimum, self.maximum)):
            raise ValueError("catalog_class_unexpected_numeric_schema")
        elif (self.kind == "enum") != bool(self.values):
            raise ValueError("catalog_class_enum_schema")
        self.validate_value(self.default)
        return self

    def field_schema(self) -> FieldSchema:
        if self.kind == "enum":
            return FieldSchema(
                type="enum",
                required=True,
                physical_state=False,
                value_catalog=tuple(sorted(self.values or {})),
            )
        if self.kind == "boolean":
            return FieldSchema(type="bool", required=True, physical_state=False)
        return FieldSchema(type="number", required=True, physical_state=False, unit=self.unit)

    def validate_value(self, value: ClassValue) -> None:
        match self.kind:
            case "enum":
                valid = type(value) is str and value in (self.values or {})
            case "integer":
                valid = (
                    type(value) is int
                    and self.minimum is not None
                    and self.maximum is not None
                    and self.minimum <= value <= self.maximum
                )
            case "boolean":
                valid = type(value) is bool
        if not valid:
            raise ValueError(f"catalog_class_value_invalid:{self.class_key}")


class CatalogClassAssignment(Model):
    class_key: ClassKey
    default: ClassValue | None = Field(default=None, exclude_if=lambda value: value is None)
    title_catalog_mapping: dict[Annotated[str, Field(pattern=r"^[0-9]+$")], ClassValue] = Field(
        default_factory=dict, max_length=100, exclude_if=lambda value: not value
    )


class ManagedCatalog(Model):
    catalog_id: str
    kind: Literal["managed"]
    version: int = Field(ge=1)
    identity_rule: IdentityRule
    classification_schema: ClassificationSchema
    unassigned_value: str
    class_assignments: tuple[CatalogClassAssignment, ...] = Field(
        default=(), max_length=64, exclude_if=lambda value: not value
    )
    _assigned_classes: dict[str, CatalogClass] = PrivateAttr(default_factory=dict)
    master_depth: Literal[1] = 1
    lifecycle: Lifecycle = Field(default="active", exclude_if=lambda value: value == "active")
    reactivation_note: str | None = Field(
        default=None, max_length=256, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def default_class(self) -> Self:
        if self.unassigned_value not in self.classification_schema.values:
            raise ValueError("catalog_default_class")
        return self

    def resolve_assignments(self, definitions: dict[str, CatalogClass]) -> None:
        classes: dict[str, CatalogClass] = {}
        for assignment in self.class_assignments:
            key = assignment.class_key
            if key in classes:
                raise ValueError(f"catalog_class_assignment_duplicate:{self.catalog_id}:{key}")
            definition = definitions.get(key)
            if definition is None:
                raise ValueError(f"catalog_class_assignment_missing:{self.catalog_id}:{key}")
            if assignment.default is not None:
                definition.validate_value(assignment.default)
            for value in assignment.title_catalog_mapping.values():
                definition.validate_value(value)
            classes[key] = definition
        self._assigned_classes = classes

    def class_defaults(self) -> dict[str, ClassValue]:
        return {
            assignment.class_key: (
                assignment.default
                if assignment.default is not None
                else self._assigned_classes[assignment.class_key].default
            )
            for assignment in self.class_assignments
        }

    def validate_classes(self, values: dict[str, ClassValue]) -> None:
        for key, value in values.items():
            definition = self._assigned_classes.get(key)
            if definition is None:
                raise ValueError(f"catalog_class_unassigned:{self.catalog_id}:{key}")
            definition.validate_value(value)

    @property
    def assigned_classes(self) -> dict[str, CatalogClass]:
        return dict(self._assigned_classes)


class TitleSightings(Model):
    first_seen: datetime
    last_seen: datetime
    seen_count: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if (
            self.first_seen.tzinfo is None
            or self.last_seen.tzinfo is None
            or self.first_seen > self.last_seen
        ):
            raise ValueError("title_sync_source_invalid")
        return self


class TitleContext(Model):
    context: str = Field(max_length=8192)
    source_app: str = Field(default="", max_length=8192)
    enum_override: int | None = Field(default=None, ge=0, le=9, strict=True)
    telemetry: TitleSightings | None = None


class TitleMetadata(Model):
    is_alias: bool = Field(default=False, exclude_if=lambda value: not value)
    source_id: str | None = Field(default=None, max_length=128)
    source_identity: tuple[str, str, str, str]
    artist: str | None = Field(default=None, max_length=8192)
    title: str | None = Field(default=None, max_length=8192)
    album: str | None = Field(default=None, max_length=8192)
    app_name: str | None = Field(default=None, max_length=8192)
    source_class: int = Field(ge=0, le=9, strict=True)
    parent_id: str | None = Field(default=None, max_length=128)
    telemetry: TitleSightings | None = None
    contexts: tuple[TitleContext, ...] = Field(default=(), max_length=64)


class CatalogMediaMetadata(Model):
    app_id: str = Field(max_length=1024)
    app_name: str = Field(default="", max_length=1024)
    live_tv: bool = False
    sender: str = Field(default="", max_length=1024)


class EntryChange(Model):
    scope: str = Field(min_length=1, max_length=128)
    media_type: str = Field(min_length=1, max_length=128)
    signal_type: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=1024)
    classification: str | None = None
    class_values: dict[ClassKey, ClassValue] = Field(
        default_factory=dict, max_length=64, exclude_if=lambda value: not value
    )
    master_key: str | None = Field(default=None, max_length=1024)
    tombstone: bool = False
    display_name: str = Field(default="", max_length=1024)
    display_metadata: (
        dict[Literal["title", "artist"], Annotated[str, Field(max_length=1024)]] | None
    ) = Field(default=None, max_length=2, exclude_if=lambda value: value is None)
    catalog_metadata: CatalogMediaMetadata | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    note: str = Field(default="", max_length=1024)
    reviewed_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    hidden_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    tc_metadata: TitleMetadata | None = Field(default=None, exclude_if=lambda value: value is None)


class CatalogWrite(Model):
    catalog_id: str
    definition_version: int = Field(ge=1)
    expected_active_revision: int = Field(ge=0, strict=True)
    expected_content_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    changes: tuple[EntryChange, ...] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=256)


class CatalogRestore(CatalogWrite):
    changes: tuple[EntryChange, ...] = Field(default=(), max_length=0)
    restore_revision: int = Field(ge=0, strict=True)
