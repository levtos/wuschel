"""Identical broker replay never refreshes, replaces or recreates device evidence."""

import copy
from datetime import timedelta

import pytest

from .test_beta_openings import NOW, closed, field, make_runtime, report


async def test_restart_and_identical_retained_replay_preserve_all_seven_proofs():
    rt = await make_runtime()
    for index in range(4):
        await closed(rt, index)
    await rt.stop()
    rt.clock.advance(60)
    rt = await make_runtime(rt.store, rt.clock)
    try:
        before = copy.deepcopy(rt.state.tables["source_observation_current"])
        publications = rt.state.publication_seq
        history = await rt.store.rows("source_observation_history")
        for index in range(4):
            for name in ("open", "tilt") if index < 3 else ("contact",):
                await report(rt, index, name, stamp=NOW, retained=True)
        assert rt.state.tables["source_observation_current"] == before
        assert rt.state.publication_seq == publications
        assert await rt.store.rows("source_observation_history") == history
        assert all(field(rt, f"test.opening{i}")["status"] == "valid" for i in range(4))
        rt.clock.advance(8341)
        await rt.submit("tick")
        assert all(field(rt, f"test.opening{i}")["status"] == "unknown" for i in range(4))
        for index in range(4):
            for name in ("open", "tilt") if index < 3 else ("contact",):
                await report(rt, index, name, stamp=NOW, retained=True)
        assert all(field(rt, f"test.opening{i}")["status"] == "unknown" for i in range(4))
    finally:
        await rt.stop()


@pytest.mark.parametrize("kind", ["other_value", "newer", "older", "no_proof", "invalid"])
async def test_retained_nonidentical_or_unproven_report_keeps_existing_conservative_rules(kind):
    rt = await make_runtime()
    try:
        await closed(rt)
        rt.clock.advance(1)
        before = copy.deepcopy(rt.state.tables["source_observation_current"]["source.test.0.open"])
        if kind == "no_proof":
            rt.state.tables["source_observation_current"]["source.test.0.open"].pop(
                "mqtt_device_proof", None
            )
        stamp = (
            NOW - timedelta(seconds=1)
            if kind == "older"
            else NOW + timedelta(seconds=1)
            if kind == "newer"
            else NOW
        )
        await report(
            rt,
            value=kind == "other_value",
            stamp=stamp,
            retained=True,
            raw={"contact": "invalid", "last_seen": stamp.isoformat()}
            if kind == "invalid"
            else None,
        )
        current = rt.state.tables["source_observation_current"]["source.test.0.open"]
        if kind == "older":
            assert current == before
            assert field(rt)["status"] == "valid"
        else:
            assert current["mqtt_retained"] is True
            assert not current.get("mqtt_device_proof")
            assert field(rt)["status"] != "valid"
    finally:
        await rt.stop()
