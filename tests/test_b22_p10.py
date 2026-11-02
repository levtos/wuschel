"""P10 input display is canonical assessment and never a live cache mutation."""

import copy

from core_contracts.quality import FieldValue

from .conftest import activate
from .test_runtime import obs
from .test_workbench_api import api_client


async def test_input_view_is_opt_in_typed_and_read_only(runtime, config, aiohttp_client, tmp_path):
    await activate(runtime, config)
    await runtime.submit("bridge_state", ("connected", "connected"))
    await runtime.submit("observation", obs(runtime, False))
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    before = copy.deepcopy(runtime.state.model_dump())
    plain = await client.get("/api/v1/diagnostics/contracts/fixture.echo", headers=headers)
    assert plain.status == 200
    assert "inputs" not in await plain.json()
    response = await client.get(
        "/api/v1/diagnostics/contracts/fixture.echo?include_inputs=1", headers=headers
    )
    assert response.status == 200
    data = await response.json()
    assert data["registry_revision"] == runtime.state.active_revision
    assert data["input_epoch_id"] == runtime.state.epoch_id
    assert data["assessed_at"] == runtime.clock.now_utc().isoformat()
    assert data["inputs"]["a"]["value"] is False
    assert data["inputs"]["a"]["status"] == "valid"
    assert runtime.state.model_dump() == before


async def test_unknown_input_is_not_displayed_as_false(runtime, config, aiohttp_client, tmp_path):
    await activate(runtime, config)
    _, client, headers = await api_client(runtime, aiohttp_client, tmp_path)
    response = await client.get(
        "/api/v1/diagnostics/contracts/fixture.echo?include_inputs=1", headers=headers
    )
    assert response.status == 200
    value = (await response.json())["inputs"]["a"]
    assert value["value"] is None
    assert value["status"] == "unknown"
    assert value["reasons"]


async def test_input_inspection_cannot_modify_sensor_cache(runtime, config, monkeypatch):
    await activate(runtime, config)
    runtime.state.tables["service_state"]["sensor:synthetic"] = {"nested": {"count": 1}}
    before = copy.deepcopy(runtime.state.model_dump())

    def assess(contract, configuration, state):
        assert state is not runtime.state
        state.tables["service_state"]["sensor:synthetic"]["nested"]["count"] = 2
        return {"a": FieldValue(status="valid", value=True, quality="healthy")}

    monkeypatch.setattr(runtime, "_inputs", assess)
    result = runtime.inspect_inputs(runtime.config.contracts[0], runtime.config, runtime.state)
    assert result["a"].value is True
    assert runtime.state.model_dump() == before
