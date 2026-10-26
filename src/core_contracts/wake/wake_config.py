# Ported from Levtos/benni-core-state @ c9408fa33f2914b1c5f7316738cb38b43add33b5.
# Preserve pure planning semantics; see docs/beta/bereich-4/A2.md.
"""Core-State-owned automatic Wake-Planning configuration.

The old wake planner is read only by the migration bridge.  Once this module
has persisted a configuration, all planning inputs used by Core State come
from this versioned document.  The data model deliberately contains only the
two effective profiles and automatic rule types; manual skip/time overrides
are not represented.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, time
from typing import Any

from . import wake_planning

CONFIG_CONTRACT_VERSION = "1.0.0"
CONFIG_STORAGE_VERSION = 1
PROFILE_WEEKDAY = "weekday"
PROFILE_WEEKEND = "weekend"
PROFILE_IDS = (PROFILE_WEEKDAY, PROFILE_WEEKEND)
DEFAULT_WAKE_TIME = {
    PROFILE_WEEKDAY: "07:00",
    PROFILE_WEEKEND: "09:30",
}


def _time_text(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def _positive_int(value: Any, *, allow_none: bool = True) -> int | None:
    if value is None and allow_none:
        return None
    try:
        result = int(value)
    except TypeError, ValueError:
        raise ValueError("must_be_integer") from None
    if result <= 0:
        raise ValueError("must_be_positive")
    return result


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except TypeError, ValueError:
        raise ValueError("must_be_integer") from None
    if not minimum <= result <= maximum:
        raise ValueError(f"must_be_between_{minimum}_{maximum}")
    return result


@dataclass(frozen=True, slots=True)
class WakeProfileConfig:
    """One of the two effective automatic wake profiles."""

    id: str
    label: str
    wake_time: str
    wake_window_minutes: int = 5
    minimum_sleep_minutes: int | None = None
    provisional_lead_minutes: int | None = None

    def validated(self) -> WakeProfileConfig:
        parsed = wake_planning.parse_time(self.wake_time)
        if parsed is None:
            raise ValueError("wake_time_invalid")
        if self.id not in PROFILE_IDS:
            raise ValueError("profile_invalid")
        return replace(
            self,
            wake_time=_time_text(parsed) or self.wake_time,
            wake_window_minutes=_bounded_int(self.wake_window_minutes, 0, 120),
            minimum_sleep_minutes=_positive_int(self.minimum_sleep_minutes),
            provisional_lead_minutes=_positive_int(self.provisional_lead_minutes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "wake_time": self.wake_time,
            "wake_window_minutes": self.wake_window_minutes,
            "minimum_sleep_minutes": self.minimum_sleep_minutes,
            "provisional_lead_minutes": self.provisional_lead_minutes,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], profile_id: str) -> WakeProfileConfig:
        default = default_profile(profile_id)
        return cls(
            id=profile_id,
            label=str(raw.get("label") or default.label),
            wake_time=str(raw.get("wake_time") or default.wake_time),
            wake_window_minutes=raw.get("wake_window_minutes", default.wake_window_minutes),
            minimum_sleep_minutes=raw.get("minimum_sleep_minutes", default.minimum_sleep_minutes),
            provisional_lead_minutes=raw.get(
                "provisional_lead_minutes", default.provisional_lead_minutes
            ),
        ).validated()


@dataclass(frozen=True, slots=True)
class WakePlanningConfig:
    """Versioned, serialisable Core-State wake-planning document."""

    version: int
    weekday: WakeProfileConfig
    weekend: WakeProfileConfig
    rules: tuple[dict[str, Any], ...]
    calendar_entity: str | None
    holiday_calendar_entity: str | None
    manual_holiday_intervals: tuple[str, ...]
    calendar_skip_titles: tuple[str, ...]
    calendar_wake_pattern: str
    routine_duration_minutes: int
    calendar_conflict_behavior: str
    holiday_behavior: str
    wake_floor: str
    migration: Mapping[str, Any]

    def validated(self) -> WakePlanningConfig:
        if self.version != CONFIG_STORAGE_VERSION:
            raise ValueError("config_version_unsupported")
        weekday = self.weekday.validated()
        weekend = self.weekend.validated()
        if weekday.id != PROFILE_WEEKDAY or weekend.id != PROFILE_WEEKEND:
            raise ValueError("profile_pair_invalid")
        if self.calendar_conflict_behavior not in wake_planning.CONFLICT_BEHAVIORS:
            raise ValueError("calendar_conflict_behavior_invalid")
        if self.holiday_behavior not in {
            wake_planning.HOLIDAY_SKIP,
            wake_planning.HOLIDAY_WEEKEND_PROFILE,
        }:
            raise ValueError("holiday_behavior_invalid")
        if wake_planning.parse_time(self.wake_floor) is None:
            raise ValueError("wake_floor_invalid")
        return replace(
            self,
            weekday=weekday,
            weekend=weekend,
            rules=tuple(_normalise_rule_payloads(self.rules)),
            manual_holiday_intervals=tuple(
                str(item).strip() for item in self.manual_holiday_intervals if str(item).strip()
            ),
            calendar_skip_titles=tuple(
                str(item).strip() for item in self.calendar_skip_titles if str(item).strip()
            ),
            calendar_wake_pattern=str(self.calendar_wake_pattern or "").strip()
            or wake_planning.DEFAULT_CALENDAR_WAKE_PATTERN,
            routine_duration_minutes=_bounded_int(self.routine_duration_minutes, 0, 1440),
            holiday_behavior=wake_planning.HOLIDAY_WEEKEND_PROFILE,
        )

    @property
    def profiles(self) -> tuple[WakeProfileConfig, WakeProfileConfig]:
        return self.weekday, self.weekend

    def profile_for(self, profile_id: str) -> WakeProfileConfig:
        if profile_id == PROFILE_WEEKEND:
            return self.weekend
        return self.weekday

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "contract_version": CONFIG_CONTRACT_VERSION,
            "profiles": {
                PROFILE_WEEKDAY: self.weekday.to_dict(),
                PROFILE_WEEKEND: self.weekend.to_dict(),
            },
            "rules": [dict(rule) for rule in self.rules],
            "calendar_entity": self.calendar_entity,
            "holiday_calendar_entity": self.holiday_calendar_entity,
            "manual_holiday_intervals": list(self.manual_holiday_intervals),
            "calendar_skip_titles": list(self.calendar_skip_titles),
            "calendar_wake_pattern": self.calendar_wake_pattern,
            "routine_duration_minutes": self.routine_duration_minutes,
            "calendar_conflict_behavior": self.calendar_conflict_behavior,
            "holiday_behavior": self.holiday_behavior,
            "wake_floor": self.wake_floor,
            "migration": dict(self.migration),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> WakePlanningConfig:
        raw = raw or {}
        profiles = raw.get("profiles")
        if not isinstance(profiles, Mapping):
            profiles = {}
        config = cls(
            version=int(raw.get("version", CONFIG_STORAGE_VERSION)),
            weekday=WakeProfileConfig.from_dict(
                profiles.get(PROFILE_WEEKDAY, {})
                if isinstance(profiles.get(PROFILE_WEEKDAY, {}), Mapping)
                else {},
                PROFILE_WEEKDAY,
            ),
            weekend=WakeProfileConfig.from_dict(
                profiles.get(PROFILE_WEEKEND, {})
                if isinstance(profiles.get(PROFILE_WEEKEND, {}), Mapping)
                else {},
                PROFILE_WEEKEND,
            ),
            rules=tuple(dict(item) for item in raw.get("rules", ()) if isinstance(item, Mapping)),
            calendar_entity=_optional_text(raw.get("calendar_entity")),
            holiday_calendar_entity=_optional_text(raw.get("holiday_calendar_entity")),
            manual_holiday_intervals=tuple(
                raw.get("manual_holiday_intervals", ())
                if isinstance(raw.get("manual_holiday_intervals", ()), (list, tuple))
                else (),
            ),
            calendar_skip_titles=tuple(
                raw.get("calendar_skip_titles", wake_planning.DEFAULT_CALENDAR_SKIP_TITLES)
                if isinstance(
                    raw.get("calendar_skip_titles", wake_planning.DEFAULT_CALENDAR_SKIP_TITLES),
                    (list, tuple),
                )
                else wake_planning.DEFAULT_CALENDAR_SKIP_TITLES,
            ),
            calendar_wake_pattern=str(
                raw.get("calendar_wake_pattern", wake_planning.DEFAULT_CALENDAR_WAKE_PATTERN)
            ),
            routine_duration_minutes=raw.get("routine_duration_minutes", 60),
            calendar_conflict_behavior=str(
                raw.get("calendar_conflict_behavior", wake_planning.CONFLICT_WARN_ONLY)
            ),
            holiday_behavior=str(
                raw.get("holiday_behavior", wake_planning.HOLIDAY_WEEKEND_PROFILE)
            ),
            wake_floor=str(raw.get("wake_floor", "06:00")),
            migration=(
                raw.get("migration", {}) if isinstance(raw.get("migration", {}), Mapping) else {}
            ),
        )
        return config.validated()


def default_profile(profile_id: str) -> WakeProfileConfig:
    labels = {PROFILE_WEEKDAY: "Werktag", PROFILE_WEEKEND: "Wochenende"}
    return WakeProfileConfig(
        id=profile_id,
        label=labels.get(profile_id, profile_id),
        wake_time=DEFAULT_WAKE_TIME.get(profile_id, "07:00"),
    )


def _profile_rule(profile: WakeProfileConfig) -> dict[str, Any]:
    if profile.id == PROFILE_WEEKEND:
        return {
            "id": "profile_weekend",
            "name": profile.label,
            "priority": 110,
            "enabled": True,
            "weekdays": [5, 6],
            "wake_time": profile.wake_time,
            "action": wake_planning.RULE_ACTION_WAKE,
        }
    return {
        "id": "profile_weekday",
        "name": profile.label,
        "priority": 100,
        "enabled": True,
        "weekdays": [0, 1, 2, 3, 4],
        "on_holiday": False,
        "wake_time": profile.wake_time,
        "action": wake_planning.RULE_ACTION_WAKE,
    }


def default_config() -> WakePlanningConfig:
    return WakePlanningConfig(
        version=CONFIG_STORAGE_VERSION,
        weekday=default_profile(PROFILE_WEEKDAY),
        weekend=default_profile(PROFILE_WEEKEND),
        rules=(),
        calendar_entity=None,
        holiday_calendar_entity=None,
        manual_holiday_intervals=(),
        calendar_skip_titles=wake_planning.DEFAULT_CALENDAR_SKIP_TITLES,
        calendar_wake_pattern=wake_planning.DEFAULT_CALENDAR_WAKE_PATTERN,
        routine_duration_minutes=wake_planning.DEFAULT_ROUTINE_DURATION_MINUTES,
        calendar_conflict_behavior=wake_planning.CONFLICT_WARN_ONLY,
        holiday_behavior=wake_planning.HOLIDAY_WEEKEND_PROFILE,
        wake_floor="06:00",
        migration={
            "status": "default",
            "source": "core_state",
            "version": CONFIG_STORAGE_VERSION,
        },
    ).validated()


def planning_rules(config: WakePlanningConfig) -> tuple[wake_planning.WakeRule, ...]:
    """Return profile rules plus valid automatic custom rules."""

    raw_rules = [_profile_rule(config.weekday), _profile_rule(config.weekend)]
    raw_rules.extend(
        rule
        for rule in config.rules
        if str(rule.get("id")) not in {"profile_weekday", "profile_weekend", "profile_holiday"}
    )
    parsed = wake_planning.parse_rules(raw_rules)
    return parsed.rules


def profile_id_for_date(day: date, holidays: Mapping[date, Any]) -> str:
    if day.weekday() >= 5 or bool(getattr(holidays.get(day), "is_holiday", False)):
        return PROFILE_WEEKEND
    return PROFILE_WEEKDAY


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _normalise_rule_payloads(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(rules):
        if not isinstance(raw, Mapping):
            continue
        parsed = wake_planning.parse_rules([raw])
        if not parsed.rules:
            continue
        rule = parsed.rules[0]
        payload: dict[str, Any] = {
            "id": rule.id or f"rule_{index}",
            "name": rule.name,
            "priority": rule.priority,
            "enabled": rule.enabled,
            "weekdays": sorted(rule.weekdays) if rule.weekdays is not None else None,
            "date_from": rule.date_from.isoformat() if rule.date_from else None,
            "date_to": rule.date_to.isoformat() if rule.date_to else None,
            "week_interval": rule.week_interval,
            "week_anchor": rule.week_anchor.isoformat() if rule.week_anchor else None,
            "specific_dates": [item.isoformat() for item in rule.specific_dates]
            if rule.specific_dates is not None
            else None,
            "cycle_anchor": rule.cycle_anchor.isoformat() if rule.cycle_anchor else None,
            "cycle_length": rule.cycle_length,
            "cycle_slot_start": rule.cycle_slot_start,
            "cycle_slot_length": rule.cycle_slot_length,
            "on_holiday": rule.on_holiday,
            "action": rule.action,
            "wake_time": _time_text(rule.wake_time),
        }
        result.append(payload)
    return result


def update_profile(
    config: WakePlanningConfig, profile_id: str, patch: Mapping[str, Any]
) -> WakePlanningConfig:
    if profile_id not in PROFILE_IDS:
        raise ValueError("profile_invalid")
    allowed = {
        "label",
        "wake_time",
        "wake_window_minutes",
        "minimum_sleep_minutes",
        "provisional_lead_minutes",
    }
    if set(patch) - allowed:
        raise ValueError("profile_field_not_allowed")
    current = config.profile_for(profile_id)
    updated = WakeProfileConfig(
        id=profile_id,
        label=str(patch.get("label", current.label)),
        wake_time=str(patch.get("wake_time", current.wake_time)),
        wake_window_minutes=patch.get("wake_window_minutes", current.wake_window_minutes),
        minimum_sleep_minutes=patch.get("minimum_sleep_minutes", current.minimum_sleep_minutes),
        provisional_lead_minutes=patch.get(
            "provisional_lead_minutes", current.provisional_lead_minutes
        ),
    ).validated()
    return replace(
        config,
        weekday=updated if profile_id == PROFILE_WEEKDAY else config.weekday,
        weekend=updated if profile_id == PROFILE_WEEKEND else config.weekend,
    ).validated()


def update_settings(config: WakePlanningConfig, patch: Mapping[str, Any]) -> WakePlanningConfig:
    allowed = {
        "calendar_entity",
        "holiday_calendar_entity",
        "manual_holiday_intervals",
        "calendar_skip_titles",
        "calendar_wake_pattern",
        "routine_duration_minutes",
        "calendar_conflict_behavior",
        "wake_floor",
    }
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError("settings_field_not_allowed")
    values = {key: getattr(config, key) for key in allowed}
    values.update({key: patch[key] for key in patch})
    for key in ("manual_holiday_intervals", "calendar_skip_titles"):
        if isinstance(values[key], str):
            values[key] = tuple(
                item.strip() for item in values[key].replace(";", "\n").splitlines() if item.strip()
            )
        elif isinstance(values[key], (list, tuple)):
            values[key] = tuple(str(item).strip() for item in values[key] if str(item).strip())
        else:
            values[key] = ()
    return replace(config, **values).validated()


def upsert_rule(config: WakePlanningConfig, rule: Mapping[str, Any]) -> WakePlanningConfig:
    rule_id = str(rule.get("id") or "").strip()
    if not rule_id or rule_id in {"profile_weekday", "profile_weekend", "profile_holiday"}:
        raise ValueError("profile_rules_are_managed_by_profile_command")
    normalised = _normalise_rule_payloads([rule])
    if not normalised:
        raise ValueError("rule_invalid")
    rows = [dict(item) for item in config.rules if str(item.get("id")) != rule_id]
    rows.append(normalised[0])
    return replace(config, rules=tuple(rows)).validated()


def remove_rule(config: WakePlanningConfig, rule_id: str) -> WakePlanningConfig:
    if rule_id in {"profile_weekday", "profile_weekend", "profile_holiday"}:
        raise ValueError("profile_rules_cannot_be_removed")
    return replace(
        config,
        rules=tuple(item for item in config.rules if str(item.get("id")) != rule_id),
    ).validated()
