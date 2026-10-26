"""Closed instance configuration for the original planning kernel."""

import re
from datetime import date
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ..model import Model, canonical
from . import wake_config as config
from . import wake_planning as planning


def time_text(value: str) -> str:
    parsed = planning.parse_time(value)
    if parsed is None or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("wake_time_invalid")
    return value


class Profile(Model):
    id: Literal["weekday", "weekend"]
    label: str = Field(min_length=1, max_length=64)
    wake_time: str
    wake_window_minutes: int = Field(default=5, ge=0, le=120, strict=True)
    minimum_sleep_minutes: int | None = Field(default=None, gt=0, le=525600, strict=True)
    provisional_lead_minutes: int | None = Field(default=None, gt=0, le=525600, strict=True)

    _time = field_validator("wake_time")(time_text)


class PlanningRule(Model):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    priority: int = Field(default=100, strict=True)
    enabled: bool = Field(default=True, strict=True)
    weekdays: tuple[int, ...] | None = Field(default=None, min_length=1, max_length=7)
    date_from: str | None = None
    date_to: str | None = None
    specific_dates: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=128)
    week_interval: int | None = Field(default=None, gt=0, le=5200, strict=True)
    week_anchor: str | None = None
    cycle_anchor: str | None = None
    cycle_length: int | None = Field(default=None, gt=0, le=36600, strict=True)
    cycle_slot_start: int | None = Field(default=None, ge=0, strict=True)
    cycle_slot_length: int | None = Field(default=None, gt=0, strict=True)
    on_holiday: bool | None = Field(default=None, strict=True)
    action: Literal["wake", "skip"] = "wake"
    wake_time: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        for raw in (
            self.date_from,
            self.date_to,
            self.week_anchor,
            self.cycle_anchor,
            *(self.specific_dates or ()),
        ):
            if raw is not None:
                date.fromisoformat(raw)
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("rule_date_order")
        if self.weekdays and any(type(v) is not int or v not in range(7) for v in self.weekdays):
            raise ValueError("rule_weekdays")
        if bool(self.week_interval) != bool(self.week_anchor):
            raise ValueError("rule_week_pair")
        cycle = (
            self.cycle_anchor,
            self.cycle_length,
            self.cycle_slot_start,
            self.cycle_slot_length,
        )
        if any(v is not None for v in cycle):
            if any(v is None for v in cycle):
                raise ValueError("rule_cycle_complete")
            assert self.cycle_length is not None and self.cycle_slot_start is not None
            assert self.cycle_slot_length is not None
            if self.cycle_slot_start + self.cycle_slot_length > self.cycle_length:
                raise ValueError("rule_cycle_bounds")
        if self.action == "wake" and self.wake_time is None:
            raise ValueError("wake_time_required")
        if self.wake_time is not None:
            time_text(self.wake_time)
        return self


class WakeSettings(Model):
    version: Literal[1] = 1
    profiles: dict[Literal["weekday", "weekend"], Profile]
    rules: tuple[PlanningRule, ...] = Field(default=(), max_length=128)
    manual_holiday_intervals: tuple[str, ...] = Field(default=(), max_length=128)
    calendar_skip_titles: tuple[str, ...] = Field(
        default=planning.DEFAULT_CALENDAR_SKIP_TITLES, max_length=32
    )
    calendar_wake_pattern: str = Field(
        default=planning.DEFAULT_CALENDAR_WAKE_PATTERN, max_length=256
    )
    routine_duration_minutes: int = Field(default=60, ge=0, le=1440, strict=True)
    calendar_conflict_behavior: Literal["ignore", "warn_only", "wake_earlier"] = "warn_only"
    holiday_behavior: Literal["weekend_profile"] = "weekend_profile"
    wake_floor: str = "06:00"
    vacation_pattern: str = Field(default="*Urlaub*", min_length=1, max_length=256)

    _floor = field_validator("wake_floor")(time_text)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if set(self.profiles) != {"weekday", "weekend"} or any(
            key != profile.id for key, profile in self.profiles.items()
        ):
            raise ValueError("profile_pair_invalid")
        identifiers = [rule.id for rule in self.rules]
        if len(set(identifiers)) != len(identifiers) or set(identifiers) & {
            "profile_weekday",
            "profile_weekend",
            "profile_holiday",
        }:
            raise ValueError("reserved_or_duplicate_rule")
        try:
            pattern = re.compile(self.calendar_wake_pattern, re.I)
        except re.error as error:
            raise ValueError("wake_pattern_invalid") from error
        if not pattern.groups or pattern.groupindex and "time" not in pattern.groupindex:
            raise ValueError("wake_pattern_time_group_required")
        if any(not title.strip() or len(title) > 256 for title in self.calendar_skip_titles):
            raise ValueError("wake_title_limit")
        for interval in self.manual_holiday_intervals:
            parts = re.split(r"\s*(?:\.\.|/|to|bis)\s*", interval.strip(), flags=re.I)
            if len(parts) not in (1, 2) or any(
                planning.parse_manual_holiday_date(part, 2032) is None for part in parts
            ):
                raise ValueError("holiday_interval_invalid")
        if len(canonical(self).encode()) > 131072:
            raise ValueError("wake_settings_limit")
        return self

    def original(self) -> config.WakePlanningConfig:
        return config.WakePlanningConfig.from_dict(self.model_dump(mode="json"))


class WakeException(Model):
    date: str
    wake_time: str | None = None
    skip: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        date.fromisoformat(self.date)
        if self.skip == (self.wake_time is not None):
            raise ValueError("exception_time_or_skip")
        if self.wake_time is not None:
            time_text(self.wake_time)
        return self
