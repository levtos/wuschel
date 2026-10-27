"""A1 probes against unmodified b14; synthetic data, MemoryStore, no live writes.

Run: uv run python docs/beta/bereich-5/probe_current.py
The synthetic definitions below are experiments, not the A2 media templates.
"""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from core_contracts.clock import FakeClock
from core_contracts.evidence import Observation
from core_contracts.freshness_templates import upgrade_document
from core_contracts.manual_source import ManualEvidence
from core_contracts.persistence import MemoryStore
from core_contracts.registry import fingerprints
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry


def schema(kind="bool", **extra):
    return dict(type=kind, required=True, physical_state=False, **extra)


def literal(value, kind="bool"):
    return dict(op="literal", value=value, schema=schema(kind))


def inp(name):
    return dict(op="input", name=name)


def both(*args):
    return dict(op="and", args=list(args))


def negate(arg):
    return dict(op="not", args=[arg])


def edge(name):
    return dict(
        op="fusion",
        strategy="first_usable",
        args=[
            dict(op="event_match", id="edge_" + name, arg=inp(name), mode="rising"),
            literal(False),
        ],
    )


def arbitration():
    states = ["unresolved", "idle", "tv", "pc"]
    tv, pc = inp("tv"), inp("pc")
    transitions = []
    for state in states:
        choices = [
            ("idle", both(negate(tv), negate(pc))),
            ("tv", both(tv, negate(pc))),
            ("pc", both(pc, negate(tv))),
            (
                "tv",
                both(
                    tv,
                    dict(
                        op="or",
                        args=[dict(op="ref", name="edge_" + n) for n in ("tv", "ps5", "appletv")],
                    ),
                ),
            ),
            ("pc", both(pc, dict(op="ref", name="edge_pc"))),
        ]
        transitions.extend(
            dict(source=state, target=target, event="input", guard=guard, reason="synthetic")
            for target, guard in choices
        )
    return dict(
        type_id="synthetic.arbitration",
        type_version=1,
        language_version=6,
        inputs=[dict(name=n, schema=schema()) for n in ("tv", "pc", "ps5", "appletv")],
        schema=dict(fields={"value": schema("enum", value_catalog=states)}),
        outputs={"value": dict(op="machine")},
        rules={"edge_" + n: edge(n) for n in ("tv", "pc", "ps5", "appletv")},
        machine=dict(
            id="selection",
            states=states,
            initial="unresolved",
            transition_selection="ordered",
            reinitialize_when=dict(op="or", args=[tv, pc]),
            transitions=transitions,
        ),
    )


def pc_definition(host_grace=False):
    bucket = dict(
        op="bucket",
        id="watts",
        arg=inp("watts"),
        classes=["off", "on"],
        initial={"unknown": True},
        schema=schema("enum", value_catalog=["off", "on"]),
        boundaries=[
            dict(
                at=literal(100, "number"),
                hysteresis=dict(lower=literal(10, "number"), upper=literal(100, "number")),
            )
        ],
    )
    power = dict(op="map", arg=bucket, entries={'"off"': False, '"on"': True}, schema=schema())
    host = (
        dict(op="temporal", id="host_grace", operation="grace", args=[inp("host")], duration_s=5)
        if host_grace
        else inp("host")
    )
    return dict(
        type_id="synthetic.pc",
        type_version=1,
        language_version=6,
        inputs=[dict(name="host", schema=schema()), dict(name="watts", schema=schema("number"))],
        schema=dict(fields={"value": schema(), "activation": schema()}),
        rules={"power": dict(op="fusion", strategy="first_usable", args=[host, power])},
        outputs={
            "value": dict(op="ref", name="power"),
            "activation": dict(
                op="event_match", id="fused_edge", mode="rising", arg=dict(op="ref", name="power")
            ),
        },
    )


def document(definition):
    sources, bindings = [], []
    for item in definition["inputs"]:
        name, field = item["name"], item["schema"]
        sources.append(
            dict(
                source_id="source." + name,
                source_type="numeric" if field["type"] == "number" else "boolean",
                source_origin="local",
                origin="manual",
                dependency_id="dep.manual",
                physical_source_key="synthetic." + name,
                value_schema=field,
                freshness=dict(mode="event_stateful", future_tolerance_s=0),
            )
        )
        bindings.append(
            dict(
                binding_id="binding." + name,
                source_id="source." + name,
                adapter=dict(kind="manual"),
            )
        )
    return upgrade_document(
        dict(
            config_schema_version=3,
            fingerprint_version=3,
            dependencies=[dict(dependency_id="dep.manual", kind="manual", monitor="internal")],
            definitions=[definition],
            sources=sources,
            bindings=bindings,
            catalogs=[],
            contracts=[
                dict(
                    contract_id="synthetic.probe",
                    type_id=definition["type_id"],
                    type_version=1,
                    enabled=True,
                    parameters={},
                    inputs=[
                        dict(name=i["name"], ref="source." + i["name"])
                        for i in definition["inputs"]
                    ],
                )
            ],
        )
    )


