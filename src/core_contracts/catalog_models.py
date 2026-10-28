"""Versioned managed definitions; no mutable contents in configuration."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .lifecycle import Lifecycle
from .model import Model
from .schema import FieldSchema


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


class ManagedCatalog(Model):
    catalog_id: str
    kind: Literal["managed"]
    version: int = Field(ge=1)
    identity_rule: IdentityRule
    classification_schema: ClassificationSchema
    unassigned_value: str
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


class EntryChange(Model):
    scope: str = Field(min_length=1, max_length=128)
    media_type: str = Field(min_length=1, max_length=128)
    signal_type: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=1024)
    classification: str | None = None
    master_key: str | None = Field(default=None, max_length=1024)
    tombstone: bool = False
    display_name: str = Field(default="", max_length=1024)
    note: str = Field(default="", max_length=1024)


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
