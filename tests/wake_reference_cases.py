"""Synthetic, source-independent input scenarios shared by generation and comparison."""

from dataclasses import replace
from datetime import datetime, time
from zoneinfo import ZoneInfo

VARIANTS = (
    "profiles",
    "profiles_midday",
    "holiday",
    "vacation",
    "skip",
    "marker",
    "early_ignore",
    "early_warn_only",
    "early_wake_earlier",
    "floor",
    "date_rule",
    "period_rule",
    "cycle_rule",
    "week_rule",
    "priority",
    "inactive",
    "holiday_without_rule",
)


def scenario(wp, wc, day, variant):
    config = wc.default_config()
    rules = wc.planning_rules(config)
    inputs = wp.WakePlanningInputs(
        now=datetime.combine(
            day, time(12) if variant == "profiles_midday" else time(0), ZoneInfo("Europe/Berlin")
        ),
        day_state="synthetic",
        rules=rules,
        floor_time=time(4),
        holiday_behavior=wp.HOLIDAY_WEEKEND_PROFILE,
    )
    if variant in {"holiday", "vacation", "holiday_without_rule"}:
        inputs = replace(
            inputs,
            holidays={
                day: wp.WakeHoliday(
                    True, is_vacation=variant == "vacation", source="manual_synthetic"
                )
            },
        )
    if variant == "skip":
        inputs = replace(inputs, calendar_decisions={day: wp.WakeCalendarDecision(skip=True)})
    if variant == "marker":
        inputs = replace(
            inputs, calendar_decisions={day: wp.WakeCalendarDecision(wake_time=time(8, 17))}
        )
    if variant.startswith("early_"):
        inputs = replace(
            inputs,
            routine_duration_minutes=75,
            calendar_conflict_behavior=variant.removeprefix("early_"),
            calendar_decisions={day: wp.WakeCalendarDecision(early_event_time=time(6, 30))},
        )
    if variant == "floor":
        inputs = replace(inputs, floor_time=time(10, 1))
    if variant.endswith("_rule") or variant == "priority":
        rule = dict(id="synthetic_rule", name="Synthetic", priority=1, wake_time="08:13")
        if variant == "date_rule":
            rule.update(specific_dates=[day.isoformat()])
        elif variant == "period_rule":
            rule.update(date_from="2032-02-01", date_to="2034-11-01")
        elif variant == "cycle_rule":
            rule.update(
                cycle_anchor="2032-01-01", cycle_length=4, cycle_slot_start=1, cycle_slot_length=2
            )
        elif variant == "week_rule":
            rule.update(week_interval=2, week_anchor="2032-01-05", weekdays=[0, 2, 4])
        elif variant == "priority":
            rule.update(action="skip")
        parsed = wp.parse_rules([rule])
        inputs = replace(inputs, rules=parsed.rules + rules)
    if variant in {"inactive", "holiday_without_rule"}:
        inputs = replace(
            inputs,
            rules=(
                wp.WakeRule(
                    id="disabled", name="Synthetic", priority=1, enabled=False, wake_time=time(8)
                ),
            ),
        )
    return inputs
