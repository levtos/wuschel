"""A1: inventory and synthetic golden digest from the pinned, untouched original.

This is NOT a port-versus-original comparison; that is the A2 acceptance gate.
Run with --source-root pointing at a read-only clone of Levtos/benni-core-state.
"""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import types
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REVISION = "c9408fa33f2914b1c5f7316738cb38b43add33b5"


def load_original(root):
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != REVISION:
        raise ValueError("reference_revision_mismatch")
    folder = root / "custom_components" / "benni_core_state"
    package = types.ModuleType("_wake_reference")
    package.__path__ = [str(folder)]
    sys.modules[package.__name__] = package
    loaded, digests = [], {}
    for name in ("wake_planning", "wake_config"):
        path = folder / (name + ".py")
        blob = subprocess.check_output(
            ["git", "-C", str(root), "show", f"HEAD:custom_components/benni_core_state/{name}.py"]
        )
        # Worktree files must be identical modulo Git's CRLF checkout conversion.
        assert path.read_bytes().replace(b"\r\n", b"\n") == blob.replace(b"\r\n", b"\n")
        digests[name] = hashlib.sha256(blob).hexdigest()
        spec = importlib.util.spec_from_file_location(f"_wake_reference.{name}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(package, name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return *loaded, digests


def main(root):
    wp, wc, sources = load_original(root)
    config = wc.default_config()
    rules = wc.planning_rules(config)
    zone = ZoneInfo("Europe/Berlin")
    start, end = date(2032, 1, 1), date(2035, 1, 1)
    digest = hashlib.sha256()
    count, day = 0, start
    variants = ("profiles", "holiday", "vacation", "skip", "marker", "early", "rules")
    while day < end:
        base = wp.WakePlanningInputs(
            now=datetime.combine(day, time(0), zone),
            day_state="synthetic",
            rules=rules,
            floor_time=time(4),
            holiday_behavior=wp.HOLIDAY_WEEKEND_PROFILE,
        )
        for variant in variants:
            inputs = base
            if variant in {"holiday", "vacation"}:
                inputs = replace(
                    base,
                    holidays={
                        day: wp.WakeHoliday(
                            is_holiday=True,
                            is_vacation=variant == "vacation",
                            source="manual_synthetic",
                        )
                    },
                )
            elif variant == "skip":
                inputs = replace(base, calendar_decisions={day: wp.WakeCalendarDecision(skip=True)})
            elif variant == "marker":
                inputs = replace(
                    base, calendar_decisions={day: wp.WakeCalendarDecision(wake_time=time(8, 17))}
                )
            elif variant == "early":
                inputs = replace(
                    base,
                    routine_duration_minutes=75,
                    calendar_conflict_behavior=wp.CONFLICT_WAKE_EARLIER,
                    calendar_decisions={day: wp.WakeCalendarDecision(early_event_time=time(6, 30))},
                )
            elif variant == "rules":
                parsed = wp.parse_rules(
                    [
                        dict(
                            id="date",
                            name="Synthetic",
                            priority=1,
                            specific_dates=[day.isoformat()],
                            wake_time="08:13",
                        ),
                        dict(
                            id="cycle",
                            name="Synthetic",
                            priority=2,
                            cycle_anchor="2032-01-01",
                            cycle_length=4,
                            cycle_slot_start=1,
                            cycle_slot_length=2,
                            wake_time="08:19",
                        ),
                        dict(
                            id="range",
                            name="Synthetic",
                            priority=3,
                            date_from="2032-02-01",
                            date_to="2034-11-01",
                            week_interval=2,
                            week_anchor="2032-01-05",
                            wake_time="08:23",
                        ),
                    ]
                )
                assert not parsed.invalid_reasons
                inputs = replace(base, rules=parsed.rules + rules)
            plan = wp.plan_wake(inputs)
            assert plan.next_wake is not None and plan.next_wake >= inputs.now
            row = dict(day=day.isoformat(), variant=variant, result=plan.as_attributes())
            digest.update(json.dumps(row, sort_keys=True, default=str).encode())
            digest.update(b"\n")
            count += 1
        day += timedelta(days=1)
    example_day = date(2032, 1, 5)
    example = replace(
        base,
        now=datetime.combine(example_day, time(0), zone),
        calendar_date=None,
        calendar_decisions={example_day: wp.WakeCalendarDecision(early_event_time=time(6, 30))},
        routine_duration_minutes=75,
        calendar_conflict_behavior=wp.CONFLICT_WAKE_EARLIER,
        rules=rules,
        holidays={},
        floor_time=time(4),
    )
    planned = wp.plan_wake(example)
    assert planned.wake_time == time(5, 15)
    assert wp.plan_wake(replace(example, floor_time=time(6))).wake_time == time(6)
    changed = wp.plan_wake(replace(example, minimum_sleep_minutes=480))
    assert changed.next_wake == planned.next_wake
    assert changed.minimum_sleep_status == "configured"
    print(
        json.dumps(
            dict(
                reference=REVISION,
                source_sha256=sources,
                days=(end - start).days,
                variants=len(variants),
                results=count,
                original_golden_sha256=digest.hexdigest(),
                lead_formula="06:30 - 75 minutes = 05:15; floor 06:00 => 06:00",
                minimum_sleep="metadata only; does not shift original next_wake",
                port_comparison="not implemented in A1",
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    main(parser.parse_args().source_root.resolve())
