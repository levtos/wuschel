from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core_contracts import wiring  # noqa: F401
from core_contracts.clock import FakeClock
from core_contracts.persistence import MemoryStore
from core_contracts.runtime import Runtime
from core_contracts.testing.contract_types import type_registry


@pytest.fixture
def config():
    return {
        "config_schema_version": 1,
        "dependencies": [{"dependency_id": "dep.test", "kind": "ha", "monitor": "internal"}],
        "sources": [
            {
                "source_id": "source.a",
                "source_type": "boolean",
                "source_origin": "local",
                "freshness": {"mode": "event_stateful", "future_tolerance_s": 1},
                "dependency_id": "dep.test",
                "physical_source_key": "physical.a",
            }
        ],
        "bindings": [
            {
                "binding_id": "binding.a",
                "source_id": "source.a",
                "adapter": {"kind": "ha_state", "ha_entity_id": "sensor.example_1"},
                "transform": {"kind": "type_cast", "target": "bool"},
            }
        ],
        "contracts": [
            {
                "contract_id": "fixture.echo",
                "type_id": "test.echo",
                "type_version": 1,
                "enabled": True,
                "inputs": [{"name": "a", "ref": "source.a"}],
                "parameters": {},
            }
        ],
        "catalogs": [],
    }


@pytest.fixture
async def runtime():
    runtime = Runtime(
        MemoryStore(), FakeClock(datetime(2026, 1, 1, 12, tzinfo=UTC)), type_registry()
    )
    await runtime.start()
    yield runtime
    await runtime.stop()


async def activate(runtime, config):
    draft = await runtime.submit("draft_create", {"config": config})
    return await activate_draft(runtime, draft)


async def activate_draft(runtime, draft, **overrides):
    preview = await runtime.submit("draft_preview", {"draft_id": draft["draft_id"]})
    return await runtime.submit(
        "activate",
        {
            "draft_id": draft["draft_id"],
            "draft_version": draft["draft_version"],
            "request_id": str(uuid4()),
            "comparison_digest": preview["comparison_digest"],
            "confirmations": preview["required_confirmations"],
            "expected_active_revision": runtime.state.active_revision,
            **overrides,
        },
    )


async def activation_body(client, path, headers, version):
    preview = await (
        await client.post(path + "/activation-preview", headers=headers, json={})
    ).json()
    return {
        "request_id": str(uuid4()),
        "draft_version": version,
        "expected_active_revision": preview["expected_active_revision"],
        "comparison_digest": preview["comparison_digest"],
        "confirmations": preview["required_confirmations"],
    }
