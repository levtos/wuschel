from core_contracts.contract_types import type_registry


def test_production_registry_has_no_test_templates():
    assert type_registry().types
    assert not any(key[0].startswith("test.") for key in type_registry().types)
    assert any(key[0].startswith("test.") for key in type_registry(include_test_types=True).types)


async def test_container_smoke_activates_portable_fixture_with_production_registry():
    import json
    from pathlib import Path

    from core_contracts.clock import SystemClock
    from core_contracts.persistence import MemoryStore
    from core_contracts.runtime import Runtime

    from .conftest import activate

    runtime = Runtime(MemoryStore(), SystemClock(), type_registry())
    await runtime.start()
    try:
        document = json.loads(
            (Path(__file__).parents[1] / "dev/fixtures/container-registry.json").read_text()
        )
        await activate(runtime, document)
        assert runtime.state.active_revision == 1
        assert runtime.config.contracts[0].type_id == "fixture.smoke"
        assert not any(key[0].startswith("test.") for key in runtime.types.types)
    finally:
        await runtime.stop()
