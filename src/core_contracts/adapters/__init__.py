"""Physical addresses belong only to adapter configuration and transport."""

from typing import Literal, Self

from pydantic import Field, model_validator

from ..apple_tv_catalog import AppleTitleConfig
from ..calendar_cache import CalendarWindow
from ..calendar_inputs import CalendarInput
from ..model import Model


class AdapterConfig(Model):
    kind: Literal[
        "ha_state", "ha_attribute", "ha_calendar", "mqtt", "scheduler", "connectivity", "manual"
    ]
    ha_entity_id: str | None = None
    ha_attribute: str | None = None
    ha_time_attribute: str | None = None
    catalog_profile: Literal["stash_slot", "apple_tv_title"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    title_catalog: AppleTitleConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    mqtt_topic: str | None = None
    mqtt_value_path: str | None = None
    mqtt_time_path: str | None = None
    value_origin_path: str | None = Field(
        default=None, max_length=256, exclude_if=lambda value: value is None
    )
    calendar: CalendarInput | None = Field(default=None, exclude_if=lambda value: value is None)
    window: CalendarWindow | None = Field(default=None, exclude_if=lambda value: value is None)
    refresh_s: int | None = Field(
        default=None, ge=60, le=86400, exclude_if=lambda value: value is None
    )
    max_age_s: int | None = Field(
        default=None, ge=60, le=604800, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def address(self) -> Self:
        if self.catalog_profile == "stash_slot" and (
            self.kind != "ha_attribute" or self.ha_attribute != "scene_id"
        ):
            raise ValueError("catalog_profile_requires_scene_binding")
        if self.catalog_profile == "apple_tv_title" and (
            self.kind != "ha_attribute"
            or self.ha_attribute != "media_title"
            or self.title_catalog is None
        ):
            raise ValueError("apple_tv_title_binding_required")
        if self.title_catalog is not None and self.catalog_profile != "apple_tv_title":
            raise ValueError("apple_tv_title_profile_required")
        if self.kind == "manual" and any(
            value is not None
            for value in (
                self.ha_entity_id,
                self.ha_attribute,
                self.ha_time_attribute,
                self.mqtt_topic,
                self.mqtt_value_path,
                self.mqtt_time_path,
            )
        ):
            raise ValueError("manual_source_has_no_transport_address")
        if self.kind == "ha_calendar":
            import re

            if not re.fullmatch(r"calendar\.[a-z0-9_]+", self.ha_entity_id or ""):
                raise ValueError("calendar_entity_required")
            if self.window is None or self.refresh_s is None or self.max_age_s is None:
                raise ValueError("calendar_window_required")
            if self.refresh_s > self.max_age_s:
                raise ValueError("calendar_refresh_after_expiry")
        elif any(value is not None for value in (self.window, self.refresh_s, self.max_age_s)):
            raise ValueError("calendar_fields_require_calendar")
        if (self.kind == "scheduler") != (self.calendar is not None):
            raise ValueError("scheduler_calendar_required")
        if self.kind.startswith("ha_") and not self.ha_entity_id:
            raise ValueError("entity required")
        if self.kind == "ha_attribute" and not self.ha_attribute:
            raise ValueError("attribute required")
        if self.kind == "mqtt" and not self.mqtt_topic:
            raise ValueError("topic required")
        return self