async def start(definition):
    runtime = Runtime(MemoryStore(), FakeClock(datetime(2032, 1, 1, tzinfo=UTC)), type_registry())
    await runtime.start()
    draft = await runtime.submit("draft_create", {"config": document(definition)})
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    await runtime.submit(
        "activate",
        dict(
            draft_id=draft["draft_id"],
            draft_version=draft["draft_version"],
            request_id=str(uuid4()),
            comparison_digest=preview["comparison_digest"],
            confirmations=preview["required_confirmations"],
            expected_active_revision=runtime.state.active_revision,
        ),
    )
    return runtime


async def observe(runtime, name, value, kind="live_change"):
    runtime.clock.advance(1)
    now = runtime.clock.now_utc()
    await runtime.submit(
        "observation",
        Observation(
            source_id="source." + name,
            binding_id="binding." + name,
            adapter="manual",
            manual=ManualEvidence(
                request_id=str(uuid4()),
                recorded_at=now,
                valid_from=now,
                source_fingerprint=fingerprints(runtime.config, runtime.types)["source." + name],
                actor={"kind": "synthetic"},
            ),
            value_raw=value,
            ha_time_fired=now,
            received_at=now,
            observation_kind=kind,
            epoch_id=runtime.state.epoch_id,
            ingest_seq=runtime.state.generation + 1,
            registry_revision=runtime.state.active_revision,
            reference_version=2,
        ),
    )


def fields(runtime):
    return runtime.state.tables["contract_state_current"]["synthetic.probe"]["fields"]


async def main():
    results = {}
    runtime = await start(arbitration())
    try:
        for name in ("tv", "pc", "ps5", "appletv"):
            await observe(runtime, name, False, "snapshot")
        sequence = []
        for name, value, expected in [
            ("tv", True, "tv"),
            ("pc", True, "pc"),
            ("ps5", True, "tv"),
            ("tv", False, "pc"),
            ("tv", True, "tv"),
            ("pc", False, "tv"),
            ("pc", True, "pc"),
            ("appletv", True, "tv"),
        ]:
            await observe(runtime, name, value)
            actual = fields(runtime)["value"]["value"]
            assert actual == expected, (name, value, actual, expected)
            sequence.append(actual)
        results["arbitration"] = sequence
        await observe(runtime, "pc", None)
        await observe(runtime, "pc", True)
        assert fields(runtime)["value"]["value"] == "tv"
        results["unknown_gap_no_activation"] = True
    finally:
        await runtime.stop()
    runtime = await start(arbitration())
    try:
        for name, value in [("tv", True), ("pc", True), ("ps5", False), ("appletv", False)]:
            await observe(runtime, name, value, "snapshot")
        assert fields(runtime)["value"]["value"] == "unresolved"
        results["both_active_at_start"] = "unresolved"
    finally:
        await runtime.stop()
    runtime = await start(pc_definition())
    try:
        await observe(runtime, "host", False)
        for watts in (15, 9):
            await observe(runtime, "watts", watts)
            assert fields(runtime)["value"]["value"] is False
        results["host_off_15W_9W"] = False
        await observe(runtime, "host", None)
        await observe(runtime, "watts", 10)
        assert fields(runtime)["value"]["value"] is False
        await observe(runtime, "watts", 100)
        assert fields(runtime)["value"]["value"] is True
        await observe(runtime, "watts", 10)
        assert fields(runtime)["value"]["value"] is True
        await observe(runtime, "watts", 9)
        assert fields(runtime)["value"]["value"] is False
        results["hysteresis_100_10_9"] = [True, True, False]
        # Counterexample: a fused level can change only because host evidence returns.
        await observe(runtime, "host", True)
        await observe(runtime, "host", None)
        await observe(runtime, "host", True)
        assert fields(runtime)["activation"]["value"] is True
        results["counterexample_fused_recovery_rising"] = True
    finally:
        await runtime.stop()
    runtime = await start(pc_definition())
    try:
        await observe(runtime, "watts", 15)
        assert fields(runtime)["value"]["status"] == "unknown"
        results["initial_band"] = "unknown"
    finally:
        await runtime.stop()
    runtime = await start(pc_definition(host_grace=True))
    try:
        await observe(runtime, "watts", 9)
        await observe(runtime, "host", True)
        await observe(runtime, "host", None)
        assert fields(runtime)["value"]["value"] is True
        assert fields(runtime)["value"]["status"] == "held"
        runtime.clock.advance(2)
        await observe(runtime, "host", True)
        assert fields(runtime)["activation"]["value"] is False
        results["host_grace_3s_no_activation"] = True
        await observe(runtime, "host", True)
        assert fields(runtime)["activation"]["value"] is False
        results["same_bool_new_manual_evidence_no_edge"] = True
    finally:
        await runtime.stop()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
