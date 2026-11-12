from core_contracts.testing.contract_types import type_registry
from core_contracts.validation import validate_document as validate

from .conftest import activate
from .test_declarative import definition, observation, registry


async def test_nonfixture_definition_activates_and_publishes(runtime, config):
    data = definition()
    data.update(type_id="example.truth")
    data["schema"]["fixture"] = False
    doc = registry(config, data)
    validate(doc, type_registry())
    await activate(runtime, doc)
    await runtime.submit("observation", observation(runtime, True))
    result = runtime.state.tables["contract_state_current"][doc["contracts"][0]["contract_id"]]
    assert result["fixture"] is False
    assert result["fields"]["value"]["value"] is True
