"""Synthetic acceptance: offline simulation by default; explicit read-only live observer.

Never installs, activates a registry, creates helpers, restarts HA or changes a DB.
See docs/platform-alpha1/live-acceptance.md for the separately approved live steps.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from core_contracts.clock import FakeClock, SystemClock
from core_contracts.evidence import Observation
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.authoring import activate_draft
from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import validate_document as validate

PREFIX = "test.alpha1."


def registry():
    sources, bindings = [], []
    for name, domain, kind in [
        ("a", "input_boolean", "bool"),
        ("b", "input_boolean", "bool"),
        ("number", "input_number", "number"),
    ]:
        sources.append(
            dict(
                source_id=f"source.cc_alpha_test_{name}",
                source_type="state",
                source_origin="local",
                freshness=dict(mode="event_stateful", future_tolerance_s=1),
                dependency_id="dep.alpha_ha",
                physical_source_key=f"cc-alpha-test-{name}",
            )
        )
        bindings.append(
            dict(
                binding_id=f"binding.cc_alpha_test_{name}",
                source_id=sources[-1]["source_id"],
                adapter=dict(kind="ha_state", ha_entity_id=f"{domain}.cc_alpha_test_{name}"),
                transform=dict(kind="type_cast", target=kind),
            )
        )

    def item(name, type_name, refs, params=None, version=1):
        return dict(
            contract_id=PREFIX + name,
            type_id="test." + type_name,
            type_version=version,
            enabled=True,
            display_name="TEST Alpha 1 " + name,
            inputs=[dict(name=key, ref=ref) for key, ref in zip("abcd", refs, strict=False)],
            parameters=params or {},
        )

    return dict(
        config_schema_version=1,
        dependencies=[dict(dependency_id="dep.alpha_ha", kind="ha", monitor="internal")],
        sources=sources,
        bindings=bindings,
        catalogs=[],
        contracts=[
            item("echo", "echo", [sources[0]["source_id"]]),
            item(
                "grace",
                "temporal",
                [sources[0]["source_id"]],
                dict(operation="grace", duration_s=10, accepts_held=False),
            ),
            item("not", "boolean", [PREFIX + "grace"], dict(operator="not")),
            item(
                "fusion",
                "fusion",
                [PREFIX + "not", sources[1]["source_id"]],
                dict(strategy="first_healthy"),
            ),
            item(
                "stable",
                "temporal",
                [PREFIX + "fusion"],
                dict(operation="stable_for", duration_s=2, accepts_held=True),
            ),
            item("multi", "multi", [sources[2]["source_id"], PREFIX + "stable"]),
            item(
                "machine",
                "state_machine",
                [PREFIX + "stable"],
                dict(deadline_s=30, sessions=False),
                version=2,
            ),
        ],
    )


async def simulate():
    document = registry()
    validate(document, type_registry())
    clock = FakeClock(datetime(2026, 1, 1, 12, tzinfo=UTC))
    runtime = Runtime(MemoryStore(), clock, type_registry())
    await runtime.start()
    try:
        draft = await runtime.submit("draft_create", dict(config=document))
        await activate_draft(runtime, draft, expected_active_revision=0)

        async def send(name, value, availability="available"):
            await runtime.submit(
                "observation",
                Observation(
                    source_id=f"source.cc_alpha_test_{name}",
                    binding_id=f"binding.cc_alpha_test_{name}",
                    adapter="ha_state",
                    value_raw=value,
                    ha_time_fired=clock.now_utc(),
                    received_at=clock.now_utc(),
                    observation_kind="live_change",
                    epoch_id=runtime.state.epoch_id,
                    ingest_seq=runtime.state.publication_seq,
                    availability=availability,
                ),
            )

        def value(name):
            return runtime.snapshot()["contracts"][PREFIX + name]["fields"]["value"]

        await send("a", False)
        await send("b", True)
        await send("number", 42)
        clock.advance(2)
        await runtime.submit("tick")
        assert value("stable")["value"] is True
        assert runtime.snapshot()["contracts"][PREFIX + "multi"]["contract_health"] == "healthy"
        await send("a", None, "unavailable")
        end = value("grace")["held_until"]
        assert all(value(name)["status"] == "held" for name in ("grace", "not", "fusion", "stable"))
        store = runtime.store
        await runtime.stop()
        runtime = Runtime(store, clock, type_registry())
        await runtime.start()
        assert value("grace")["held_until"] == end
        clock.advance(11)
        await runtime.submit("tick")
        assert value("grace")["status"] == "unknown"
        await send("b", True)
        assert value("fusion")["quality"] == "degraded"
        return dict(
            mode="isolated_memory",
            passed=True,
            contracts=sorted(runtime.snapshot()["contracts"]),
            publication_seq=runtime.state.publication_seq,
            checks=[
                "graph",
                "multifield",
                "held_chain",
                "restart",
                "original_grace_deadline",
                "partial_evidence",
            ],
            live_verified=False,
        )
    finally:
        await runtime.stop()


async def observe(args):
    # The standalone dev script uses the repository's public client package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
    from core_contracts_client import CoreContractsClient

    if not args.url or not args.installation_id:
        raise ValueError("explicit URL and expected installation ID required")
    token = os.environ.get("CORE_CONTRACTS_CONSUMER_TOKEN", "")
    clock = SystemClock()
    expected = {c["contract_id"] for c in registry()["contracts"]}
    async with CoreContractsClient(args.url, token, args.installation_id) as client:
        info = await client.info()
        snapshot = await client.snapshot()
        if set(snapshot.contracts) != expected:
            raise ValueError("active registry does not match the isolated acceptance plan")
        if not all(
            c["fixture"] and c["type"]["id"].startswith("test.")
            for c in snapshot.contracts.values()
        ):
            raise ValueError("non-synthetic contract detected")
        frames = []
        stream = client.subscribe(sorted(expected))
        try:
            async with asyncio.timeout(args.seconds):
                async for event in stream:
                    frames.append(dict(at=clock.now_utc().isoformat(), event=event))
        except TimeoutError:
            pass
        finally:
            await stream.aclose()
        return dict(
            mode="live_read_only",
            label=args.label,
            info=info,
            before=snapshot.model_dump(),
            after=(await client.snapshot()).model_dump(),
            frames=frames,
            live_verified=False,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--installation-id")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--label", default="manual-live-step")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds > 3600:
        parser.error("seconds must be between 0 and 3600")
    if args.export:
        args.export.write_text(json.dumps(registry(), indent=2) + "\n", encoding="utf-8")
        return
    result = asyncio.run(observe(args) if args.observe else simulate())
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
